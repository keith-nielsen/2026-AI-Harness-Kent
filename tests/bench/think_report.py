#!/usr/bin/env python3
"""think_report.py — compare probe runs of one model in different thinking modes (campaign.sh THINKS).

    python3 tests/bench/think_report.py RUNS/NAME [--baseline RUN_DIR] [--model MODEL]

Reads RUNS/NAME/probes-<model>-think<mode>/results.jsonl for every mode present (off, 2048, 8192, ...)
and prints, in Markdown: pass rate per mode, paired comparisons on the (item, repeat) pairs both modes
finished (discordant counts and exact McNemar p), time and tokens per item, how often reasoning ran into
its budget, and per-item changes. --baseline compares the off arm with an earlier run of the same items
(e.g. bench/runs/kv-quality/probes-kv-q8q4) the same way. Only finished items count; a run still going
is reported as far as it got.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path


def mcnemar_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def load(d: Path) -> dict[tuple[str, int], dict]:
    rows = {}
    f = d / "results.jsonl"
    for line in f.read_text().splitlines() if f.exists() else []:
        r = json.loads(line)
        rows[(r["id"], r["rep"])] = r          # last row wins (a re-asked item)
    return rows


def mode_key(m: str) -> tuple[int, int]:
    return (0, 0) if m == "off" else (1, int(m))


def pct(k: int, n: int) -> str:
    return f"{100 * k / n:.1f}%" if n else "-"


def compare(name_a: str, a: dict, name_b: str, b: dict) -> str:
    keys = sorted(set(a) & set(b))
    if not keys:
        return f"| {name_a} vs {name_b} | 0 | - | - | - | - | - |"
    pa = sum(a[k]["pass"] for k in keys)
    pb = sum(b[k]["pass"] for k in keys)
    only_a = sum(a[k]["pass"] and not b[k]["pass"] for k in keys)
    only_b = sum(b[k]["pass"] and not a[k]["pass"] for k in keys)
    return (f"| {name_a} vs {name_b} | {len(keys)} | {pa} | {pb} | {only_a} / {only_b} | "
            f"{100 * (pb - pa) / len(keys):+.1f} pt | {mcnemar_p(only_a, only_b):.3f} |")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("--model", default="kv-q8q4")
    ap.add_argument("--baseline", default="")
    a = ap.parse_args()
    runs = Path(a.runs)
    modes = {}
    for d in runs.glob(f"probes-{a.model}-think*"):
        if (m := re.fullmatch(rf"probes-{re.escape(a.model)}-think(off|\d+)", d.name)):
            modes[m.group(1)] = load(d)
    if not modes:
        print(f"no probes-{a.model}-think* runs in {runs}")
        return 1
    order = sorted(modes, key=mode_key)
    out = [f"# Thinking modes — {runs.name} ({a.model})", "",
           "## Pass rate (finished items)", "",
           "| mode | finished | pass | rate | reps done | median s/item | mean completion tokens | "
           "mean reasoning chars | at budget* | cut at max_tokens |",
           "|---|---:|---:|---:|---|---:|---:|---:|---:|---:|"]
    for m in order:
        rows = list(modes[m].values())
        n = len(rows)
        p = sum(r["pass"] for r in rows)
        reps = sorted({r["rep"] for r in rows})
        secs = [r.get("seconds") or 0 for r in rows]
        toks = [r.get("completion_tokens") or 0 for r in rows]
        rc = [r.get("reasoning_chars") or 0 for r in rows]
        # reasoning that ran into the budget: completion tokens at or above the budget (reasoning counts against it)
        at_budget = sum(t >= int(m) for t in toks) if m != "off" else 0
        cut = sum(r.get("finish") == "length" for r in rows)
        out.append(f"| {m} | {n} | {p} | {pct(p, n)} | {','.join(map(str, reps))} | "
                   f"{statistics.median(secs) if secs else 0:.1f} | {statistics.mean(toks) if toks else 0:.0f} | "
                   f"{statistics.mean(rc) if rc else 0:.0f} | {at_budget} | {cut} |")
    leaks = sum(r.get("think_leak") for rows in modes.values() for r in rows.values())
    out += ["", f"*completion tokens >= the budget. Reasoning leaked into the answer (<think> in content): {leaks}.", "",
            "## Paired comparisons (same item and repeat finished in both)", "",
            "| comparison | pairs | pass A | pass B | only A / only B | B - A | McNemar p |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for i, ma in enumerate(order):
        for mb in order[i + 1:]:
            out.append(compare(ma, modes[ma], mb, modes[mb]))
    if a.baseline and "off" in modes:
        base = load(Path(a.baseline))
        out.append(compare(f"baseline {Path(a.baseline).name}", base, "off (this run)", modes["off"]))
    # per category
    cats = sorted({r["cat"] for rows in modes.values() for r in rows.values()})
    out += ["", "## Pass rate by category (finished items)", "",
            "| category | " + " | ".join(order) + " |", "|---|" + "---:|" * len(order)]
    for c in cats:
        cells = []
        for m in order:
            rows = [r for r in modes[m].values() if r["cat"] == c]
            cells.append(f"{sum(r['pass'] for r in rows)}/{len(rows)}")
        out.append(f"| {c} | " + " | ".join(cells) + " |")
    # items that change between off and each thinking mode, over all repeats
    if "off" in modes:
        out += ["", "## Items that change (passes over finished repeats)", "",
                "| item | " + " | ".join(order) + " |", "|---|" + "---:|" * len(order)]
        items = sorted({k[0] for k in modes["off"]})
        for it in items:
            cells, scores = [], []
            for m in order:
                rows = [r for (i, _), r in modes[m].items() if i == it]
                cells.append(f"{sum(r['pass'] for r in rows)}/{len(rows)}" if rows else "-")
                scores.append(sum(r["pass"] for r in rows) / len(rows) if rows else None)
            known = [s for s in scores if s is not None]
            if len(known) > 1 and max(known) != min(known):
                out.append(f"| {it} | " + " | ".join(cells) + " |")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
