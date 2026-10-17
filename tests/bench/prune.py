#!/usr/bin/env python3
"""Prune the bench to the items that still say something: prune.py RUN_DIR... > suites/v1.json

Reads every RUN_DIR/<suite>-<model>/results.jsonl and keeps an item unless every row of every model
passed it (a ceiling item tells two good models nothing). Items every model failed are kept: a better
model may still pass them. Writes {"rule": ..., "sources": [...], "probes": [ids], "kent": [...],
"crews": [...]} on stdout; run.py --tier v1 runs exactly those ids.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def main():
    rows = defaultdict(list)
    sources = []
    for runs in map(Path, sys.argv[1:]):
        for d in sorted(p for p in runs.expanduser().iterdir() if (p / "results.jsonl").exists()):
            suite = d.name.partition("-")[0]
            sources.append(str(d.resolve()))
            for r in map(json.loads, (d / "results.jsonl").read_text().splitlines()):
                rows[(suite, r["id"])].append(r["pass"])
    keep = {"probes": [], "kent": [], "crews": []}
    for (suite, i), v in sorted(rows.items()):
        if not all(v):
            keep[suite].append(i)
    print(json.dumps({"rule": "drop items every row of every model passed", "sources": sources, **keep}, indent=1))
    print(f"prune: kept {sum(map(len, keep.values()))}/{len(rows)} "
          + " ".join(f"{s} {len(v)}" for s, v in keep.items()), file=sys.stderr)


if __name__ == "__main__":
    main()
