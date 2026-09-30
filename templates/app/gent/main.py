"""Gent CEO — runs one project inside an isolated container.

Lifecycle:
  1. Seed the kanban from /data/project/tasks.yaml on first start.
  2. Loop: claim the next backlog task; decide whether it needs escalation (tasks.yaml
     `escalate: true` or the router's judgement); if so, publish an escalation_request
     for Kent and mark the task blocked until Kent's answer arrives in /inbox.
  3. Run a one-task crew on the local "fast" tier; validate the result; retry once
     (told why the first attempt was rejected), then escalate. Final answers go to
     /data/workspace/outputs/<task>.md; a task's named deliverable (tasks.yaml
     `output:`) is saved from the final answer when the agent did not write it.
  4. When every task is done: write REPORT.md, publish transferable learnings to
     shared_learnings for Kent to review, and mark the project complete.
  5. Throughout: record events (task done/failed, escalated, resumed, project complete,
     halted) in stack.db `events`, which Kent reads every minute and reports to the operators.

Everything the CEO does is visible in stack.db (kanban, shared_learnings) and in
the container log. The CEO never talks to Kent directly: requests go out through
shared_learnings (read by Kent), answers come back through the read-only /inbox.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from gent.crew import build_crew
from gent.router import needs_escalation

STACK_ID = os.environ.get("STACK_ID", "unknown")
DATA = Path(os.environ.get("GENT_DATA", "/data"))
DB_PATH = DATA / "stack.db"
PROJECT = DATA / "project"
WORKSPACE = DATA / "workspace"
INBOX = Path(os.environ.get("GENT_INBOX", "/inbox"))
KEY_PATH = Path(os.environ.get("GENT_KEY_FILE", "/run/kent/gent_key"))
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://172.30.0.1:4000/v1")
POLL = int(os.environ.get("POLL_INTERVAL", "10"))
MAX_RETRIES = 1


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} [gent-{STACK_ID}] {msg}", flush=True)


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def chat(key: str, system: str, user: str, max_tokens: int = 800) -> str:
    r = requests.post(f"{GATEWAY_URL}/chat/completions", timeout=900, headers={"Authorization": f"Bearer {key}"},
                      json={"model": "fast", "max_tokens": max_tokens,
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def json_from(text: str, default):
    m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
    try:
        return json.loads(m.group(1)) if m else default
    except ValueError:
        return default


# --- Kanban ------------------------------------------------------------------------
def seed(c: sqlite3.Connection) -> None:
    if c.execute("SELECT COUNT(*) FROM kanban").fetchone()[0]:
        return
    tasks = yaml.safe_load((PROJECT / "tasks.yaml").read_text()) or {}
    n = len(tasks)
    for i, (key, t) in enumerate(tasks.items()):
        c.execute("INSERT INTO kanban (task_id, title, description, status, priority, assigned_to, created_at) "
                  "VALUES (?,?,?,?,?,?,datetime('now'))",
                  (f"t{i + 1:02d}-{key}", key, json.dumps(t), "backlog", n - i, t.get("agent", "")))
    c.commit()
    log(f"seeded kanban with {n} task(s)")


def recover(c: sqlite3.Connection) -> None:
    """One CEO per stack: a task still in_progress at startup was interrupted
    (crash, OOM kill, reboot, container recreate) and would otherwise never run."""
    n = c.execute("UPDATE kanban SET status='backlog', error_text='interrupted; requeued at restart' "
                  "WHERE status='in_progress'").rowcount
    c.commit()
    if n:
        log(f"requeued {n} interrupted task(s)")


def resume_requested(c: sqlite3.Connection) -> None:
    """Kent's `kent-gent resume` drops /inbox/resume-<n>.json after the operator fixed
    the cause of a circuit break: failed tasks get one fresh attempt per token."""
    seen_file = DATA / ".resume_seen"
    seen = set(seen_file.read_text().split()) if seen_file.exists() else set()
    for tok in sorted(INBOX.glob("resume-*.json")):
        if tok.name in seen:
            continue
        n = c.execute("UPDATE kanban SET status='backlog', retry_count=0, "
                      "error_text='requeued by operator resume' WHERE status='failed'").rowcount
        c.commit()
        seen.add(tok.name)
        seen_file.write_text("\n".join(sorted(seen)) + "\n")
        log(f"resume {tok.name}: requeued {n} failed task(s)")


def claim(c: sqlite3.Connection):
    row = c.execute("UPDATE kanban SET status='in_progress', started_at=datetime('now'), heartbeat_at=datetime('now') "
                    "WHERE task_id = (SELECT task_id FROM kanban WHERE status='backlog' "
                    # tasks.yaml order is a dependency order: nothing starts while an
                    # earlier task is blocked on an escalation.
                    "AND priority > (SELECT COALESCE(MAX(priority), -1) FROM kanban WHERE status='blocked') "
                    "ORDER BY priority DESC, created_at LIMIT 1) RETURNING *").fetchone()
    c.commit()
    return row


EVENTS_DDL = ("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
              "kind TEXT NOT NULL, task_id TEXT, summary TEXT NOT NULL, detail TEXT)")


def emit(c, kind: str, task_id: str | None, summary: str, **detail) -> None:
    """Report to Kent (stack.db events; Kent reads new rows every minute)."""
    c.execute("INSERT INTO events (ts, kind, task_id, summary, detail) VALUES (datetime('now'),?,?,?,?)",
              (kind, task_id, summary[:500], json.dumps(detail)[:4000]))
    c.commit()


VERDICT_LINE = re.compile(r"overall\W{0,20}(PASS|FAIL)\b", re.I)


def review_verdict(text: str) -> str | None:
    """A review task's overall verdict (tasks.yaml `verdict: true`): the last "overall PASS|FAIL".
    Only this one word goes to Kent, never the review's text."""
    found = VERDICT_LINE.findall(text or "")
    return found[-1].upper() if found else None


