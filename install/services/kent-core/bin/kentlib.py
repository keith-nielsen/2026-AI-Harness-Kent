"""Shared helpers for Kent core tools (config, Loki, Prometheus, gateway, kent.db)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def conf() -> dict[str, str]:
    path = Path(os.environ.get("KENT_CONF", "/etc/kent/kent/kent.conf"))
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = os.path.expandvars(os.path.expanduser(v.strip().strip('"')))
    return out


def http_json(url: str, *, data: dict | None = None, headers: dict | None = None, timeout: int = 30):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def loki_query_range(c: dict, query: str, start_ns: int, end_ns: int | None = None, limit: int = 5000) -> list[tuple[int, dict, str]]:
    """Returns [(ts_ns, labels, line)] oldest first."""
    params = {"query": query, "start": str(start_ns), "end": str(end_ns or time.time_ns()),
              "limit": str(limit), "direction": "forward"}
    res = http_json(f"{c['LOKI_URL']}/loki/api/v1/query_range?" + urllib.parse.urlencode(params))
    out = []
    for stream in res["data"]["result"]:
        for ts, line in stream["values"]:
            out.append((int(ts), stream["stream"], line))
    return sorted(out, key=lambda x: x[0])


def loki_instant(c: dict, query: str) -> list[dict]:
    params = {"query": query, "time": str(time.time_ns())}
    return http_json(f"{c['LOKI_URL']}/loki/api/v1/query?" + urllib.parse.urlencode(params))["data"]["result"]


def prom_query(c: dict, query: str) -> list[dict]:
    return http_json(f"{c['PROM_URL']}/api/v1/query?" + urllib.parse.urlencode({"query": query}))["data"]["result"]


def gateway_chat(c: dict, model: str, system: str, user: str, *, max_tokens: int = 512, timeout: int = 300) -> str:
    key = Path(c["GATEWAY_KEY_FILE"]).read_text().strip()
    res = http_json(f"{c['GATEWAY_URL']}/chat/completions", timeout=timeout,
                    headers={"Authorization": f"Bearer {key}"},
                    data={"model": model, "max_tokens": max_tokens,
                          "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    return res["choices"][0]["message"]["content"] or ""


def kent_db(c: dict) -> sqlite3.Connection:
    db = sqlite3.connect(c["KENT_DB"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def stack_db_ro(c: dict, stack_id: str) -> sqlite3.Connection | None:
    """Open a Gent's stack.db strictly read-only (Kent never writes Gent data)."""
    p = Path(c["STACKS_DIR"]) / stack_id / "data" / "stack.db"
    if not p.exists() and c.get("GENT_ARCHIVE_DIR"):
        p = Path(c["GENT_ARCHIVE_DIR"]) / stack_id / "data" / "stack.db"   # archived Gent
    if not p.exists():
        return None
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def audit(entity: str, event: str, detail: str = "") -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("kent_audit", Path(__file__).with_name("kent_audit.py"))
    ka = importlib.util.module_from_spec(spec); spec.loader.exec_module(ka)
    c = conf()
    ka.append(Path(c["AUDIT_LOG"]), Path(c["AUDIT_SECRET"]).read_bytes().strip(), entity, event, detail)


