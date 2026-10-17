#!/usr/bin/env python3
"""kld_corpus.py PROXY_DIR OUT.txt — an "agent traffic" text corpus for the KL-divergence comparison.

Takes the Kent (S2) and crew (S3) requests a bench proxy recorded, keeps the last request of each
(item, repeat) (it holds the whole conversation so far) plus that request's reply, drops messages already
written (system prompts and tool results repeat across items), and writes them as plain text with
"### role" headers, in request order. Deterministic for a given proxy log. Output stays local: the
fixtures it quotes are git-ignored bench content.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path


def text_of(m: dict) -> str:
    parts = []
    c = m.get("content")
    if isinstance(c, list):
        c = "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
    if c:
        parts.append(str(c))
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function") or {}
        parts.append(f"[tool call] {fn.get('name')}({fn.get('arguments') or ''})")
    return "\n".join(parts).strip()


def main():
    proxy, out = Path(sys.argv[1]), Path(sys.argv[2])
    recs = [json.loads(l) for l in (proxy / "requests.jsonl").read_text().splitlines()]
    last = {}
    for r in recs:
        item = str(r.get("item"))
        if item.startswith(("kent:", "crew:")):
            last[(item, r.get("rep"))] = r["n"]
    seen, blocks = set(), []
    for n in sorted(last.values()):
        b = json.load(gzip.open(proxy / "bodies" / f"{n:06d}.json.gz", "rt"))
        msgs = list(b["request"]["messages"])
        reply = ((b.get("response") or {}).get("choices") or [{}])[0].get("message")
        if reply:
            msgs.append({"role": "assistant", **reply})
        for m in msgs:
            t = text_of(m)
            if t and t not in seen:
                seen.add(t)
                blocks.append(f"### {m.get('role')}\n{t}\n")
    out.write_text("\n".join(blocks))
    print(f"kld_corpus: {len(last)} conversations, {len(blocks)} messages, {out.stat().st_size:,} bytes -> {out}")


if __name__ == "__main__":
    main()
