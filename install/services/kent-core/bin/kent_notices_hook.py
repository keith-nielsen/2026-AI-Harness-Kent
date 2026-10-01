#!/usr/bin/env python3
"""kent-notices-hook — Hermes `pre_llm_call` shell hook for Kent: puts Kent's unread notices
(Gents finished, failed, halted, stuck) in front of the model at the start of a turn, so Kent
knows what happened while nobody was talking to him.

Contract (Hermes shell hooks): JSON payload on stdin (session_id, ...); stdout
{"context": "..."} is added to that turn's user message, or nothing. Read state is kept per
chat session (reader "kent-chat:<session>"), so a one-off `kent -q` or a background call does
not use up a notice the operator's chat never saw; a new session starts with the last 24 hours
(NOTICE_LOOKBACK_HOURS in kent.conf; 0 = only notices from after the session started, which
evaluation runs use so one test item's notices never reach the next).
Notices are written by Kent's relay from structured fields only (kentlib.add_notice), never
from Gent free text. Any error: print nothing and exit 0, so the hook can never break a chat.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kentlib  # noqa: E402

FIRST_LOOK_HOURS = 24


def context_for(kdb, session: str, hours: int = FIRST_LOOK_HOURS) -> str:
    reader = f"kent-chat:{session}"
    if kdb.execute("SELECT 1 FROM notice_reads WHERE reader=?", (reader,)).fetchone() is None:
        start = kdb.execute("SELECT COALESCE(MAX(id), 0) FROM notices WHERE created_at < datetime('now', ?)",
                            (f"-{hours} hours",)).fetchone()[0]
        kdb.execute("INSERT INTO notice_reads (reader, last_id) VALUES (?, ?)", (reader, start))
        kdb.commit()
    rows = kentlib.unread_notices(kdb, reader)
    if not rows:
        return ""
    lines = "\n".join(f"- [{r['created_at']} UTC] " + r["text"].replace("\n", "\n  ") for r in rows)
    return ("[Kent's own notices, from Kent's relay; not from the operator. Tell the operator about "
            "anything they have not heard yet, briefly, when it is relevant. When a notice carries Kent's "
            "frontier review, show it in full (verdict, one line per task, issues) when the operator asks "
            "about that Gent; do not claim more than it says.]\n" + lines)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        session = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("session_id") or "none"))[:80]
        c = kentlib.conf()
        hours = int(c.get("NOTICE_LOOKBACK_HOURS", FIRST_LOOK_HOURS))
        ctx = context_for(kentlib.kent_db(c), session, max(0, hours))
        if ctx:
            print(json.dumps({"context": ctx}))
    except Exception:  # noqa: BLE001 - never break Kent's chat over a notice
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
