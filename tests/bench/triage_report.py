#!/usr/bin/env python3
"""triage_report.py — can logprob triage set the low-water / high-water marks? (classifier redesign, task 3)

    python3 tests/bench/triage_report.py TRIAGE_RUN_DIR LABELS.json [--alpha 0.05 --delta 0.10]

Joins triagecheck.py output (TRIAGE_RUN_DIR/triage.jsonl) with labels.py rows. Each variant's answer distribution
gives an easy score (high = thinking off is safe) and a hard score (high = 4096 helps). Reports:
- separation: AUROC per variant (bootstrap 95% CI), easy target = easy_safe on all probes, hard target = helps_4k
  on probes with >= 3 repeats at both 2048 and 4096; within-category AUROC for the easy target;
- low-water mark, held out by category and by template: the variant and threshold are chosen on the other groups
  ("zero-miss" = the lowest threshold with no false easy in training; "certified" = Clopper-Pearson upper bound of
  the false-easy rate <= alpha at confidence 1-delta), then applied to the held-out group; pooled false-easy count,
  share of easy-safe probes caught, pass/time vs always 2048;
- high-water mark, held out by category: top share q of training hard scores -> 4096; pass/time vs always 2048
  and vs sending the same share at random.
Numeric noise: the repeat of the first variant (NAME#2) against the original.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

YES, NO = {"y", "yes"}, {"n", "no"}


def dist(row: dict) -> tuple[dict[str, float], float]:
    opts = row["options"]
    p = {o: 0.0 for o in opts}
    for tok, lp in row["top"]:
        t = tok.strip()
        k = ("Y" if t.lower() in YES else "N" if t.lower() in NO else None) if opts == "YN" else t
        if k in p:
            p[k] += math.exp(lp)
    mass = sum(p.values())
    return ({o: v / mass for o, v in p.items()} if mass else {o: 1 / len(opts) for o in opts}), mass


def expect(p: dict[str, float]) -> float:
    return sum(i * v for i, v in enumerate(p.values()))


# variant -> (easy score, hard score) from the answer distribution
SCORES = {
    "effort4": (lambda p: p["0"], expect), "effort4d": (lambda p: p["0"], expect),
    "steps": (lambda p: p["0"], expect),
    "diff15": (lambda p: -expect(p), expect), "conf09": (expect, lambda p: -expect(p)),
    "direct_ok": (lambda p: p["Y"], lambda p: p["N"]), "risk": (lambda p: p["N"], lambda p: p["Y"]),
    "long_helps": (lambda p: p["N"], lambda p: p["Y"]),
}


def auroc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))


def boot_ci(scores, labels, n=2000, seed=1):
    rng, idx, out = random.Random(seed), range(len(scores)), []
    for _ in range(n):
        s = [rng.choice(idx) for _ in idx]
        a = auroc([scores[i] for i in s], [labels[i] for i in s])
        if a is not None:
            out.append(a)
    out.sort()
    return (out[int(0.025 * len(out))], out[int(0.975 * len(out)) - 1]) if out else (None, None)


def fmt_auc(scores, labels):
    a = auroc(scores, labels)
    if a is None:
        return "- (one class)"
    lo, hi = boot_ci(scores, labels)
    return f"{a:.2f} [{lo:.2f}, {hi:.2f}]"


def binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def cp_upper(k: int, n: int, delta: float) -> float:
    """One-sided Clopper-Pearson upper bound: P(rate <= bound) >= 1 - delta after k failures in n."""
    if n == 0 or k >= n:
        return 1.0
    lo, hi = k / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if binom_cdf(k, n, mid) > delta else (lo, mid)
    return hi


def ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 / max(1, len(xs) - 1)
        i = j + 1
    return r


def load(run: Path, labels: Path):
    lab = {r["id"]: r for r in json.loads(labels.read_text())["rows"]}
    tri = defaultdict(dict)
    for line in (run / "triage.jsonl").read_text().splitlines():
        r = json.loads(line)
        tri[r["id"]][r["variant"]] = r
    ids = sorted(i for i in tri if i in lab)
    base = [v for v in SCORES if all(v in tri[i] for i in ids)]
    easy, hard, mass = {}, {}, defaultdict(list)
    for v in base:
        easy[v], hard[v] = [], []
        for i in ids:
            p, m = dist(tri[i][v])
            mass[v].append(m)
            easy[v].append(SCORES[v][0](p))
            hard[v].append(SCORES[v][1](p))
    if len(base) > 1:                                    # ensemble: mean rank over the variants
        easy["mean_rank"] = [statistics.mean(c) for c in zip(*(ranks(easy[v]) for v in base))]
        hard["mean_rank"] = [statistics.mean(c) for c in zip(*(ranks(hard[v]) for v in base))]
    return lab, tri, ids, base, easy, hard, mass


def pick_variant(score, y, train):
    best = None
    for v, s in score.items():
        a = auroc([s[i] for i in train], [y[i] for i in train])
        if a is not None and (best is None or a > best[1]):
            best = (v, a)
    return best and best[0]                              # None: training has one class only


def low_threshold(s, y, train, rule, alpha, delta):
    """Lowest threshold t (route easy when score >= t) meeting the rule on the training items; None = route nothing."""
    cands = sorted({s[i] for i in train}, reverse=True)
    best = None
    for t in cands:
        routed = [i for i in train if s[i] >= t]
        k = sum(not y[i] for i in routed)
        ok = k == 0 if rule == "zero-miss" else cp_upper(k, len(routed), delta) <= alpha
        if rule == "zero-miss" and not ok:
            break                                        # lower thresholds only add the miss back
        if ok:
            best = t
    return best


def mode(row, m):
    x = row["modes"][m]
    return x["pass_rate"], x["mean_s"]


def low_cv(ids, lab, easy, groups, rule, alpha, delta):
    y = [bool(lab[i]["easy_safe"]) for i in ids]
    routed, k, caught, chosen = 0, 0, 0, []
    pick = {}
    for g in sorted(set(groups)):
        train = [j for j, x in enumerate(groups) if x != g]
        test = [j for j, x in enumerate(groups) if x == g]
        v = pick_variant(easy, y, train)
        t = v and low_threshold(easy[v], y, train, rule, alpha, delta)
        chosen.append(f"{g}:{v}@{'none' if t is None else f'{t:.3g}'}")
        for j in test:
            off = t is not None and easy[v][j] >= t
            pick[j] = "off" if off else "2048"
            if off:
                routed += 1; k += not y[j]; caught += y[j]
    pas = statistics.mean(mode(lab[ids[j]], pick[j])[0] for j in pick)
    sec = statistics.mean(mode(lab[ids[j]], pick[j])[1] for j in pick)
    return {"routed": routed, "false_easy": k, "caught": caught, "easy_total": sum(y), "pass": pas, "s": sec,
            "chosen": chosen, "cp": cp_upper(k, routed, delta)}


def high_cv(ids, lab, hard, q, seed=1):
    sub = [j for j, i in enumerate(ids) if lab[i]["modes"].get("2048", {}).get("n", 0) >= 3
           and lab[i]["modes"].get("4096", {}).get("n", 0) >= 3]
    y = {j: bool(lab[ids[j]]["helps_4k"]) for j in sub}
    pick = {}
    for g in sorted({lab[ids[j]]["cat"] for j in sub}):
        train = [j for j in sub if lab[ids[j]]["cat"] != g]
        test = [j for j in sub if lab[ids[j]]["cat"] == g]
        v = pick_variant(hard, y, train) or next(iter(hard))
        s = sorted((hard[v][j] for j in train), reverse=True)
        t = s[max(0, round(q * len(s)) - 1)] if q > 0 else math.inf
        for j in test:
            pick[j] = "4096" if hard[v][j] >= t else "2048"
    sent = sum(m == "4096" for m in pick.values())
    pas = statistics.mean(mode(lab[ids[j]], pick[j])[0] for j in pick)
    sec = statistics.mean(mode(lab[ids[j]], pick[j])[1] for j in pick)
    rng, rp, rs = random.Random(seed), [], []                # same number sent at random, 2000 draws
    for _ in range(2000):
        r = set(rng.sample(sub, sent))
        rp.append(statistics.mean(mode(lab[ids[j]], "4096" if j in r else "2048")[0] for j in sub))
        rs.append(statistics.mean(mode(lab[ids[j]], "4096" if j in r else "2048")[1] for j in sub))
    base = [mode(lab[ids[j]], "2048") for j in sub]
    return {"n": len(sub), "pos": sum(y.values()), "sent": sent, "pass": pas, "s": sec,
            "rand_pass": statistics.mean(rp), "rand_s": statistics.mean(rs),
            "base_pass": statistics.mean(p for p, _ in base), "base_s": statistics.mean(s for _, s in base)}


def report(run: Path, labels: Path, alpha: float, delta: float) -> str:
    lab, tri, ids, base, easy, hard, mass = load(run, labels)
    ye = [bool(lab[i]["easy_safe"]) for i in ids]
    hsub = [j for j, i in enumerate(ids) if lab[i]["helps_4k"] is not None
            and lab[i]["modes"]["2048"]["n"] >= 3 and lab[i]["modes"]["4096"]["n"] >= 3]
    yh = [bool(lab[ids[j]]["helps_4k"]) for j in hsub]
    out = [f"# Triage variants vs labels ({run.name})", "",
           f"{len(ids)} probes, {len(base)} variants; easy_safe {sum(ye)}/{len(ye)}; helps_4k {sum(yh)}/{len(yh)} "
           f"(probes with >= 3 repeats at 2048 and 4096).", "",
           "## Separation (AUROC, 0.5 = chance; 95% bootstrap CI)", "",
           "| variant | easy_safe | hard: helps_4k | answer mass min / median |", "|---|---|---|---|"]
    for v in list(easy):
        sh = [hard[v][j] for j in hsub]
        m = f"{min(mass[v]):.3f} / {statistics.median(mass[v]):.3f}" if v in mass else "-"
        out.append(f"| {v} | {fmt_auc(easy[v], ye)} | {fmt_auc(sh, yh)} | {m} |")
    cats = sorted({lab[i]["cat"] for i in ids})
    out += ["", "## Easy target within each category (AUROC; n = probes, easy-safe)", "",
            "| variant | " + " | ".join(cats) + " |", "|---|" + "---|" * len(cats)]
    cat_n = {c: (sum(lab[i]["cat"] == c for i in ids), sum(ye[j] for j, i in enumerate(ids) if lab[i]["cat"] == c))
             for c in cats}
    out.append("| n | " + " | ".join(f"{n} / {e}" for n, e in cat_n.values()) + " |")
    for v in easy:
        cells = []
        for c in cats:
            js = [j for j, i in enumerate(ids) if lab[i]["cat"] == c]
            a = auroc([easy[v][j] for j in js], [ye[j] for j in js])
            cells.append("-" if a is None else f"{a:.2f}")
        out.append(f"| {v} | " + " | ".join(cells) + " |")
    b2 = (statistics.mean(mode(lab[i], "2048")[0] for i in ids), statistics.mean(mode(lab[i], "2048")[1] for i in ids))
    out += ["", f"## Low-water mark (thinking off), held out; always 2048 = {100*b2[0]:.1f}% / {b2[1]:.0f} s per item", "",
            f"Certified = Clopper-Pearson upper bound of false-easy <= {alpha:.0%} at {1-delta:.0%} confidence on the "
            "training groups. Pooled CP = the same bound on the pooled held-out counts.", "",
            "| held out by | rule | routed off | false easy | easy-safe caught | pass | s/item | pooled CP bound |",
            "|---|---|---:|---:|---:|---:|---:|---:|"]
    groups = {"category": [lab[i]["cat"] for i in ids], "template": [lab[i]["template"] for i in ids]}
    chosen = []
    for gname, g in groups.items():
        for rule in ("zero-miss", "certified"):
            r = low_cv(ids, lab, easy, g, rule, alpha, delta)
            out.append(f"| {gname} | {rule} | {r['routed']} | {r['false_easy']} | {r['caught']}/{r['easy_total']} | "
                       f"{100*r['pass']:.1f}% | {r['s']:.0f} | {r['cp']:.1%} |")
            chosen.append(f"- {gname} / {rule}: " + ", ".join(r["chosen"]))
    out += ["", "Chosen per held-out group (variant@threshold):", *chosen]
    out += ["", "## High-water mark (4096), held out by category, on the helps_4k probes", "",
            "| share sent (training) | sent | pass | s/item | random, same count: pass | s/item |",
            "|---|---:|---:|---:|---:|---:|"]
    for q in (0.0, 0.15, 0.3, 0.5, 1.0):
        r = high_cv(ids, lab, hard, q)
        if q == 0:
            out.insert(len(out) - 2, f"{r['n']} probes, {r['pos']} where 4096 helps; always 2048 = "
                                     f"{100*r['base_pass']:.1f}% / {r['base_s']:.0f} s.\n")
        out.append(f"| {q:.0%} | {r['sent']} | {100*r['pass']:.1f}% | {r['s']:.0f} | {100*r['rand_pass']:.1f}% | "
                   f"{r['rand_s']:.0f} |")
    reps = [v for v in {r for i in ids for r in tri[i]} if "#" in v]
    for rv in reps:
        v = rv.split("#")[0]
        d = [max(abs(a - b) for a, b in zip(dist(tri[i][v])[0].values(), dist(tri[i][rv])[0].values()))
             for i in ids if rv in tri[i] and v in tri[i]]
        out += ["", f"Noise ({rv} vs {v}, {len(d)} probes): max |dp| median {statistics.median(d):.4f}, "
                    f"max {max(d):.4f}."]
    secs = [r["seconds"] for i in ids for r in tri[i].values()]
    out += [f"Triage call time: median {statistics.median(secs):.2f} s, max {max(secs):.1f} s (first variant per "
            "probe pays the prefill)."]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("labels", type=Path)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--delta", type=float, default=0.10)
    a = ap.parse_args()
    print(report(a.run, a.labels, a.alpha, a.delta))


if __name__ == "__main__":
    main()
