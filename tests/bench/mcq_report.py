#!/usr/bin/env python3
"""mcq_report.py RUN_DIR — per-question results, accuracies and paired tests from mcqcheck.sh logs.

llama-perplexity prints one line per question: multiple-choice "<n>\\t<running accuracy %>", Winogrande
"<n>\\t<running acc>\\t<score1> <score2> <result> <answer>". The question order is the same for every model (fixed seed),
so question n is the same question in every log and models can be compared pair by pair (exact McNemar test on the
questions where exactly one model is right). A log without llama-perplexity's final score line (crash, refused task) is
listed as INCOMPLETE and left out of every table; the per-question total is checked against the final score.
Writes RUN_DIR/per_item.csv and prints the report (markdown) to stdout.
"""
import csv
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

run = Path(sys.argv[1])
MC = re.compile(r"^(\d+)\t(\d+\.\d+)$")
FINAL = re.compile(r"Final (?:result|Winogrande score)[^:]*: (\d+\.\d+)")
WG = re.compile(r"^(\d+)\t(\d+\.\d+)\t\s*-?\d+\.\d+\s+-?\d+\.\d+\s+(\d)\s+(\d)$")


def per_item(path: Path) -> list[int]:
    """1/0 per question, in order."""
    out, prev = [], 0
    for line in path.read_text(errors="replace").splitlines():
        m = WG.match(line)
        if m:
            out.append(int(m.group(3) == m.group(4)))
            continue
        m = MC.match(line)
        if m:
            n, acc = int(m.group(1)), float(m.group(2))
            if n != len(out) + 1:
                continue
            correct = round(acc * n / 100)
            out.append(correct - prev)
            prev = correct
    return out


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def mcnemar_p(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


res = defaultdict(dict)                           # set -> model -> [0/1]
bad = []                                          # (set, model, reason)
for f in sorted(f for f in run.glob("*-q*.log") if re.fullmatch(r".+-q\w+", f.stem)):   # q4, q6, q4kv84, ...
    s, m = f.stem.rsplit("-", 1)
    items = per_item(f)
    fin = FINAL.search(f.read_text(errors="replace"))
    if not fin:
        err = next((ln.split(" E ", 1)[1] for ln in f.read_text(errors="replace").splitlines() if " E " in ln), "")
        bad.append((s, m, f"no final score after {len(items)} questions{': ' + err.strip() if err else ''}"))
        continue
    if not items or abs(100 * sum(items) / len(items) - float(fin.group(1))) > 0.01:
        bad.append((s, m, f"per-question total {sum(items)}/{len(items)} does not match final score {fin.group(1)}"))
        continue
    res[s][m] = items

with open(run / "per_item.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["set", "item", *sorted({m for v in res.values() for m in v})])
    models = sorted({m for v in res.values() for m in v})
    for s, bym in sorted(res.items()):
        n = max(len(v) for v in bym.values())
        for i in range(n):
            w.writerow([s, i + 1, *[(bym[m][i] if m in bym and i < len(bym[m]) else "") for m in models]])

print(f"# Multiple-choice scoring: {run.name}\n")
print("| set | model | questions | correct | accuracy % | 95% interval |")
print("|---|---|---:|---:|---:|---|")
for s, bym in sorted(res.items()):
    for m, v in sorted(bym.items()):
        lo, hi = wilson(sum(v), len(v))
        print(f"| {s} | {m} | {len(v)} | {sum(v)} | {100 * sum(v) / len(v):.2f} | {lo:.2f}-{hi:.2f} |")
if bad:
    print("\n**INCOMPLETE (left out):**\n")
    for s, m, why in bad:
        print(f"- {s} {m}: {why}")
print("\n## Paired comparisons (same questions; McNemar exact test on the questions where exactly one is right)\n")
print("| set | A vs B | questions | A right only | B right only | difference (points) | p |")
print("|---|---|---:|---:|---:|---:|---:|")
for s, bym in sorted(res.items()):
    ms = sorted(bym)
    for i, a in enumerate(ms):
        for b in ms[i + 1:]:
            n = min(len(bym[a]), len(bym[b]))
            if not n:
                continue
            ao = sum(1 for x, y in zip(bym[a][:n], bym[b][:n]) if x and not y)
            bo = sum(1 for x, y in zip(bym[a][:n], bym[b][:n]) if y and not x)
            print(f"| {s} | {a} vs {b} | {n} | {ao} | {bo} | {100 * (ao - bo) / n:+.2f} | {mcnemar_p(ao, bo):.4f} |")
