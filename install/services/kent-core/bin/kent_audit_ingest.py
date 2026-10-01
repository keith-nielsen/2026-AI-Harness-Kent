#!/usr/bin/python3
"""Append security-relevant events from Loki to the HMAC audit chain.

Sources (since the last cursor): gateway access denials, Kent install/uninstall
events (journal tag kent-install), and sudo invocations. Idempotent: the cursor
(last ingested timestamp, ns) lives next to the chain.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kentlib  # noqa: E402

SOURCES = [
    ("gateway", '{unit="kent-litellm.service", kent_event="access_denied"}'),
    ("install", '{syslog_identifier="kent-install"}'),
    ("sudo", '{syslog_identifier="sudo"} |= "COMMAND="'),
]


def main() -> int:
    c = kentlib.conf()
    cursor_file = Path(c["AUDIT_LOG"]).with_suffix(".cursor")
    start = int(cursor_file.read_text()) + 1 if cursor_file.exists() else time.time_ns() - 24 * 3600 * 10**9
    end = time.time_ns() - 5 * 10**9            # leave a small window for late-arriving lines
    events = []
    for source, query in SOURCES:
        for ts, labels, line in kentlib.loki_query_range(c, query, start, end):
            events.append((ts, source, labels, line))
    events.sort(key=lambda e: e[0])
    for ts, source, labels, line in events:
        if source == "gateway":
            try:
                d = json.loads(line)
                detail = f"identity={d.get('identity')} route={d.get('route')} model={d.get('model')} reason={d.get('reason')}"
            except ValueError:
                detail = line
            kentlib.audit("kent-litellm", "access_denied", detail)
        elif source == "install":
            kentlib.audit("kent-install", "change", line)
        else:
            kentlib.audit("sudo", "command", line)
    cursor_file.write_text(str(max([e[0] for e in events], default=end)))
    print(f"ingested {len(events)} event(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
