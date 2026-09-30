"""Gent CEO state machine (templates/app/gent/main.py) with crew, router and
gateway stubbed: seeding, escalation round-trip through the inbox, capped
retries (the old CEO retried forever), transient errors, finalisation."""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def ceo(tmp_path, monkeypatch):
    data, inbox = tmp_path / "data", tmp_path / "inbox"
    (data / "project").mkdir(parents=True)
    inbox.mkdir()
    (data / "project" / "project.yaml").write_text("name: T\ngoal: test\n")
    (data / "project" / "agents.yaml").write_text("dev:\n  role: r\n  goal: g\n  backstory: b\n")
    (data / "project" / "tasks.yaml").write_text(
        "a:\n  agent: dev\n  description: do a\n  expected_output: a done\n"
        "b:\n  agent: dev\n  description: do b\n  escalate: true\n  question: how b?\n")
    con = sqlite3.connect(data / "stack.db")
    con.executescript((ROOT / "schemas" / "stack.sql").read_text())
    con.close()
    (tmp_path / "key").write_text("sk-test")
    monkeypatch.setenv("GENT_DATA", str(data))
    monkeypatch.setenv("GENT_INBOX", str(inbox))
    monkeypatch.setenv("GENT_KEY_FILE", str(tmp_path / "key"))
    monkeypatch.setenv("GENT_WORKSPACE", str(data / "workspace"))
    monkeypatch.setenv("POLL_INTERVAL", "0")

    state = {"crew_outputs": [], "verdicts": [], "router": (False, ""), "kickoffs": 0, "raise": None,
             "descriptions": [], "max_tokens": [], "agent_writes": {}}

    class FakeCrew:
        def kickoff(self):
            state["kickoffs"] += 1
            if state["raise"]:
                exc, state["raise"] = state["raise"], None
                raise exc
            for name, text in state["agent_writes"].items():   # the agent used Write File
                (data / "workspace" / name).write_text(text)
            return types.SimpleNamespace(raw=state["crew_outputs"].pop(0) if state["crew_outputs"] else "ok")

    crew_mod = types.ModuleType("gent.crew")
    def fake_build(*a, on_step=None, **k):
        state["on_step"] = on_step
        state["descriptions"].append(a[2])
        state["max_tokens"].append(k.get("max_tokens"))
        return FakeCrew()
    crew_mod.build_crew = fake_build
    router_mod = types.ModuleType("gent.router")
    router_mod.needs_escalation = lambda key, d: state["router"]
    monkeypatch.syspath_prepend(str(ROOT / "templates" / "app"))
    for m in ("gent.main", "gent.crew", "gent.router"):
        sys.modules.pop(m, None)
    import gent  # noqa: F401
    monkeypatch.setitem(sys.modules, "gent.crew", crew_mod)
    monkeypatch.setitem(sys.modules, "gent.router", router_mod)
    main = importlib.import_module("gent.main")

    def fake_chat(key, system, user, max_tokens=800):
        state.setdefault("chats", []).append((system, user))
        if "review task results" in system:
            return json.dumps(state["verdicts"].pop(0) if state["verdicts"] else {"passed": True})
        if "lessons" in system:
            return json.dumps([{"category": "technique", "summary": "L1", "detail": "d", "confidence": 0.8},
                               {"category": "escalation_request", "summary": "sneaky", "detail": "x"}])
        return "# Report"
    monkeypatch.setattr(main, "chat", fake_chat)
    main.WORKSPACE.mkdir(parents=True, exist_ok=True)
    return main, state, data, inbox


def tasks(data):
    con = sqlite3.connect(data / "stack.db"); con.row_factory = sqlite3.Row
    return {r["title"]: dict(r) for r in con.execute("SELECT * FROM kanban")}


def learnings(data):
    con = sqlite3.connect(data / "stack.db"); con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute("SELECT * FROM shared_learnings ORDER BY id")]


def step(main):
    c = main.db()
    main.resume_answered(c)
    t = main.claim(c)
    if t:
        main.run_task("sk-test", c, t)
    return t


def test_seed_is_ordered_and_idempotent(ceo):
    main, _, data, _ = ceo
    c = main.db(); main.seed(c); main.seed(c)
    t = tasks(data)
    assert set(t) == {"a", "b"} and t["a"]["priority"] > t["b"]["priority"]
    assert all(x["status"] == "backlog" for x in t.values())


