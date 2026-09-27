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

    state = {"crew_outputs": [], "verdicts": [], "router": (False, ""), "kickoffs": 0, "raise": None}

    class FakeCrew:
        def kickoff(self):
            state["kickoffs"] += 1
            if state["raise"]:
                exc, state["raise"] = state["raise"], None
                raise exc
            return types.SimpleNamespace(raw=state["crew_outputs"].pop(0) if state["crew_outputs"] else "ok")

    crew_mod = types.ModuleType("gent.crew")
    def fake_build(*a, on_step=None, **k):
        state["on_step"] = on_step
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
