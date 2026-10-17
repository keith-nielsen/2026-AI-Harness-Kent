"""Rasch (1-parameter IRT) model for the bench's difficulty ladder.

P(model m passes item i) = 1 / (1 + exp(-(theta_m - b_i)))
  theta_m: the model's ability, per category (T, I, R, H)
  b_i:     the item's difficulty, on the same scale

calibrate(): fits b_i from pilot/full results of several models (and theta for each model) by
maximum a posteriori estimation, with each item's designed level as the prior mean (L1 -2, L2 -0.5,
L3 1, L4 2.5, L5 4; prior sd 1.2) so items every model passed or failed still get a sensible place.
Items above every calibrated model keep mostly their prior until a stronger model is run.

Adaptive draft (CAT): per category, start at theta 0 (the middle of the ladder), give the unasked
item whose difficulty is closest to the current estimate, update theta (MAP, prior N(0, 1.5)),
and stop after MAX_ITEMS or when the standard error is below SE_STOP. This is the robust form of
"start at #50; pass -> #75, fail -> #25; then narrow in": one lucky or unlucky answer moves the
estimate a little instead of sending the search down the wrong half.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

LEVEL_PRIOR = {1: -2.0, 2: -0.5, 3: 1.0, 4: 2.5, 5: 4.0}
B_PRIOR_SD = 1.2
THETA_PRIOR_SD = 1.5
MAX_ITEMS = 8
SE_STOP = 0.55


def _p(theta: float, b: float) -> float:
    return 1.0 / (1.0 + math.exp(-(theta - b)))


def estimate_theta(answers: list[tuple[float, bool]], prior_mean: float = 0.0,
                   prior_sd: float = THETA_PRIOR_SD) -> tuple[float, float]:
    """MAP ability from (difficulty, passed) pairs; returns (theta, standard error)."""
    th = prior_mean
    for _ in range(50):
        g = -(th - prior_mean) / prior_sd ** 2
        h = -1.0 / prior_sd ** 2
        for b, ok in answers:
            p = _p(th, b)
            g += (1.0 if ok else 0.0) - p
            h -= p * (1 - p)
        step = g / h
        th -= max(-1.0, min(1.0, step))
        if abs(step) < 1e-6:
            break
    return th, math.sqrt(-1.0 / h)


def calibrate(results: list[dict], items: dict[str, dict], iters: int = 200) -> dict:
    """results: rows with id, pass, and 'model'; items: id -> {"cat", "level"}.
    Returns {"b": {id: difficulty}, "theta": {model: {cat: ability}}}."""
    b = {i: LEVEL_PRIOR[it["level"]] for i, it in items.items()}
    obs = defaultdict(list)                       # (model, cat) -> [(item, passed)]
    for r in results:
        if r["id"] in items:
            obs[(r["model"], items[r["id"]]["cat"])].append((r["id"], bool(r["pass"])))
    theta = {k: 0.0 for k in obs}
    by_item = defaultdict(list)                   # item -> [((model, cat), passed)]
    for k, rows in obs.items():
        for i, ok in rows:
            by_item[i].append((k, ok))
    for _ in range(iters):
        for k, rows in obs.items():               # abilities given difficulties
            theta[k], _ = estimate_theta([(b[i], ok) for i, ok in rows])
        for i, rows in by_item.items():           # difficulties given abilities (MAP, level prior)
            mu = LEVEL_PRIOR[items[i]["level"]]
            x = b[i]
            for _ in range(20):
                g = -(x - mu) / B_PRIOR_SD ** 2
                h = -1.0 / B_PRIOR_SD ** 2
                for k, ok in rows:
                    p = _p(theta[k], x)
                    g += p - (1.0 if ok else 0.0)
                    h -= p * (1 - p)
                step = g / h
                x -= max(-1.0, min(1.0, step))
                if abs(step) < 1e-6:
                    break
            b[i] = x
        mean_b = sum(b.values()) / len(b)         # anchor the scale: designed level priors keep their meaning
        shift = mean_b - sum(LEVEL_PRIOR[it["level"]] for it in items.values()) / len(items)
        b = {i: v - shift for i, v in b.items()}
        theta = {k: v - shift for k, v in theta.items()}
    out = defaultdict(dict)
    for (m, c), v in theta.items():
        out[m][c] = round(v, 2)
    return {"b": {i: round(v, 3) for i, v in b.items()}, "theta": dict(out)}


class Adaptive:
    """One category's adaptive search."""

    def __init__(self, cat: str, difficulties: dict[str, float], start: float = 0.0):
        self.cat, self.b, self.answers, self.asked = cat, difficulties, [], []
        self.theta, self.se = start, THETA_PRIOR_SD

    def next_item(self) -> str | None:
        if len(self.asked) >= MAX_ITEMS or (len(self.asked) >= 3 and self.se < SE_STOP):
            return None
        left = [i for i in self.b if i not in self.asked]
        return min(left, key=lambda i: abs(self.b[i] - self.theta)) if left else None

    def record(self, item: str, passed: bool):
        self.asked.append(item)
        self.answers.append((self.b[item], passed))
        self.theta, self.se = estimate_theta(self.answers)

    def state(self) -> dict:
        return {"cat": self.cat, "theta": round(self.theta, 2), "se": round(self.se, 2), "asked": self.asked,
                "answers": [ok for _, ok in self.answers]}


def save(path, cal: dict):
    with open(path, "w") as f:
        json.dump(cal, f, indent=1, sort_keys=True)
