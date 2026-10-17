#!/usr/bin/env python3
"""pick_best.py — rank the pilot models on their first pass and print where the best one's runs live.

Score = mean over suites (probes, kent, crews) of passes / items, first repeat only; a missing or
unfinished suite counts its missing items as failures. Per model and suite the newest valid run folder
is used: pilot-v1b first, then pilot-v1, except the pilot-v1 folders known to be invalid (stock ran
on the pre-fix bench; occamy's crews hit the fixture outage). The ranking goes to stderr; stdout gets
one line per run folder of the winner: "RUN_NAME MODEL SUITE [SUITE...]" (input for campaign.sh).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

RUNS = Path.home() / "Documents/repo/bench/runs"
ORDER = ["pilot-v1b", "pilot-v1"]
MODELS = ["occamy", "stock", "stock-q6"]   # carnice, nemotron: dropped by the operator
SUITES = ["probes", "kent", "crews"]
INVALID = {("pilot-v1", "stock", s) for s in SUITES} | {("pilot-v1", "occamy", "crews")}


def first_pass(run: str, suite: str, model: str) -> dict[str, bool] | None:
    f = RUNS / run / f"{suite}-{model}" / "results.jsonl"
    if (run, model, suite) in INVALID or not f.exists():
        return None
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    got = {r["id"]: bool(r["pass"]) for r in rows if r.get("rep", 0) == 0}
    return got or None


def main() -> int:
    found: dict[tuple[str, str], tuple[str, dict[str, bool]]] = {}
    for m in MODELS:
        for s in SUITES:
            for run in ORDER:
                if (got := first_pass(run, s, m)) is not None:
                    found[(m, s)] = (run, got)
                    break
    size = {s: max((len(v[1]) for (m, s2), v in found.items() if s2 == s), default=0) for s in SUITES}
    scores = {}
    for m in MODELS:
        parts = []
        for s in SUITES:
            got = found.get((m, s), (None, {}))[1]
            parts.append((sum(got.values()), size[s]))
        scores[m] = sum(p / n for p, n in parts if n) / len([1 for _, n in parts if n] or [1])
        print(f"pick_best: {m:9s} score {scores[m]:.3f}  " +
              "  ".join(f"{s} {p}/{n}" for s, (p, n) in zip(SUITES, parts)), file=sys.stderr)
    best = max(MODELS, key=lambda m: scores[m])
    print(f"pick_best: best = {best}", file=sys.stderr)
    by_run = defaultdict(list)
    for s in SUITES:
        if (best, s) in found:
            by_run[found[(best, s)][0]].append(s)
    for run, suites in by_run.items():
        print(run, best, " ".join(suites))
    return 0


if __name__ == "__main__":
    sys.exit(main())
