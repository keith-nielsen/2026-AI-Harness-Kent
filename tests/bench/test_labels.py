"""labels.py: pooling across runs, label rules, setup refusal, and the stored BUDGET CURVE 115 tables
(bench/runs/*/sim115.md) reproduced from the label rows."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import labels  # noqa: E402

RUNS = Path.home() / "Documents/repo/bench/runs"
MAN = {"model_sha256": "abc", "llama_build": ["version: x"], "bench_hash": "b1", "suite": "probes",
       "kent_bench": "bench on; Kent state s1", "tier": "full", "harness_code_hash": "h"}


def arm(run: Path, mode: str, rows: list[dict], **man) -> None:
    d = run / f"probes-kv-q8q4-think{mode}"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({**MAN, **man}))
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def r(id, rep, ok, s=10.0, toks=50):
    return {"id": id, "rep": rep, "pass": ok, "seconds": s, "completion_tokens": toks, "prompt_tokens": 1000}


def test_pools_runs_and_applies_label_rules(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    arm(a, "off", [r("a01", 0, True), r("a06", 0, True)])
    arm(b, "off", [r("a01", 0, True), r("a06", 0, False)])
    arm(a, "2048", [r("a01", 0, True), r("a06", 0, False, toks=2048)])
    arm(a, "4096", [r("a01", 0, True), r("a06", 0, True, s=None)])
    lab = labels.build([a, b], "kv-q8q4")
    row = {x["id"]: x for x in lab["rows"]}
    assert row["a01"]["easy_safe"] is True and row["a01"]["helps_4k"] is False
    assert row["a06"]["easy_safe"] is False and row["a06"]["helps_4k"] is True
    assert row["a06"]["modes"]["off"] == {"n": 2, "passes": 1, "pass_rate": 0.5, "mean_s": 10.0,
                                          "median_s": 10.0, "at_budget": 0}
    assert row["a06"]["modes"]["2048"]["at_budget"] == 1
    assert row["a06"]["modes"]["4096"]["mean_s"] == 0       # missing timing counts as 0, as sim115.py did
    assert row["a01"]["cat"] == "R" and row["a01"]["band"] == "a" and row["a01"]["template"] in labels.TEMPLATES
    assert len(lab["sources"]) == 4


def test_missing_modes_give_null_labels_and_rerun_rows_replace(tmp_path):
    arm(tmp_path / "a", "2048", [r("a01", 0, False), r("a01", 0, True)])
    row = labels.build([tmp_path / "a"], "kv-q8q4")["rows"][0]
    assert row["easy_safe"] is None and row["helps_4k"] is None
    assert row["modes"]["2048"]["n"] == 1 and row["modes"]["2048"]["passes"] == 1


@pytest.mark.parametrize("field,value", [("model_sha256", "zzz"), ("bench_hash", "b2"),
                                         ("kent_bench", "bench on; Kent state s2")])
def test_refuses_mixed_setups(tmp_path, field, value):
    arm(tmp_path / "a", "off", [r("a01", 0, True)])
    arm(tmp_path / "b", "off", [r("a01", 0, True)], **{field: value})
    with pytest.raises(SystemExit, match="different setup"):
        labels.build([tmp_path / "a", tmp_path / "b"], "kv-q8q4")


def test_harness_hash_change_is_recorded_not_refused(tmp_path):
    arm(tmp_path / "a", "off", [r("a01", 0, True)])
    arm(tmp_path / "b", "2048", [r("a01", 0, True)], harness_code_hash="other")
    lab = labels.build([tmp_path / "a", tmp_path / "b"], "kv-q8q4")
    assert {s["harness_code_hash"] for s in lab["sources"]} == {"h", "other"}


def test_unknown_item_refused(tmp_path):
    arm(tmp_path / "a", "off", [r("zz9", 0, True)])
    with pytest.raises(SystemExit, match="zz9"):
        labels.build([tmp_path / "a"], "kv-q8q4")


CURVES = {"think-v1x-1017": ["think-v1-1016", "think-easy-1017", "think-1024-1017", "think-4096-1017",
                             "think-v1x-1017"],
          "think-easy2-1017": ["think-v1-1016", "think-easy-1017", "think-easy2-1017", "think-1024-1017",
                               "think-4096-1017", "think-v1x-1017"]}


@pytest.mark.parametrize("report", sorted(CURVES))
def test_reproduces_stored_budget_curve(report):
    want = RUNS / report / "sim115.md"
    if not want.exists():
        pytest.skip(f"{want} not on this machine")
    lab = labels.build([RUNS / d for d in CURVES[report]], "kv-q8q4")
    assert len(lab["rows"]) == 115
    got = labels.curve(lab).splitlines()[:13]
    assert got == want.read_text().splitlines()[:13]