def files_since(since: float) -> list[str]:
    """Workspace files written since a time (not the CEO's own outputs/), for task reports."""
    return sorted(str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob("*")
                  if p.is_file() and "outputs" not in p.relative_to(WORKSPACE).parts and p.stat().st_mtime >= since - 1)


def set_status(c, task_id, status, **fields):
    sets = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE kanban SET status=?{', ' + sets if sets else ''}, heartbeat_at=datetime('now') WHERE task_id=?",
              (status, *fields.values(), task_id))
    c.commit()


# --- Escalation (Gent -> Kent -> frontier -> /inbox) -----------------------------
def escalate(c, task, question: str) -> None:
    cur = c.execute("INSERT INTO shared_learnings (timestamp, category, summary, detail, confidence, applied_locally) "
                    "VALUES (datetime('now'), 'escalation_request', ?, ?, 0.0, 0)",
                    (f"ESCALATION for {task['task_id']}", question[:6000]))
    c.commit()
    set_status(c, task["task_id"], "blocked", error_text=f"awaiting escalation #{cur.lastrowid}")
    log(f"task {task['task_id']} escalated to Kent (learning #{cur.lastrowid})")
    emit(c, "escalated", task["task_id"], f"escalated to Kent (#{cur.lastrowid})", learning=cur.lastrowid)


def resume_answered(c) -> None:
    for t in c.execute("SELECT * FROM kanban WHERE status='blocked'").fetchall():
        m = re.search(r"#(\d+)", t["error_text"] or "")
        answer_file = INBOX / f"escalation-{m.group(1)}.json" if m else None
        if answer_file and answer_file.exists():
            ans = json.loads(answer_file.read_text())
            spec = json.loads(t["description"])
            spec["expert_answer"] = ans.get("answer", "")
            spec["escalate"] = False
            c.execute("UPDATE kanban SET status='backlog', description=?, error_text=NULL WHERE task_id=?",
                      (json.dumps(spec), t["task_id"]))
            c.commit()
            log(f"task {t['task_id']} resumed with Kent's answer (via {ans.get('tier')})")
            emit(c, "resumed", t["task_id"], f"resumed with Kent's answer (via {ans.get('tier')})")


# --- Execution ----------------------------------------------------------------------
MIN_ANSWER_CHARS = 200   # a shorter final answer ("file written") is not the deliverable itself


