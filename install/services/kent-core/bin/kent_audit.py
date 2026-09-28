#!/usr/bin/python3
"""Kent — HMAC-chained, append-only audit log.

  kent-audit append <entity> <event> [detail]   add one chained entry
  kent-audit verify                             validate the chain against the latest anchor
  kent-audit anchor                             verify, then record count + last HMAC in the
                                                system journal (tag kent-audit-anchor)

Anchors defeat truncate-then-append: the journal is root-owned, so an attacker
with the operator's account cannot remove a recorded anchor.

Entry format (one line):  <utc-iso>|<entity>|<event>|<detail>|<hmac>
hmac = HMAC-SHA256(secret, prev_hmac + "|" + <first four fields>), prev of the first
entry = "GENESIS". Any edit, deletion, reordering or truncation-then-append breaks
the chain from that point. Fields are sanitised ("|" and newlines replaced).
Paths come from ~/.config/kent/kent.conf (AUDIT_LOG, AUDIT_SECRET).
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import sys
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


def clean(s: str, limit: int = 2000) -> str:
    return s.replace("|", "¦").replace("\r", " ").replace("\n", " ")[:limit]


def mac(secret: bytes, prev: str, payload: str) -> str:
    return hmac.new(secret, f"{prev}|{payload}".encode(), hashlib.sha256).hexdigest()


def append(log: Path, secret: bytes, entity: str, event: str, detail: str = "") -> str:
    log.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        last = ""
        for line in f:
            if line.strip():
                last = line
        prev = last.rstrip("\n").rsplit("|", 1)[1] if last else "GENESIS"
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        payload = "|".join([ts, clean(entity, 100), clean(event, 100), clean(detail)])
        line = f"{payload}|{mac(secret, prev, payload)}\n"
        f.write(line)
        f.flush(); os.fsync(f.fileno())
        return line


def verify(log: Path, secret: bytes) -> list[str]:
    problems, prev, n = [], "GENESIS", 0
    if not log.exists():
        return problems
    for n, line in enumerate(log.read_text().splitlines(), 1):
        if not line.strip():
            problems.append(f"line {n}: blank line"); continue
        parts = line.rsplit("|", 1)
        if len(parts) != 2 or line.count("|") != 4:
            problems.append(f"line {n}: malformed"); prev = parts[-1]; continue
        payload, stored = parts
        if not hmac.compare_digest(stored, mac(secret, prev, payload)):
            problems.append(f"line {n}: chain break")
        prev = stored
    return problems


ANCHOR_TAG = "kent-audit-anchor"


def chain_id(secret: bytes) -> str:
    """Identity of a chain (derived from its secret, reveals nothing about it). Anchors
    carry it, so a legitimately new chain (reinstall after --purge-state) is checked
    against its own anchors only, while the journal still shows that an earlier chain
    existed."""
    return hashlib.sha256(b"kent-audit-chain\0" + secret).hexdigest()[:16]


def parse_anchor(line: str) -> dict | None:
    try:
        f = dict(kv.split("=", 1) for kv in line.split())
        return {"chain": f.get("chain", "legacy"), "count": int(f["count"]), "last": f["last"]}
    except (ValueError, KeyError):
        return None          # alert lines etc. share the tag; they are not anchors


ANCHOR_SOURCE_OK = True   # set False when no anchor source could be read (reported by verify)


def journal_anchors() -> list[dict]:
    """Anchors, oldest first. Read from Loki (Alloy ships the journal there), because Kent's
    unprivileged account cannot read the system journal; journalctl is the fallback for
    privileged callers. An unreadable source is reported, never treated as "no anchors"."""
    global ANCHOR_SOURCE_OK
    import json, subprocess, time, urllib.parse, urllib.request
    lines: list[str] = []
    try:
        url = os.environ.get("KENT_LOKI_URL") or conf().get("LOKI_URL", "http://127.0.0.1:3100")
        q = urllib.parse.urlencode({"query": f'{{syslog_identifier="{ANCHOR_TAG}"}}', "limit": "5000",
                                    "start": str(time.time_ns() - 30 * 86400 * 10**9), "direction": "forward"})
        with urllib.request.urlopen(f"{url}/loki/api/v1/query_range?{q}", timeout=10) as r:
            vals = [v for s in json.load(r)["data"]["result"] for v in s["values"]]
        lines = [line for _, line in sorted(vals, key=lambda v: int(v[0]))]
    except Exception:  # noqa: BLE001 - fall back to the journal
        try:
            out = subprocess.run(["journalctl", "-t", ANCHOR_TAG, "-o", "cat", "--no-pager"],
                                 capture_output=True, text=True, timeout=30)
            lines = out.stdout.splitlines()
            ANCHOR_SOURCE_OK = out.returncode == 0
        except Exception:  # noqa: BLE001
            ANCHOR_SOURCE_OK = False
    return [a for a in map(parse_anchor, lines) if a]


def latest_anchor(cid: str | None = None, anchors: list[dict] | None = None) -> tuple[int, str] | None:
    """Most recent anchor of chain `cid` (never confused by alert lines or other chains)."""
    for a in reversed(anchors if anchors is not None else journal_anchors()):
        if cid is None or a["chain"] == cid:
            return a["count"], a["last"]
    return None


def check_anchor(log: Path, anchor: tuple[int, str] | None) -> list[str]:
    if anchor is None:
        return []
    count, last = anchor
    lines = log.read_text().splitlines() if log.exists() else []
    if len(lines) < count:
        return [f"truncated: {len(lines)} entries, anchor recorded {count}"]
    if count and lines[count - 1].rsplit("|", 1)[-1] != last:
        return [f"entry {count} no longer matches the anchored HMAC"]
    return []


def main(argv: list[str]) -> int:
    c = conf()
    log, secret = Path(c["AUDIT_LOG"]), Path(c["AUDIT_SECRET"]).read_bytes().strip()
    if len(argv) >= 3 and argv[0] == "append":
        append(log, secret, argv[1], argv[2], " ".join(argv[3:]))
        return 0
    if argv[:1] in (["verify"], ["anchor"]):
        cid = chain_id(secret)
        anchors = journal_anchors()
        problems = verify(log, secret) + check_anchor(log, latest_anchor(cid, anchors))
        n = len(log.read_text().splitlines()) if log.exists() else 0
        if not ANCHOR_SOURCE_OK:
            print("note: no anchor source readable (Loki and journal unavailable); truncation cannot be checked")
        others = sorted({a["chain"] for a in anchors} - {cid})
        if others:
            print(f"note: journal also holds anchors of {len(others)} earlier chain(s) "
                  f"({', '.join(others)}); this chain is {cid}")
        if problems:
            print(f"AUDIT CHAIN INVALID: {len(problems)} problem(s) in {n} entries")
            print("\n".join(problems[:50]))
            if argv[0] == "anchor":
                # Architecture §17.2: a chain break is critical -> Gitea issue for the human.
                # The scheduled anchor run raises it; ad-hoc verify only reports.
                import subprocess
                subprocess.run(["logger", "-p", "user.crit", "-t", ANCHOR_TAG, f"CHAIN INVALID problems={len(problems)}"])
                try:
                    sys.path.insert(0, str(Path(__file__).resolve().parent))
                    import kentlib
                    num = kentlib.gitea_issue(c, c["TEMPLATE_REPO"], "CRITICAL: Kent audit chain verification failed",
                                              f"{len(problems)} problem(s) in {n} entries:\n\n" +
                                              "\n".join(f"- {p}" for p in problems[:50]))
                    print(f"opened issue #{num} in {c['TEMPLATE_REPO']}")
                except Exception as e:  # noqa: BLE001 - journal (crit) already carries the alert
                    print(f"could not open Gitea issue: {e}", file=sys.stderr)
            return 1
        print(f"audit chain valid: {n} entries")
        if argv[0] == "anchor" and n:
            import subprocess
            last = log.read_text().splitlines()[-1].rsplit("|", 1)[1]
            subprocess.run(["logger", "-t", ANCHOR_TAG, f"chain={cid} count={n} last={last}"], check=True)
            print(f"anchored count={n}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