def test_escalation_round_trip_through_inbox(ceo):
    main, state, data, inbox = ceo
    main.seed(main.db())
    step(main)                                   # a: done
    step(main)                                   # b: forced escalation
    t = tasks(data)
    assert t["a"]["status"] == "done" and t["b"]["status"] == "blocked"
    esc = [l for l in learnings(data) if l["category"] == "escalation_request"]
    assert len(esc) == 1 and esc[0]["detail"] == "how b?"
    assert step(main) is None                    # nothing claimable while blocked
    (inbox / f"escalation-{esc[0]['id']}.json").write_text(json.dumps({"answer": "do it like X", "tier": "frontier"}))
    step(main)                                   # resumes and completes with the answer
    t = tasks(data)
    assert t["b"]["status"] == "done"
    assert json.loads(t["b"]["description"])["expert_answer"] == "do it like X"


def test_router_escalation(ceo):
    main, state, data, _ = ceo
    state["router"] = (True, "is this licence compatible?")
    main.seed(main.db()); step(main)
    assert tasks(data)["a"]["status"] == "blocked"
    assert learnings(data)[0]["detail"] == "is this licence compatible?"


def test_retries_are_capped_then_escalated(ceo):
    main, state, data, _ = ceo
    state["verdicts"] = [{"passed": False, "critique": "bad"}, {"passed": False, "critique": "still bad"}]
    main.seed(main.db())
    step(main)
    assert tasks(data)["a"]["status"] == "backlog" and tasks(data)["a"]["retry_count"] == 1
    step(main)
    assert tasks(data)["a"]["status"] == "blocked"          # escalated, not retried forever
    assert state["kickoffs"] == 2
    assert "still bad" in learnings(data)[-1]["detail"]


def test_transient_error_retried_once_then_failed(ceo):
    main, state, data, _ = ceo
    main.seed(main.db())
    c = main.db()
    for expected in ("backlog", "failed"):
        state["raise"] = ConnectionError("gateway down")
        t = main.claim(c)
        try:
            main.run_task("sk-test", c, t)
        except ConnectionError as e:   # main() records it like this
            status = "backlog" if t["retry_count"] < main.MAX_RETRIES else "failed"
            c.execute("UPDATE kanban SET status=?, retry_count=retry_count+1, error_text=? WHERE task_id=?",
                      (status, str(e), t["task_id"])); c.commit()
        assert tasks(data)["a"]["status"] == expected


def test_finalize_writes_report_and_sanitises_learning_categories(ceo):
    main, state, data, _ = ceo
    c = main.db(); main.seed(c)
    main.finalize("sk-test", c)
    assert (main.WORKSPACE / "REPORT.md").read_text() == "# Report"
    assert (data / "STATE").read_text().strip() == "complete"
    cats = [l["category"] for l in learnings(data)]
    assert "escalation_request" not in cats          # a Gent cannot forge an escalation via learnings
    assert cats == ["technique", "technique"]


def test_validator_sees_files_written(ceo):
    main, _, _, _ = ceo
    import time
    t0 = time.time()
    (main.WORKSPACE / "tool.py").write_text("print('hi')")
    (main.WORKSPACE / "outputs").mkdir(exist_ok=True)
    (main.WORKSPACE / "outputs" / "x.md").write_text("ceo output")
    ev = main.workspace_evidence(t0)
    assert "tool.py" in ev and "print('hi')" in ev and "x.md" not in ev


def test_later_tasks_wait_behind_a_blocked_task(ceo):
    main, state, data, _ = ceo
    state["router"] = (True, "q?")               # a escalates
    main.seed(main.db())
    step(main)
    assert tasks(data)["a"]["status"] == "blocked"
    assert step(main) is None                    # b must not start before a is answered
    assert tasks(data)["b"]["status"] == "backlog"


def test_validator_excerpts_are_labelled_and_keep_the_end(ceo):
    main, _, _, _ = ceo
    import time
    t0 = time.time()
    body = "def a():\n    pass\n" * 2000 + "if __name__ == '__main__':\n    main()\n"
    (main.WORKSPACE / "big.py").write_text(body)
    ev = main.workspace_evidence(t0)
    assert "characters omitted" in ev and "complete file on disk" in ev
    assert "if __name__ == '__main__'" in ev      # the tail is visible, so it doesn't look truncated


def test_interrupted_task_is_requeued_at_startup(ceo):
    main, _, data, _ = ceo
    c = main.db(); main.seed(c)
    t = main.claim(c)                     # CEO dies here
    assert tasks(data)["a"]["status"] == "in_progress"
    main.recover(main.db())               # next start
    assert tasks(data)["a"]["status"] == "backlog"
    assert step(main)["task_id"] == t["task_id"]


def test_heartbeat_callback_updates_task(ceo):
    main, state, data, _ = ceo
    main.seed(main.db()); step(main)
    con = sqlite3.connect(data / "stack.db")
    con.execute("UPDATE kanban SET heartbeat_at='2000-01-01 00:00:00'"); con.commit()
    state["on_step"](None)
    assert con.execute("SELECT heartbeat_at FROM kanban WHERE title='a'").fetchone()[0] > "2020"