def today() -> str:
    """Today's date (UTC). The local model's training ends before it, so without this it takes
    real recent dates for inventions (2026-09-30: the validator rejected a correct kernel
    release date as "in the future" three times)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def output_path(spec: dict) -> Path | None:
    """The task's named deliverable (tasks.yaml `output: paper.md`), inside the workspace only."""
    name = str(spec.get("output") or "").strip()
    if not name:
        return None
    p = (WORKSPACE / name.removeprefix("/data/workspace/")).resolve()
    ws = WORKSPACE.resolve()
    if p == ws or ws not in p.parents or "outputs" in p.relative_to(ws).parts:
        raise ValueError(f"task output {name!r} must be a file inside /data/workspace (not outputs/)")
    return p


def strip_fence(text: str) -> str:
    """A whole answer wrapped in one ```markdown fence is the document itself."""
    m = re.fullmatch(r"\s*```[\w-]*\n(.*?)\n```\s*", text, re.S)
    return m.group(1) if m else text.strip() + "\n"


def save_deliverable(target: Path, output: str, started: float) -> bool:
    """Small local models often put the deliverable in their final answer instead of calling
    Write File (2026-09-30: research findings returned as text, task failed as "no file").
    Save the answer as the named file unless the agent wrote that file during this task."""
    if target.exists() and target.stat().st_mtime >= started - 1:
        return False
    if len(output.strip()) < MIN_ANSWER_CHARS:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(strip_fence(output))
    return True


def previous_outputs(c) -> str:
    rows = c.execute("SELECT title, output_summary FROM kanban WHERE status='done' ORDER BY completed_at").fetchall()
    return "\n\n".join(f"### {r['title']}\n{(r['output_summary'] or '')[:1500]}" for r in rows)


