#!/usr/bin/env python3
"""Report for mtpcheck.sh: step cost (llama-bench), the MTP sweep (mtp_probe.py), and predicted vs measured.

  mtp_report.py RUN_DIR      (markdown on stdout)

Requests flagged only for a CPU-clock dip (cpu_mhz) are kept in the means and the busy-core average clock is shown
per row, so a clock difference between variants is visible directly; any other flag (swap, majflt, other load,
GPU clock or counters, prompt reuse) excludes the request. Every flagged request is listed. "text" compares each
request's greedy output with the baseline (none, -tb 4) of the same workload: same = token-identical output.
Prediction: ms/step ~ step cost at N = 1 + drafts offered per step (linear interpolation of llama-bench at the
variant's batch thread count; single-token steps always run on -t 5). The residual is what the step cost does
not explain (drafting itself, sampling, server bookkeeping).
"""
import glob, json, os, statistics, sys

RUN = sys.argv[1]


def mean_sd(xs):
    return (statistics.mean(xs), statistics.stdev(xs) if len(xs) > 1 else 0.0) if xs else (None, None)


# B. step cost
cost = {}   # threads -> {N: ms}
try:
    for e in json.load(open(f"{RUN}/stepcost.json")):
        if e.get("n_gen", 0) == 0 and e.get("n_prompt"):
            cost.setdefault(e["n_threads"], {})[e["n_prompt"]] = e["avg_ns"] / 1e6
except (OSError, ValueError) as ex:
    print(f"(no step cost: {ex})\n")
if cost:
    print("## B. Step cost: one forward pass of N tokens at depth 2048 (ms; ratio to N=1 at -t 5)\n")
    ns = sorted({n for c in cost.values() for n in c})
    base1 = cost.get(5, {}).get(1)
    print("| threads | " + " | ".join(f"N={n}" for n in ns) + " |")
    print("|---|" + "---:|" * len(ns))
    for t in sorted(cost):
        print(f"| {t} | " + " | ".join(f"{cost[t][n]:.1f} ({cost[t][n] / base1:.2f}x)" if n in cost[t] and base1 else "-"
                                       for n in ns) + " |")
    print()


def predict(tb, n_eff):
    c = cost.get(tb)
    if not c or 1 not in cost.get(5, {}):
        return None
    if n_eff <= 1:
        return cost[5][1]
    xs = sorted(k for k in c if k >= 2) or [1]
    lo = max([k for k in xs if k <= n_eff] or [xs[0]]); hi = min([k for k in xs if k >= n_eff] or [xs[-1]])
    if lo == hi:
        return c[lo]
    return c[lo] + (c[hi] - c[lo]) * (n_eff - lo) / (hi - lo)


# C. sweep
rows = {}
pairs = {}
for f in sorted(glob.glob(f"{RUN}/probe-*.json")):
    d = json.load(open(f))
    rnd = os.path.basename(f).rsplit("-r", 1)[1].split(".")[0]
    for w, rq in d["requests"].items():
        if w == "pair":
            pairs.setdefault(d["label"], []).append((rnd, rq))
        else:
            rows.setdefault((d["label"], w), []).append((rnd, rq))
base_sha = {w: {r["text_sha"] for _, r in v} for (lab, w), v in rows.items() if lab == "none"}

print("## C. MTP sweep (greedy; mean ± sd over clean rounds)\n")
print("| variant | workload | tok/s | ms/step | tok/step | drafts/step | accept | predicted ms/step | residual | busy-core MHz | text vs none | used |")
print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
order = sorted({lab for lab, _ in rows}, key=lambda s: (s.endswith("tb5"), s != "none", s))
flagged = []
for lab in order:
    for w in ("edit", "agentic", "prose"):
        v = rows.get((lab, w), [])
        clean = [r for _, r in v if all(f.startswith("cpu_mhz") for f in r["flags"])]
        flagged += [(lab, w, rnd, r["flags"]) for rnd, r in v if r["flags"]]
        if not clean:
            print(f"| {lab} | {w} | - | - | - | - | - | - | - | - | 0/{len(v)} |"); continue
        tps, mps = mean_sd([r["tok_s"] for r in clean]), mean_sd([r["ms_per_step"] for r in clean])
        tpst = statistics.mean(r["tok_per_step"] for r in clean)
        dps = statistics.mean(r["draft_n"] / r["steps"] for r in clean)
        dn = sum(r["draft_n"] for r in clean)
        acc = sum(r["draft_acc"] for r in clean) / dn if dn else None
        tb = 5 if lab.endswith("tb5") else 4
        pr = predict(tb, 1 + dps)
        same = {r["text_sha"] for r in clean} <= base_sha.get(w, set())
        print(f"| {lab} | {w} | {tps[0]:.2f} ± {tps[1]:.2f} | {mps[0]:.1f} ± {mps[1]:.1f} | {tpst:.2f} | {dps:.2f} | "
              f"{'-' if acc is None else f'{acc:.2f}'} | {'-' if pr is None else f'{pr:.1f}'} | "
              f"{'-' if pr is None else f'{mps[0] - pr:+.1f}'} | "
              f"{statistics.mean(r['sys']['cpu_avg_mhz'] for r in clean):.0f} (min {min(r['sys']['cpu_min_mhz'] or 0 for r in clean):.0f}) | "
              f"{'same' if same else 'DIFFERS'} | {len(clean)}/{len(v)} |")
print()
if pairs:
    print("## D. Two slots at once (edit + agentic concurrently; aggregate = all tokens / longest generation time)\n")
    print("| variant | aggregate tok/s | edit tok/s | agentic tok/s | wall tok/s | edit ms/step | agentic ms/step | clean |")
    print("|---|---:|---:|---:|---:|---:|---:|---|")
    for lab in sorted(pairs, key=lambda s: (s != "none-pair", s)):
        v = pairs[lab]
        clean = [r for _, r in v if all(f.startswith("cpu_mhz") for f in r["flags"])]
        flagged += [(lab, "pair", rnd, r["flags"]) for rnd, r in v if r["flags"]]
        if not clean:
            print(f"| {lab} | - | - | - | - | - | - | 0/{len(v)} |"); continue
        m = lambda f: mean_sd([f(r) for r in clean])
        a, e, g, wl = m(lambda r: r["agg_tok_s"]), m(lambda r: r["members"]["edit"]["tok_s"]), \
            m(lambda r: r["members"]["agentic"]["tok_s"]), m(lambda r: r["wall_tok_s"])
        es, gs = m(lambda r: r["members"]["edit"]["ms_per_step"]), m(lambda r: r["members"]["agentic"]["ms_per_step"])
        print(f"| {lab} | {a[0]:.2f} ± {a[1]:.2f} | {e[0]:.2f} | {g[0]:.2f} | {wl[0]:.2f} | {es[0]:.1f} | {gs[0]:.1f} | "
              f"{len(clean)}/{len(v)} |")
    print()
print("## Flagged requests (cpu_mhz only: kept; anything else: excluded)\n")
print("\n".join(f"- {lab} {w} round {rnd}: {', '.join(fl)}" for lab, w, rnd, fl in flagged) or "none")
try:
    print("\n## Host profile (tests/bench/hostprofile.sh check after stopping kent-llama, and at the end)\n")
    for f in ("hostprofile-start.txt", "hostprofile-end.txt"):
        if os.path.exists(f"{RUN}/{f}"):
            print(f"{f}:\n```\n" + open(f"{RUN}/{f}").read().strip() + "\n```")
except OSError:
    pass