def test_resume_token_requeues_failed_tasks_once(ceo):
    main, _, data, inbox = ceo
    c = main.db(); main.seed(c)
    c.execute("UPDATE kanban SET status='failed', retry_count=2 WHERE title='a'"); c.commit()
    main.resume_requested(c)
    assert tasks(data)["a"]["status"] == "failed"          # no token, no change
    (inbox / "resume-1.json").write_text("{}")
    main.resume_requested(c)
    assert tasks(data)["a"]["status"] == "backlog" and tasks(data)["a"]["retry_count"] == 0
    c.execute("UPDATE kanban SET status='failed' WHERE title='a'"); c.commit()
    main.resume_requested(c)
    assert tasks(data)["a"]["status"] == "failed"          # same token is not reused


# --- Deliverables, retries and token limits (2026-09-30 research run: findings returned as
# text instead of written, the retry repeated the mistake, the answer was cut off) ---------------
FINDINGS = "# Findings\n" + "1. Reconnection drives convection. Source: https://doi.org/10.1103/PhysRevLett.6.47\n" * 5


def one_task(data, extra: str) -> None:
    (data / "project" / "tasks.yaml").write_text(
        "a:\n  agent: dev\n  description: write findings\n  expected_output: findings.md\n" + extra)


def test_final_answer_is_saved_as_the_named_deliverable(ceo):
    main, state, data, _ = ceo
    one_task(data, "  output: findings.md\n")
    state["crew_outputs"] = [FINDINGS]
    main.seed(main.db()); step(main)
    assert (main.WORKSPACE / "findings.md").read_text() == FINDINGS.strip() + "\n"
    assert "Save the deliverable as findings.md" in state["descriptions"][0]
    assert tasks(data)["a"]["status"] == "done"


def test_a_file_the_agent_wrote_is_not_overwritten(ceo):
    main, state, data, _ = ceo
    one_task(data, "  output: findings.md\n")
    state["agent_writes"] = {"findings.md": "agent's own file"}
    state["crew_outputs"] = [FINDINGS]
    main.seed(main.db()); step(main)
    assert (main.WORKSPACE / "findings.md").read_text() == "agent's own file"


def test_a_short_final_answer_is_not_taken_for_the_deliverable(ceo):
    main, state, data, _ = ceo
    one_task(data, "  output: findings.md\n")
    state["crew_outputs"] = ["findings.md written"]
    main.seed(main.db()); step(main)
    assert not (main.WORKSPACE / "findings.md").exists()


def test_a_fenced_answer_is_saved_without_the_fence(ceo):
    main, state, data, _ = ceo
    one_task(data, "  output: sub/paper.md\n")
    state["crew_outputs"] = ["```markdown\n" + FINDINGS + "```\n"]
    main.seed(main.db()); step(main)
    assert (main.WORKSPACE / "sub" / "paper.md").read_text().startswith("# Findings")


@pytest.mark.parametrize("name", ["../escape.md", "/etc/passwd", "outputs/t01-a.md", "."])
def test_output_must_stay_inside_the_workspace(ceo, name):
    main, _, _, _ = ceo
    with pytest.raises(ValueError):
        main.output_path({"output": name})
    assert main.output_path({"output": "/data/workspace/ok.md"}) == (main.WORKSPACE / "ok.md").resolve()


def test_retry_is_told_why_the_first_attempt_failed(ceo):
    main, state, data, _ = ceo
    state["verdicts"] = [{"passed": False, "critique": "no file was written"}, {"passed": True}]
    main.seed(main.db())
    step(main); step(main)
    assert "rejected by the reviewer" not in state["descriptions"][0]
    assert "rejected by the reviewer: no file was written" in state["descriptions"][1]
    assert tasks(data)["a"]["status"] == "done"


def test_task_max_tokens_reaches_the_crew(ceo):
    main, state, data, _ = ceo
    one_task(data, "  max_tokens: 6000\n")
    main.seed(main.db()); step(main)
    assert state["max_tokens"] == [6000]