def workspace_evidence(since: float, limit: int = 12000) -> str:
    """Files written during this task (excluding the CEO's own outputs/), for the validator.
    Excerpts are labelled as such: a sim run showed a validator failing complete code
    because it only saw the first part of the file."""
    changed = [p for p in sorted(WORKSPACE.rglob("*"), key=lambda p: -p.stat().st_mtime)
               if p.is_file() and "outputs" not in p.relative_to(WORKSPACE).parts and p.stat().st_mtime >= since - 1]
    if not changed:
        return "FILES WRITTEN DURING THIS TASK: none"
    out = ["FILES WRITTEN DURING THIS TASK (newest first; long files are shown as head + tail excerpts, "
           "which is NOT a sign the file itself is incomplete):"]
    budget = max(1500, (limit - 300) // len(changed))
    for p in changed:
        text = p.read_text(errors="replace")
        if len(text) > budget:
            half = budget // 2
            text = f"{text[:half]}\n[... {len(text) - budget} characters omitted from this excerpt ...]\n{text[-half:]}"
        out.append(f"--- {p.relative_to(WORKSPACE)} ({p.stat().st_size} bytes, complete file on disk)\n{text}")
    return "\n".join(out)[:limit]


def run_task(key: str, c, task) -> bool:
    spec = json.loads(task["description"])
    project = yaml.safe_load((PROJECT / "project.yaml").read_text()) or {}
    if "expert_answer" not in spec:
        forced = bool(spec.get("escalate"))
        want, question = (True, spec.get("question") or spec["description"]) if forced else needs_escalation(key, spec["description"])
        if want:
            escalate(c, task, question or spec["description"])
            return True
    target = output_path(spec)
    description = (f"Project: {project.get('name')} — {project.get('goal')}\n\n"
                   f"Today's date (UTC): {today()}.\n\n"
                   f"Your task: {spec['description']}\n\n"
                   f"Work only inside /data/workspace. Save deliverables with the Write File tool.\n")
    if target is not None:
        description += (f"Save the deliverable as {target.relative_to(WORKSPACE.resolve())} with the Write File tool; "
                        "if you cannot, give the complete deliverable as your final answer.\n")
    critique = (task["error_text"] or "").removeprefix("validation failed:").strip() \
        if (task["error_text"] or "").startswith("validation failed:") else ""
    if critique:
        # Without this the retry got the same instructions and repeated the same mistake.
        description += f"\nYour previous attempt was rejected by the reviewer: {critique}\nFix exactly that.\n"
    if spec.get("expert_answer"):
        description += f"\nExpert guidance from Kent (follow it):\n{spec['expert_answer'][:6000]}\n"
    prior = previous_outputs(c)
    if prior:
        description += f"\nResults of earlier tasks:\n{prior[:6000]}\n"
    def heartbeat(_step=None):
        # Every agent step proves liveness (architecture §17.3); Kent flags stale tasks.
        with sqlite3.connect(DB_PATH, timeout=30) as hb:
            hb.execute("UPDATE kanban SET heartbeat_at=datetime('now') WHERE task_id=?", (task["task_id"],))

    crew = build_crew(key, spec.get("agent", ""), description, spec.get("expected_output", ""), on_step=heartbeat,
                      max_tokens=spec.get("max_tokens"))
    started = time.time()
    result = crew.kickoff()
    output = getattr(result, "raw", str(result))
    (WORKSPACE / "outputs").mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "outputs" / f"{task['task_id']}.md").write_text(output)
    if target is not None and save_deliverable(target, output, started):
        log(f"task {task['task_id']}: saved the final answer as {target.relative_to(WORKSPACE.resolve())} "
            "(the agent did not write it)")
    guidance = (f"\n\nEXPERT GUIDANCE FROM KENT (trusted; the team was told to follow it):\n"
                f"{spec['expert_answer'][:2000]}") if spec.get("expert_answer") else ""
    verdict = json_from(chat(key, "You review task results. Judge the FILES the task produced as well as the "
                                  f"agent's final answer. Today's date (UTC) is {today()}: dates up to today are "
                                  "not in the future. Reply JSON only: "
                                  '{"passed": true|false, "critique": "<one sentence>"}',
                             f"TASK:\n{spec['description'][:3000]}\n\nEXPECTED:\n{spec.get('expected_output', '')[:1000]}"
                             f"{guidance}"
                             f"\n\nFINAL ANSWER:\n{output[:4000]}\n\n{workspace_evidence(started)}", 200), {})
    if verdict.get("passed", False):
        set_status(c, task["task_id"], "done", completed_at=datetime.now(timezone.utc).isoformat(), output_summary=output[:4000])
        log(f"task {task['task_id']} done")
        extra = {}
        if spec.get("verdict"):
            src = target.read_text(errors="replace") if target is not None and target.exists() else output
            extra["verdict"] = review_verdict(src)
        emit(c, "task_done", task["task_id"], f"{task['task_id']} done", files=files_since(started),
             retries=task["retry_count"], escalated="expert_answer" in spec, **extra)
        return True
    retries = task["retry_count"] + 1
    if retries > MAX_RETRIES:
        escalate(c, task, f"The team could not complete this task to standard.\nTask: {spec['description']}\n"
                          f"Last result:\n{output[:3000]}\nReviewer critique: {verdict.get('critique')}\n"
                          "What should the team do differently?")
    else:
        c.execute("UPDATE kanban SET status='backlog', retry_count=?, error_text=? WHERE task_id=?",
                  (retries, f"validation failed: {verdict.get('critique', '')}"[:1000], task["task_id"]))
        c.commit()
        log(f"task {task['task_id']} failed validation; retry {retries}/{MAX_RETRIES}")
    return False


def write_status(err: str | None, prev: dict) -> dict:
    """/data/CEO_STATUS: a heartbeat of the supervisor loop and its last error, read by Kent's
    stuck-Gent check (an error repeated every pass is invisible otherwise: only the log shows it)."""
    now = time.time()
    s = {"ts": now, "error": None, "error_since": None, "error_count": 0}
    if err:
        same = prev.get("error") == err
        s.update(error=err[:500], error_since=prev["error_since"] if same else now,
                 error_count=prev.get("error_count", 0) + 1 if same else 1)
    tmp = DATA / ".CEO_STATUS.tmp"
    tmp.write_text(json.dumps(s))
    tmp.replace(DATA / "CEO_STATUS")
    return s


