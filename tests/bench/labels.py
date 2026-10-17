#!/usr/bin/env python3
"""labels.py — per-prompt triage labels from thinking-budget probe runs (classifier redesign, task 1).

    python3 tests/bench/labels.py build RUN_DIR... --out LABELS.json [--model kv-q8q4]
    python3 tests/bench/labels.py curve LABELS.json

build reads every RUN_DIR/probes-<model>-think<mode>/results.jsonl (campaign.sh THINKS runs), pools all
repeats of an item per thinking mode across the runs, and writes one row per probe:

  id, cat, level, band (id letter), template (system frame: gent | hermes | bare | custom), turns, v1 (in the
  pruned hard set suites/v1.json), prompt_tokens (median), modes{mode: n, passes, pass_rate, mean_s, median_s,
  at_budget}, and the labels
  easy_safe = passed every repeat with thinking off (null without off data),
  helps_4k  = 4096 pass rate > 2048 pass rate (null unless both were run).

Labels come from the serving model's own outcomes, not from designed difficulty. The runs must share model
file, llama build, bench hash, suite and Kent state (else build refuses); harness hashes are recorded, not
compared (probe results do not depend on them). curve prints the fixed-budget table in the format of
bench/runs/*/sim115.md: item-weighted pass and seconds, easy = items outside v1.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "suites")]

SCHEMA = 1
SAME = ("model_sha256", "llama_build", "bench_hash", "suite", "kent_bench_state")
ARM = re.compile(r"^probes-(?P<model>.+)-think(?P<mode>off|\d+)$")
TEMPLATES = ("gent", "hermes", "bare", "custom")


def mode_key(m: str) -> tuple[int, int]:
    return (0, 0) if m == "off" else (1, int(m))


def template(it: dict) -> str:
    s, t = it["system"], it["tools"]
    if s in ("gent", "hermes") and t == s:
        return s
    return "bare" if s is None and t is None else "custom"


def item_meta() -> dict[str, dict]:
    import probes
    v1 = set(json.loads((HERE / "suites" / "v1.json").read_text())["probes"])
    return {it["id"]: {"cat": it["cat"], "level": it["level"], "band": it["id"][0], "template": template(it),
                       "turns": len(it["messages"]), "v1": it["id"] in v1} for it in probes.ITEMS}


def arms(run_dirs: list[Path], model: str) -> list[tuple[Path, str]]:
    out = []
    for d in run_dirs:
        for sub in sorted(d.iterdir()):
            m = ARM.match(sub.name)
            if sub.is_dir() and m and m["model"] == model and (sub / "results.jsonl").exists():
                out.append((sub, m["mode"]))
    return out


def setup(manifest: dict) -> dict:
    kb = manifest.get("kent_bench", "")
    m = re.search(r"Kent state (\w+)", kb)
    return {"model_sha256": manifest.get("model_sha256"), "llama_build": manifest.get("llama_build"),
            "bench_hash": manifest.get("bench_hash"), "suite": manifest.get("suite"),
            "kent_bench_state": m[1] if m else None}


def build(run_dirs: list[Path], model: str) -> dict:
    found = arms(run_dirs, model)
    if not found:
        raise SystemExit(f"no probes-{model}-think<mode> results under {', '.join(map(str, run_dirs))}")
    sources, ref = [], None
    samples = defaultdict(lambda: defaultdict(list))        # samples[id][mode] = [result row]
    for sub, mode in found:
        man = json.loads((sub / "manifest.json").read_text())
        s = setup(man)
        if ref is None:
            ref = s
        diff = [k for k in SAME if s[k] != ref[k]]
        if diff:
            raise SystemExit(f"{sub}: different setup from {sources[0]['arm']} in {', '.join(diff)}")
        rows = {}
        for line in (sub / "results.jsonl").read_text().splitlines():
            r = json.loads(line)
            rows[(r["id"], r["rep"])] = r                   # last row wins (a re-asked item), as think_report
        for r in rows.values():
            samples[r["id"]][mode].append(r)
        sources.append({"arm": str(sub), "mode": mode, "rows": len(rows), "tier": man.get("tier"),
                        "harness_code_hash": man.get("harness_code_hash"),
                        "harness_dirty_hash": man.get("harness_dirty_hash")})
    meta = item_meta()
    unknown = sorted(set(samples) - set(meta))
    if unknown:
        raise SystemExit(f"items not in suites/probes.py: {', '.join(unknown)}")
    out = []
    for i in sorted(samples):
        modes = {}
        for m in sorted(samples[i], key=mode_key):
            rs = samples[i][m]
            secs = [r["seconds"] or 0 for r in rs]
            passes = sum(bool(r["pass"]) for r in rs)
            modes[m] = {"n": len(rs), "passes": passes, "pass_rate": passes / len(rs),
                        "mean_s": statistics.mean(secs), "median_s": statistics.median(secs),
                        "at_budget": 0 if m == "off" else sum((r["completion_tokens"] or 0) >= int(m) for r in rs)}
        off, b2, b4 = modes.get("off"), modes.get("2048"), modes.get("4096")
        toks = [r["prompt_tokens"] for rs in samples[i].values() for r in rs if r.get("prompt_tokens")]
        out.append({"id": i, **meta[i], "prompt_tokens": statistics.median(toks) if toks else None,
                    "modes": modes,
                    "easy_safe": None if off is None else off["passes"] == off["n"],
                    "helps_4k": None if b2 is None or b4 is None else b4["pass_rate"] > b2["pass_rate"]})
    return {"schema": SCHEMA, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model, "setup": ref, "sources": sources,
            "definitions": {"easy_safe": "passed every repeat with thinking off",
                            "helps_4k": "pass rate at thinking budget 4096 > at 2048",
                            "at_budget": "repeats whose completion tokens reached the thinking budget",
                            "template": "system frame of the probe: gent | hermes | bare (no system, no tools) | custom",
                            "v1": "in suites/v1.json (pruned hard set); others = every model passed them thinking off"},
            "rows": out}


def curve(lab: dict) -> str:
    rows = lab["rows"]
    modes = sorted({m for r in rows for m in r["modes"]}, key=mode_key)
    hard = [r for r in rows if r["v1"]]
    easy = [r for r in rows if not r["v1"]]
    missing = [(r["id"], m) for r in rows for m in modes if m not in r["modes"]]
    out = [f"items {len(rows)} (hard {len(hard)}, easy {len(easy)}); missing cells {missing[:5]}"
           f"{'...' if len(missing) > 5 else ''} ({len(missing)})",
           "reps per cell (hard): " + str({m: sorted({r['modes'][m]['n'] for r in hard if m in r['modes']}) for m in modes})
           + " (easy): " + str({m: sorted({r['modes'][m]['n'] for r in easy if m in r['modes']}) for m in modes}),
           "", "## Fixed budgets", "",
           f"| budget | all {len(rows)} pass | all s/item | hard {len(hard)} pass | hard s | easy {len(easy)} pass "
           "| easy s | easy median s |", "|---|---:|---:|---:|---:|---:|---:|---:|"]

    def ev(sub, m):
        c = [r["modes"][m] for r in sub if m in r["modes"]]
        return sum(x["pass_rate"] for x in c) / len(c), sum(x["mean_s"] for x in c) / len(c)
    for m in modes:
        (a, at), (h, ht), (e, et) = ev(rows, m), ev(hard, m), ev(easy, m)
        med = statistics.median(r["modes"][m]["mean_s"] for r in easy if m in r["modes"])
        out.append(f"| {m} | {100*a:.1f}% | {at:.0f} | {100*h:.1f}% | {ht:.0f} | {100*e:.1f}% | {et:.0f} | {med:.0f} |")
    es = [r["easy_safe"] for r in rows if r["easy_safe"] is not None]
    h4 = [r["helps_4k"] for r in rows if r["helps_4k"] is not None]
    out += ["", f"Labels: easy_safe {sum(es)}/{len(es)} (v1 {sum(bool(r['easy_safe']) for r in hard)}/{len(hard)}, "
            f"others {sum(bool(r['easy_safe']) for r in easy)}/{len(easy)}); helps_4k {sum(h4)}/{len(h4)}."]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("runs", nargs="+", type=Path)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--model", default="kv-q8q4")
    c = sp.add_parser("curve")
    c.add_argument("labels", type=Path)
    a = ap.parse_args()
    if a.cmd == "build":
        lab = build(a.runs, a.model)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(lab, indent=1) + "\n")
        print(f"{a.out}: {len(lab['rows'])} rows from {len(lab['sources'])} arms")
    else:
        print(curve(json.loads(a.labels.read_text())))


if __name__ == "__main__":
    main()
