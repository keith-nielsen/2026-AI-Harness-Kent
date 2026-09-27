"""Shared helpers for Kent core tools (config, Loki, Prometheus, gateway, kent.db)."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def conf() -> dict[str, str]:
    path = Path(os.environ.get("KENT_CONF", Path.home() / ".config/kent/kent.conf"))
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