def test_token_limits_default_and_cap(tmp_path, monkeypatch):
    # crew.py needs crewai (image only): stub it and capture what the LLM is given.
    fake = types.ModuleType("crewai")
    class LLM:
        def __init__(self, **k): self.k = k
    fake.LLM, fake.Agent, fake.Crew, fake.Task = LLM, object, object, object
    fake.Process = types.SimpleNamespace(sequential="sequential")
    tools = types.ModuleType("gent.tools"); tools.all_tools = lambda: []
    monkeypatch.setitem(sys.modules, "crewai", fake)
    monkeypatch.setitem(sys.modules, "gent.tools", tools)
    monkeypatch.syspath_prepend(str(ROOT / "templates" / "app"))
    monkeypatch.setenv("GENT_PROJECT", str(tmp_path))
    sys.modules.pop("gent.crew", None)
    crew = importlib.import_module("gent.crew")
    (tmp_path / "project.yaml").write_text("name: T\n")
    assert crew.llm("k").k["max_tokens"] == 4000                  # was 1500: cut off real answers
    assert crew.llm("k", max_tokens=6000).k["max_tokens"] == 6000
    assert crew.llm("k", max_tokens=10**6).k["max_tokens"] == crew.MAX_TOKENS_CAP
    (tmp_path / "project.yaml").write_text("name: T\nlimits: {max_tokens: 2500}\n")
    assert crew.llm("k").k["max_tokens"] == 2500
    sys.modules.pop("gent.crew", None)


def test_ceo_status_tracks_a_repeating_error(ceo):
    # Kent's stuck-Gent check reads /data/CEO_STATUS: one error repeated every pass must show
    # when it started and how often, and clear once a pass succeeds.
    main, _, data, _ = ceo
    s = main.write_status("PermissionError: /inbox/escalation-1.json", {})
    first = s["error_since"]
    s = main.write_status("PermissionError: /inbox/escalation-1.json", s)
    assert s["error_since"] == first and s["error_count"] == 2
    s = main.write_status("OSError: other", s)
    assert s["error_count"] == 1 and s["error_since"] >= first
    s = main.write_status(None, s)
    on_disk = json.loads((data / "CEO_STATUS").read_text())
    assert on_disk["error"] is None and on_disk["error_count"] == 0 and on_disk["ts"] >= first


def events(data):
    con = sqlite3.connect(data / "stack.db"); con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute("SELECT * FROM events ORDER BY id")]


def test_ceo_reports_to_kent_through_events(ceo):
    # Worker -> Gent is the task result; Gent -> Kent is stack.db events (read by Kent every minute).
    main, state, data, inbox = ceo
    state["agent_writes"] = {"a.md": "result a"}
    main.seed(main.db())
    step(main)                                   # a: done, wrote a.md
    step(main)                                   # b: forced escalation
    esc = [l for l in learnings(data) if l["category"] == "escalation_request"][0]
    (inbox / f"escalation-{esc['id']}.json").write_text(json.dumps({"answer": "x", "tier": "frontier"}))
    step(main)                                   # b: resumed and done
    main.finalize("sk-test", main.db())
    ev = events(data)
    assert [e["kind"] for e in ev] == ["task_done", "escalated", "resumed", "task_done", "project_complete"]
    assert json.loads(ev[0]["detail"])["files"] == ["a.md"] and ev[0]["task_id"] == "t01-a"
    assert json.loads(ev[3]["detail"])["escalated"] is True
    assert "a.md" in json.loads(ev[-1]["detail"])["files"]


def test_review_task_reports_only_its_overall_verdict(ceo):
    main, state, data, _ = ceo
    (data / "project" / "tasks.yaml").write_text(
        "review:\n  agent: dev\n  description: check\n  output: review.md\n  verdict: true\n")
    state["agent_writes"] = {"review.md": "a.md: PASS\nb.md: FAIL, overall PASS would be wrong\nOverall: FAIL\n"}
    main.seed(main.db()); step(main)
    main.finalize("sk-test", main.db())
    ev = events(data)
    assert json.loads(ev[0]["detail"])["verdict"] == "FAIL"           # the last "overall" line wins
    assert json.loads(ev[-1]["detail"])["review"] == "FAIL"
    assert main.review_verdict("no verdict here") is None


def test_workers_and_validator_know_todays_date_and_kents_answer(ceo):
    # 2026-09-30: without the date, the validator rejected a correct 2026 release date as
    # "in the future" and the task escalated until the breaker would trip.
    main, state, data, inbox = ceo
    main.seed(main.db())
    step(main)                                          # a: plain task
    today = main.today()
    assert f"Today's date (UTC): {today}." in state["descriptions"][0]
    system, user = [cu for cu in state["chats"] if "review task results" in cu[0]][0]
    assert f"Today's date (UTC) is {today}" in system and "EXPERT GUIDANCE" not in user
    step(main)                                          # b escalates
    esc = [l for l in learnings(data) if l["category"] == "escalation_request"][0]
    (inbox / f"escalation-{esc['id']}.json").write_text(json.dumps({"answer": "7.2.8 is correct", "tier": "frontier"}))
    step(main)                                          # b resumed: the validator sees Kent's answer
    system, user = [cu for cu in state["chats"] if "review task results" in cu[0]][-1]
    assert "EXPERT GUIDANCE FROM KENT" in user and "7.2.8 is correct" in user