def _gitea(c: dict, method: str, path: str, body: dict | None = None):
    token = Path(c["GITEA_TOKEN_FILE"]).read_text().strip()
    req = urllib.request.Request(f"{c['GITEA_URL']}/api/v1{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"token {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def gitea_get_file(c: dict, repo: str, path: str) -> tuple[bytes, str] | None:
    """Returns (content, blob sha) or None if the file does not exist."""
    import base64
    try:
        cur = _gitea(c, "GET", f"/repos/{repo}/contents/{urllib.parse.quote(path)}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return base64.b64decode(cur["content"]), cur["sha"]


def gitea_put_file(c: dict, repo: str, path: str, content: bytes, message: str) -> str:
    """Create or update one file on main (one commit). Returns the commit SHA."""
    import base64
    cur = gitea_get_file(c, repo, path)
    if cur is not None and cur[0] == content:
        return ""  # unchanged
    body = {"content": base64.b64encode(content).decode(), "branch": "main", "message": message}
    if cur is not None:
        body["sha"] = cur[1]
    res = _gitea(c, "PUT" if cur is not None else "POST", f"/repos/{repo}/contents/{urllib.parse.quote(path)}", body)
    return res["commit"]["sha"]


LEARNINGS_HEADER = "# Adopted learnings\n\nCurated by Kent from Gent projects. New Gents inherit these.\n"


def gitea_append_learning(c: dict, stack_id: str, learning_id: int, summary: str, detail: str, notes: str) -> str:
    """Append an adopted learning to LEARNINGS.md in the stack-template repo (one commit).
    Returns the new commit SHA."""
    cur = gitea_get_file(c, c["TEMPLATE_REPO"], "LEARNINGS.md")
    text = cur[0].decode() if cur else LEARNINGS_HEADER
    text += (f"\n## {summary.strip()[:120]}\n\n{detail.strip()[:3000]}\n\n"
             f"_Source: gent-{stack_id} learning #{learning_id}. Kent: {notes.strip()[:300]}_\n")
    return gitea_put_file(c, c["TEMPLATE_REPO"], "LEARNINGS.md", text.encode(),
                          f"learning: adopt gent-{stack_id}#{learning_id}\n\n{notes.strip()[:500]}")


def gitea_issue(c: dict, repo: str, title: str, body: str) -> int:
    """Open an issue (critical alerts, circuit breaker). Returns the issue number."""
    return _gitea(c, "POST", f"/repos/{repo}/issues", {"title": title[:200], "body": body[:20000]})["number"]


def gent_ctl(c: dict, action: str, stack_id: str, *extra: str):
    """Container operations on a Gent through the root-owned broker (Kent is not in the
    docker group). action: inspect | logs | stop | start."""
    import subprocess
    return subprocess.run(["sudo", "-n", f"{c.get('GENT_BIN', '/opt/kent-gent/bin')}/kent-gent-ctl",
                           action, stack_id, *extra], capture_output=True, text=True)


# --- Stuck Gents -------------------------------------------------------------------------
# A Gent can stop making progress without failing a task (2026-09-30: its supervisor could not
# read Kent's answer and logged "Permission denied" every 10 s for 15 minutes; nothing noticed).
ANSWER_PICKUP_S = 120      # the CEO polls its inbox every 10 s
CEO_ERROR_S = 300          # the same loop error for this long
CEO_SILENT_S = 600         # no loop pass while no task is running
HEARTBEAT_S = 1200         # a running task with no agent step (one local-model call can take 15 min)


def _utc(ts: str) -> float:
    return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def stuck_reasons(sdb: sqlite3.Connection, data: Path, inbox: Path, now: float | None = None) -> list[str]:
    """Why a Gent is not progressing, in plain language (empty when it is fine or finished).
    Everything quoted from the Gent (error text) is labelled untrusted."""
    now = time.time() if now is None else now
    if (data / "STATE").exists() or (data / "HALTED").exists():
        return []   # finished, or already halted by the circuit breaker
    out = []
    running = sdb.execute("SELECT task_id, heartbeat_at FROM kanban WHERE status='in_progress'").fetchall()
    for t in sdb.execute("SELECT task_id, error_text FROM kanban WHERE status='blocked'"):
        m = re.search(r"awaiting escalation #(\d+)", t["error_text"] or "")
        f = inbox / f"escalation-{m.group(1)}.json" if m else None
        if f is not None and f.exists() and now - f.stat().st_mtime > ANSWER_PICKUP_S:
            out.append(f"task {t['task_id']}: Kent's answer (escalation #{m.group(1)}) was delivered "
                       f"{int((now - f.stat().st_mtime) // 60)} min ago but not picked up")
    for t in running:
        if t["heartbeat_at"] and now - _utc(t["heartbeat_at"]) > HEARTBEAT_S:
            out.append(f"task {t['task_id']}: no agent step for {int((now - _utc(t['heartbeat_at'])) // 60)} min")
    st = data / "CEO_STATUS"
    try:
        s = json.loads(st.read_text())
    except (OSError, ValueError):
        s = None
    if s:
        if s.get("error") and now - float(s.get("error_since", now)) > CEO_ERROR_S:
            out.append(f"supervisor failing for {int((now - float(s['error_since'])) // 60)} min "
                       f"({s.get('error_count', '?')}x): untrusted-gent-text={json.dumps(str(s['error'])[:160])}")
        if not running and now - float(s.get("ts", now)) > CEO_SILENT_S:
            out.append(f"supervisor silent for {int((now - float(s['ts'])) // 60)} min with no task running "
                       "(container stopped or hung?)")
    return out


# --- Notices -------------------------------------------------------------------------------
# What Kent tells the operators (and his own chat) about Gents. Composed by Kent from structured
# fields only: Gent free text never reaches a notice, because notices are put in front of Kent's
# model at each chat turn (a Gent must not be able to write instructions into Kent's context).
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._/-]")


def safe_name(name: str, limit: int = 60) -> str:
    """A Gent-supplied file or task name, reduced to harmless characters."""
    return _SAFE_NAME.sub("_", str(name))[:limit]


def add_notice(kdb: sqlite3.Connection, stack_id: str | None, kind: str, text: str, limit: int = 600) -> int:
    cur = kdb.execute("INSERT INTO notices (created_at, stack_id, kind, text) VALUES (datetime('now'),?,?,?)",
                      (stack_id, kind, text[:limit]))
    kdb.commit()
    return cur.lastrowid


def unread_notices(kdb: sqlite3.Connection, reader: str, mark: bool = True, limit: int = 20) -> list[sqlite3.Row]:
    """Notices newer than what this reader has seen (oldest first); marks them read."""
    row = kdb.execute("SELECT last_id FROM notice_reads WHERE reader=?", (reader,)).fetchone()
    last = row[0] if row else 0
    rows = kdb.execute("SELECT * FROM notices WHERE id > ? ORDER BY id LIMIT ?", (last, limit)).fetchall()
    if mark and rows:
        kdb.execute("INSERT INTO notice_reads (reader, last_id) VALUES (?, ?) "
                    "ON CONFLICT(reader) DO UPDATE SET last_id = excluded.last_id", (reader, rows[-1]["id"]))
        kdb.commit()
    return rows