def finalize(key: str, c) -> None:
    project = yaml.safe_load((PROJECT / "project.yaml").read_text()) or {}
    done = c.execute("SELECT title, output_summary FROM kanban ORDER BY priority DESC").fetchall()
    files = "\n".join(f"- {p.relative_to(WORKSPACE)}" for p in sorted(WORKSPACE.rglob("*")) if p.is_file())
    report = chat(key, "Write a concise project report in Markdown: goal, what was delivered (with file names), "
                       "how to use it, limitations.",
                  f"Project: {project}\n\nTask results:\n" +
                  "\n\n".join(f"## {r['title']}\n{(r['output_summary'] or '')[:2000]}" for r in done) +
                  f"\n\nFiles:\n{files}", 700)
    (WORKSPACE / "REPORT.md").write_text(report)
    learnings = json_from(chat(key, "Extract 1-3 lessons from this project that would help FUTURE, UNRELATED "
                                    "projects (reusable technique, pitfall to avoid). Reply JSON list only: "
                                    '[{"category":"technique|pitfall|pattern","summary":"...","detail":"...","confidence":0.0-1.0}]',
                               report[:6000], 500), [])
    for l in learnings if isinstance(learnings, list) else []:
        if isinstance(l, dict) and l.get("summary"):
            c.execute("INSERT INTO shared_learnings (timestamp, category, summary, detail, confidence, applied_locally) "
                      "VALUES (datetime('now'), ?, ?, ?, ?, 1)",
                      (l.get("category") if l.get("category") in ("technique", "pitfall", "pattern") else "technique", str(l["summary"])[:500], str(l.get("detail", ""))[:4000],
                       float(l.get("confidence", 0.5) or 0.5)))
    c.commit()
    (DATA / "STATE").write_text("complete\n")
    review = None
    for (d,) in c.execute("SELECT detail FROM events WHERE kind='task_done' ORDER BY id DESC"):
        v = json.loads(d or "{}").get("verdict")
        if v in ("PASS", "FAIL"):
            review = v
            break
    emit(c, "project_complete", None, "project complete", review=review,
         files=sorted(str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob("*")
                      if p.is_file() and "outputs" not in p.relative_to(WORKSPACE).parts),
         learnings=len(learnings) if isinstance(learnings, list) else 0)
    log(f"project complete: REPORT.md written, {len(learnings) if isinstance(learnings, list) else 0} learning(s) published")


def main() -> int:
    os.umask(0o027)   # files stay readable by Kent (setgid group on /data), never by others
    key = KEY_PATH.read_text().strip()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    c = db()
    c.execute(EVENTS_DDL)   # stacks spawned before the events table existed
    seed(c)
    recover(c)
    log("CEO running")
    status: dict = {}
    err = None   # how the previous pass ended, recorded at the top of the next one
    while True:
        try:
            status = write_status(err, status)
        except OSError as e:
            log(f"cannot write CEO_STATUS: {e}")
        task = None
        err = None
        try:
            resume_requested(c)
            resume_answered(c)
            task = claim(c)
            if task is None:
                open_ = c.execute("SELECT COUNT(*) FROM kanban WHERE status IN ('backlog','in_progress','blocked')").fetchone()[0]
                failed = c.execute("SELECT COUNT(*) FROM kanban WHERE status='failed'").fetchone()[0]
                if open_ == 0 and failed:
                    # Circuit-breaker territory: don't finalise over failed work; Kent halts
                    # the container and the operator resumes after fixing the cause.
                    if not (DATA / "HALTED").exists():
                        (DATA / "HALTED").write_text(f"{failed} failed task(s)\n")
                        log(f"halted: {failed} failed task(s); waiting for operator resume")
                        emit(c, "halted", None, f"halted: {failed} failed task(s)", failed=failed)
                    time.sleep(POLL)
                    continue
                (DATA / "HALTED").unlink(missing_ok=True)
                if open_ == 0:
                    if not (DATA / "STATE").exists():
                        finalize(key, c)
                    log("nothing left to do; exiting")
                    return 0
                time.sleep(POLL)
                continue
            log(f"claimed {task['task_id']}")
            run_task(key, c, task)
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # noqa: BLE001 - keep the CEO alive; record the failure on the task
            err = f"{type(e).__name__}: {e}"
            log(f"error: {e}")
            traceback.print_exc()
            if task is not None:
                # Transient errors (gateway restart, proxy hiccup) get the same capped retry.
                status = "backlog" if task["retry_count"] < MAX_RETRIES else "failed"
                c.execute("UPDATE kanban SET status=?, retry_count=retry_count+1, error_text=? WHERE task_id=?",
                          (status, f"error: {e}"[:1000], task["task_id"]))
                c.commit()
                if status == "failed":
                    emit(c, "task_failed", task["task_id"], f"{task['task_id']} failed", error=str(e)[:500])
            time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
