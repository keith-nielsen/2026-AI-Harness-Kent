#!/usr/bin/env python3
"""Report for tbcheck.sh: -tb 4 against the default (-tb = -t = 5), per production measurement, and the verdict.

  tb_report.py RUN_DIR      (markdown on stdout; the last line starts with VERDICT)

Rule (fixed before the run): keep -tb 4 only if it is >= 3% faster than the default on at least one measurement
AND not >= 3% slower on any; otherwise remove the override. A pair measurement flagged by sysmon for anything
other than a CPU-clock dip is left out (listed).
"""
import glob, json, os, statistics, sys

RUN, THRESH = sys.argv[1], 3.0


def runs(prefix):
    out = {"tb4": [], "default": []}
    for f in sorted(glob.glob(f"{RUN}/{prefix}-*.json")):
        v = os.path.basename(f)[len(prefix) + 1:].rsplit("-", 1)[0]
        out[v].append(json.load(open(f)))
    return out


bench, pair = runs("bench"), runs("pair")
excluded = []
metrics = {   # name -> function(bench_json, pair_json) -> value (higher is better)
    "decode, 1 request (control: -tb unused)": ("bench", lambda b: b["decode"]["1"]["agg_gen_tps"]),
    "decode, 2 at once (aggregate)": ("bench", lambda b: b["decode"]["2"]["agg_gen_tps"]),
    "edit + agentic at once (aggregate)": ("pair", lambda p: p["requests"]["pair"]["agg_tok_s"]),
    "prefill 16k, 1 request": ("bench", lambda b: b["prefill"]["1"]["agg_pp_tps"]),
    "prefill 16k, 2 at once (aggregate)": ("bench", lambda b: b["prefill"]["2"]["agg_pp_tps"]),
    "decode while the other slot reads 32k": ("bench", lambda b: b["interference"]["decode_tps"]),
    "32k prefill while the other slot decodes": ("bench", lambda b: b["interference"]["prefill_tps"]),
}
print("## -tb 4 vs default (-tb = -t = 5), production configuration (tok/s, mean of the A-B-B-A runs)\n")
print("| measurement | -tb 4 | default | -tb 4 vs default | runs (tb4 / default) |")
print("|---|---:|---:|---:|---|")
better, worse, missing = [], [], []
for name, (src, fn) in metrics.items():
    vals = {}
    for v in ("tb4", "default"):
        xs = []
        for d in (bench if src == "bench" else pair)[v]:
            if src == "pair":
                fl = [f for f in d["requests"]["pair"]["flags"] if not f.startswith("cpu_mhz")]
                if fl:
                    excluded.append(f"{v} pair: {', '.join(fl)}"); continue
            try:
                xs.append(fn(d))
            except (KeyError, TypeError):
                pass
        vals[v] = xs
    if not vals["tb4"] or not vals["default"]:
        missing.append(name)
        print(f"| {name} | - | - | - | {len(vals['tb4'])} / {len(vals['default'])} |"); continue
    a, b = statistics.mean(vals["tb4"]), statistics.mean(vals["default"])
    d = (a / b - 1) * 100
    if d >= THRESH: better.append(f"{name} ({d:+.1f}%)")
    if d <= -THRESH: worse.append(f"{name} ({d:+.1f}%)")
    spread = lambda xs: f"{min(xs):.1f}-{max(xs):.1f}"
    print(f"| {name} | {a:.1f} | {b:.1f} | **{d:+.1f}%** | {spread(vals['tb4'])} / {spread(vals['default'])} |")
print()
if excluded:
    print("Excluded (sysmon flags): " + "; ".join(excluded) + "\n")
for f in ("hostprofile-start.txt", "hostprofile-end.txt"):
    if os.path.exists(f"{RUN}/{f}"):
        print(f"{f}: " + " ".join(l for l in open(f"{RUN}/{f}").read().split("\n") if l.startswith(("boost", "core_clock", "OK", "MISMATCH"))))
print()
if missing:
    print(f"VERDICT: INCONCLUSIVE — no data for: {'; '.join(missing)}")
elif better and not worse:
    print(f"VERDICT: KEEP -tb 4 — >= {THRESH}% faster on: {'; '.join(better)}; not >= {THRESH}% slower anywhere")
else:
    why = (f"-tb 4 is >= {THRESH}% slower on: {'; '.join(worse)}" if worse else "") + \
          ("; " if worse and better else "") + (f"faster on: {'; '.join(better)}" if better else "")
    print(f"VERDICT: REMOVE -tb 4 (use the default) — {why or f'no measurement differs by >= {THRESH}%'}")
