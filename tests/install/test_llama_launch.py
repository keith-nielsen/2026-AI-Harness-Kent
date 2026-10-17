"""The kent-llama launcher reproduces the operator's measured llama-server configuration
(llamaserver-qwen36-optimum.sh) from llama.env, and rejects settings it was not measured for."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "install" / "services" / "llama" / "bin" / "kent_llama_launch.py"
spec = importlib.util.spec_from_file_location("kent_llama_launch", SRC)
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)


def opt(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_defaults_are_the_production_profile():
    # Operator decision 2026-10-16: Q4 MTP, 128k, K q8_0 / V q4_0, MTP 2 drafts p_min 0 (kvcheck-10061119).
    a = L.build_argv({})
    assert a[0] == L.BIN
    assert opt(a, "-m").endswith("/Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf")
    assert opt(a, "-c") == "131072" and opt(a, "-ncmoe") == "40" and opt(a, "-ub") == "1024"
    assert opt(a, "-ctk") == "q8_0" and opt(a, "-ctv") == "q4_0"
    assert opt(a, "--alias") == "locally-run-model"
    assert opt(a, "-lm") == "none" and "--no-mmap" not in a and "--no-mmproj-offload" in a


def test_mtp_two_drafts_no_threshold_with_an_f16_draft_cache():
    a = L.build_argv({})
    assert opt(a, "--spec-type") == "draft-mtp"
    assert opt(a, "--spec-draft-n-max") == "2" and opt(a, "--spec-draft-p-min") == "0"
    assert "-ctkd" not in a and "-ctvd" not in a
    assert opt(L.build_argv({"MODEL_SET": "uc"}), "--spec-type") == "none"   # no MTP head


def test_two_slots_share_one_kv_cache_and_log_errors_only():
    a = L.build_argv({})
    assert opt(a, "--parallel") == "2" and "-kvu" in a and opt(a, "-lv") == "1"
    assert opt(a, "-t") == "5" and "-tb" not in a   # batch threads default to -t (tbcheck 2026-10-05)


def test_listens_on_loopback_only():
    a = L.build_argv({})
    assert opt(a, "--host") == "127.0.0.1" and opt(a, "--port") == "8080"


def test_models_come_from_the_read_only_mount():
    model, mmproj = L.model_files({})
    assert model.startswith("/srv/kent/models/") and mmproj.startswith("/srv/kent/models/")


def test_non_thinking_is_pinned_by_default():
    a = L.build_argv({})
    assert opt(a, "-rea") == "off" and opt(a, "--temp") == "0.7" and opt(a, "--top-p") == "0.80"
    t = L.build_argv({"THINK": "1"})
    assert opt(t, "-rea") == "on" and opt(t, "--temp") == "1.0" and opt(t, "--presence-penalty") == "1.5"


def test_each_model_set_uses_its_own_ncmoe():
    uc = L.build_argv({"MODEL_SET": "uc"})
    assert opt(uc, "-ncmoe") == "36" and opt(uc, "-ub") == "1024" and "Uncensored" in opt(uc, "-m")


def test_256k_is_retired():
    with pytest.raises(L.ConfigError):
        L.build_argv({"CTX": "256k"})


def test_overrides_and_pinning():
    a = L.build_argv({"NCMOE": "38", "UB": "2048", "PIN": "1", "CORES": "4-7"})
    assert opt(a, "-ncmoe") == "38" and opt(a, "-ub") == "2048"
    assert a[:3] == ["/usr/bin/taskset", "-c", "4-7"] and a[3] == L.BIN


@pytest.mark.parametrize("env", [{"MODEL_SET": "x"}, {"CTX": "64k"}, {"THINK": "2"}, {"UB": "1k"},
                                 {"NCMOE": "-1"}, {"PIN": "1", "CORES": "1;rm"}])
def test_rejects_invalid_settings(env):
    with pytest.raises(L.ConfigError):
        L.build_argv(env)


def test_empty_values_fall_back_to_defaults():
    assert L.build_argv({"UB": "", "NCMOE": "", "MODEL_SET": "", "CTX": ""}) == L.build_argv({})
