#!/usr/bin/env python3
"""Summary for depthcheck.sh: the fixed-schedule -tb comparison and depth.csv by variant x bucket x task x sampling.

  depth_report.py RUN_DIR      (markdown on stdout; depth.csv in RUN_DIR has one row per request for any other cut)

Rows flagged by sysmon for anything other than a CPU-clock dip, and rows with prompt reuse where none was asked
for, are left out of the means (counted). Prefill is the copy-greedy request of each prompt (full prompt read).
"""
import csv, glob, json, os, statistics as st, sys

RUN = sys.argv[1]

print("## 1. Fixed schedule (llama-batched-bench, 2048-token prompts, 128 generated): decode tok/s (S_TG)\n")
bb = {}
for f in sorted(glob.glob(f"{RUN}/batched-*.jsonl")):
    v = os.path.basename(f)[len("batched-"):].rsplit("-", 1)[0]
    for line in open(f):
        if not line.strip().startswith("{"):
            continue
        d = json.loads(line)
        if "pl" in d:
            bb.setdefault((v, d["pl"]), []).append(d["speed_tg"])
if bb:
    print("| sequences | -tb 4 | default (-tb = -t) | -tb 4 vs default |")
    print("|---|---:|---:|---:|")
    for n in sorted({k[1] for k in bb}):
        a, b = bb.get(("tb4", n)), bb.get(("default", n))
        if a and b:
            print(f"| {n} | {st.mean(a):.2f} ({', '.join(f'{x:.2f}' for x in a)}) | {st.mean(b):.2f} "
                  f"({', '.join(f'{x:.2f}' for x in b)}) | **{(st.mean(a) / st.mean(b) - 1) * 100:+.1f}%** |")
    print()

rows = list(csv.DictReader(open(f"{RUN}/depth.csv"))) if os.path.exists(f"{RUN}/depth.csv") else []
EXCLUDE = ("swap", "majflt", "other_load", "gpu_mhz", "gpu_counters", "reuse")   # cpu_mhz dips are kept
use = [r for r in rows if not any(k in r["flags"] for k in EXCLUDE)]
print(f"## 2. Context depth ({len(rows)} requests, {len(rows) - len(use)} left out for flags)\n")
variants = sorted({r["variant"] for r in rows}, key=lambda v: (v.startswith("q6"), v != "q4-none", v))
buckets = sorted({int(r["bucket_k"]) for r in rows})


def m(xs):
    xs = [float(x) for x in xs if x not in ("", None)]
    return st.mean(xs) if xs else None


for sampling in ("greedy", "sampled"):
    for task in ("copy", "prose"):
        print(f"### decode tok/s — {task}, {sampling} (mean over prompts and rounds; change vs q4-none)\n")
        print("| variant | " + " | ".join(f"{b}K" for b in buckets) + " | all |")
        print("|---|" + "---:|" * (len(buckets) + 1))
        base = {}
        for v in variants:
            cells = []
            for b in buckets + ["all"]:
                sel = [r for r in use if r["variant"] == v and r["task"] == task and r["sampling"] == sampling
                       and (b == "all" or int(r["bucket_k"]) == b)]
                x = m(r["decode_tok_s"] for r in sel)
                if v == "q4-none":
                    base[b] = x
                ref = base.get(b)
                cells.append("-" if x is None else f"{x:.2f}" + ("" if v == "q4-none" or not ref else f" ({(x / ref - 1) * 100:+.0f}%)"))
            print(f"| {v} | " + " | ".join(cells) + " |")
        print()

print("### prefill tok/s (full prompt read: copy greedy) and time to first token (s)\n")
print("| variant | " + " | ".join(f"{b}K prefill / TTFT" for b in buckets) + " |")
print("|---|" + "---:|" * len(buckets))
for v in variants:
    cells = []
    for b in buckets:
        sel = [r for r in use if r["variant"] == v and r["task"] == "copy" and r["sampling"] == "greedy" and int(r["bucket_k"]) == b]
        p, t = m(r["prefill_tok_s"] for r in sel), m(r["ttft_s"] for r in sel)
        cells.append("-" if p is None else f"{p:.0f} / {t:.1f}")
    print(f"| {v} | " + " | ".join(cells) + " |")
print()
print("### MTP acceptance (accepted / offered drafts), by task and sampling\n")
print("| variant | copy greedy | copy sampled | prose greedy | prose sampled |")
print("|---|---:|---:|---:|---:|")
for v in variants:
    if v == "q4-none":
        continue
    cells = []
    for task in ("copy", "prose"):
        for sampling in ("greedy", "sampled"):
            sel = [r for r in use if r["variant"] == v and r["task"] == task and r["sampling"] == sampling]
            dn, da = sum(int(r["draft_n"]) for r in sel), sum(int(r["draft_acc"]) for r in sel)
            cells.append(f"{da / dn:.2f}" if dn else "-")
    print(f"| {v} | " + " | ".join(cells) + " |")
for f in ("hostprofile-start.txt", "hostprofile-end.txt"):
    if os.path.exists(f"{RUN}/{f}"):
        print(f"\n{f}: " + " ".join(l for l in open(f"{RUN}/{f}").read().split("\n") if l.startswith(("boost", "core_clock", "OK", "MISMATCH"))))
