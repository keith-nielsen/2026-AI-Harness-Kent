"""triage_report.py: Clopper-Pearson sample sizes (card table), AUROC, answer distributions, the low-water
threshold rules and the held-out evaluation on a synthetic run."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import triage_report as tr  # noqa: E402


def test_cp_sample_sizes_match_card():
    need = [next(n for n in range(k + 1, 1000) if tr.cp_upper(k, n, 0.10) <= 0.05) for k in range(6)]
    assert need == [45, 77, 105, 132, 158, 184]
    assert tr.cp_upper(0, 0, 0.1) == 1.0


def test_auroc_ties_and_degenerate():
    assert tr.auroc([3, 2, 1], [True, False, False]) == 1.0
    assert tr.auroc([1, 1], [True, False]) == 0.5
    assert tr.auroc([1, 2], [True, True]) is None


def test_dist_folds_yes_no_spellings_and_normalises():
    p, mass = tr.dist({"options": "YN", "top": [[" Yes", math.log(0.6)], ["Y", math.log(0.2)], ["no", math.log(0.1)],
                                                ["<tool_call>", math.log(0.1)]]})
    assert abs(mass - 0.9) < 1e-9 and abs(p["Y"] - 0.8 / 0.9) < 1e-9
    p, _ = tr.dist({"options": "0123", "top": [["1", 0.0]]})
    assert p == {"0": 0.0, "1": 1.0, "2": 0.0, "3": 0.0} and tr.expect(p) == 1.0


def test_low_threshold_rules():
    s = [0.9, 0.8, 0.7, 0.6, 0.5]
    y = [True, True, False, True, True]
    tr_ = list(range(5))
    assert tr.low_threshold(s, y, tr_, "zero-miss", 0.05, 0.1) == 0.8          # stops above the first miss
    assert tr.low_threshold(s, y, tr_, "certified", 0.05, 0.1) is None          # 5 items can never certify 5%
    assert tr.low_threshold([0.1] * 50, [True] * 50, list(range(50)), "certified", 0.05, 0.1) == 0.1


def labels_row(i, cat, easy_safe, helps, template="gent"):
    m = {"off": {"n": 3, "pass_rate": 1.0 if easy_safe else 0.0, "mean_s": 5},
         "2048": {"n": 3, "pass_rate": 1.0 if easy_safe else 0.5, "mean_s": 30},
         "4096": {"n": 3, "pass_rate": 1.0 if (easy_safe or helps) else 0.5, "mean_s": 60}}
    return {"id": i, "cat": cat, "template": template, "modes": m, "easy_safe": easy_safe, "helps_4k": helps}


def test_report_end_to_end_on_a_perfect_variant(tmp_path):
    rows, tri = [], []
    for n in range(40):
        i, cat = f"x{n:02d}", "TIHR"[n % 4]
        es, h = n < 24, n >= 34
        rows.append(labels_row(i, cat, es, h if not es else False, "gent" if n % 3 else "hermes"))
        d = "0" if es else ("3" if h else "2")
        tri.append({"id": i, "variant": "effort4", "options": "0123", "top": [[d, 0.0]], "seconds": 0.5})
        tri.append({"id": i, "variant": "effort4#2", "options": "0123", "top": [[d, 0.0]], "seconds": 0.5})
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "triage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in tri))
    (tmp_path / "labels.json").write_text(json.dumps({"rows": rows}))
    out = tr.report(tmp_path / "run", tmp_path / "labels.json", 0.05, 0.10)
    assert "| effort4 | 1.00" in out
    assert "| category | zero-miss | 24 | 0 | 24/24 |" in out
    assert "max |dp| median 0.0000" in out
