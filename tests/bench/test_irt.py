"""IRT sanity on simulated models: calibration recovers the difficulty order, and the adaptive search
lands near a simulated model's true ability with far fewer items than the full ladder."""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import irt  # noqa: E402


def sim(theta, b, rng):
    return rng.random() < irt._p(theta, b)


def test_calibration_recovers_order_and_ability_ranking():
    rng = random.Random(7)
    true_b = {f"q{i:02d}": -3 + 6 * i / 39 for i in range(40)}
    items = {i: {"cat": "T", "level": 1 + min(3, int((b + 3) / 1.6))} for i, b in true_b.items()}
    models = {"weak": -1.0, "mid": 0.5, "strong": 2.0}
    rows = [{"id": i, "model": m, "pass": sim(t, b, rng)} for m, t in models.items() for i, b in true_b.items()
            for _ in range(3)]
    cal = irt.calibrate(rows, items)
    th = {m: cal["theta"][m]["T"] for m in models}
    assert th["weak"] < th["mid"] < th["strong"]
    easy = [cal["b"][f"q{i:02d}"] for i in range(10)]
    hard = [cal["b"][f"q{i:02d}"] for i in range(30, 40)]
    assert sum(easy) / 10 < sum(hard) / 10 - 2


def test_adaptive_search_converges_with_few_items():
    b = {f"q{i:03d}": -3 + 6 * i / 99 for i in range(100)}
    for true in (-1.5, 0.0, 1.8):
        errs = []
        for seed in range(40):
            rng = random.Random(seed)
            a = irt.Adaptive("T", b)
            while (i := a.next_item()) is not None:
                a.record(i, sim(true, b[i], rng))
            assert len(a.asked) <= irt.MAX_ITEMS
            errs.append(abs(a.theta - true))
        assert sum(errs) / len(errs) < 0.9, (true, sum(errs) / len(errs))


def test_first_item_is_mid_ladder_and_moves_with_answers():
    b = {f"q{i:03d}": -3 + 6 * i / 99 for i in range(100)}
    a = irt.Adaptive("T", b)
    first = a.next_item()
    assert abs(b[first]) < 0.1                       # starts at "#50"
    a.record(first, True)
    assert b[a.next_item()] > b[first]               # pass -> harder
    a2 = irt.Adaptive("T", b); f2 = a2.next_item(); a2.record(f2, False)
    assert b[a2.next_item()] < b[f2]                 # fail -> easier
