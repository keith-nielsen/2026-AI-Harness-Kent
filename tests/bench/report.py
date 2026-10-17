#!/usr/bin/env python3
"""Bench report: report.py RUNS_DIR [--calibrate OUT.json]

Reads every RUNS_DIR/<suite>-<model>/results.jsonl and prints, per model:
  - pass rate per category x level (all suites), with pass^k where an item has k > 1 repeats
  - efficiency: prompt + completion tokens per solved item, requests, cut-off replies, errors (S2/S3)
With --calibrate it fits the IRT ladder (irt.py) over all models and writes item difficulties and
each model's per-category ability (theta) to OUT.json.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent / "suites")]


def load(runs: Path) -> list[dict]:
    rows = []
    for d in sorted(p for p in runs.iterdir() if (p / "results.jsonl").exists()):
        suite, _, model = d.name.partition("-")
        for r in map(json.loads, (d / "results.jsonl").read_text().splitlines()):
            r["suite"], r["model"] = suite, model
            rows.append(r)
    return rows


def table(rows: list[dict]) -> str:
    out = []
    models = sorted({r["model"] for r in rows})
    levels = sorted({r["level"] for r in rows})
    for m in models:
        mr = [r for r in rows if r["model"] == m]
        out.append(f"\n== {m}: {sum(r['pass'] for r in mr)}/{len(mr)} rows passed")
        out.append("cat  " + "".join(f"   L{l}    " for l in levels))
        for c in "TIRH":
            cells = []
            for l in levels:
                cr = [r for r in mr if r["cat"] == c and r["level"] == l]
                cells.append(f"{sum(r['pass'] for r in cr):>3}/{len(cr):<3}  " if cr else "    -    ")
            out.append(f"{c}   " + " ".join(cells))
        by_item = defaultdict(list)
        for r in mr:
            by_item[(r["suite"], r["id"])].append(r["pass"])
        rep_items = [v for v in by_item.values() if len(v) > 1]
        if rep_items:
            out.append(f"pass^k: {sum(all(v) for v in rep_items)}/{len(rep_items)} repeated items passed every time; "
                       f"flips (passed and failed): {sum(any(v) and not all(v) for v in rep_items)}")
        tok = [r for r in mr if r.get("prompt_tokens") is not None and r["suite"] in ("kent", "crews")]
        if tok:
            solved = [r for r in tok if r["pass"]]
            pt = sum(r["prompt_tokens"] or 0 for r in solved); ct = sum(r["completion_tokens"] or 0 for r in solved)
            out.append(f"S2/S3 efficiency: {len(solved)} solved; per solved item {pt // max(1, len(solved)):,} prompt + "
                       f"{ct // max(1, len(solved)):,} completion tokens, "
                       f"{sum(r.get('requests', 0) for r in solved) / max(1, len(solved)):.1f} requests; "
                       f"cut-off replies {sum(r.get('cut_off', 0) for r in tok)}, errors {sum(r.get('errors', 0) for r in tok)}")
    return "\n".join(out)


def main():
    runs = Path(sys.argv[1]).expanduser()
    rows = load(runs)
    print(table(rows))
    if "--calibrate" in sys.argv:
        import irt
        items = {r["id"]: {"cat": r["cat"], "level": r["level"]} for r in rows}
        cal = irt.calibrate(rows, items)
        irt.save(sys.argv[sys.argv.index("--calibrate") + 1], cal)
        print("\nability (theta) per model and category:")
        for m, th in sorted(cal["theta"].items()):
            print(f"  {m:<10} " + "  ".join(f"{c} {th.get(c, float('nan')):+.2f}" for c in "TIRH"))
        n = defaultdict(lambda: [0, 0])
        for r in rows:
            n[r["id"]][0] += r["pass"]; n[r["id"]][1] += 1
        flat = [i for i, (p, t) in n.items() if p in (0, t)]
        print(f"\n{len(flat)}/{len(n)} items gave every model the same result (no information between models)")


if __name__ == "__main__":
    main()
