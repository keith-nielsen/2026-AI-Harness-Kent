"""Unit tests for the thinking-budget triage in kent_gateway (apply_triage and its parts)."""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REPO_CONFIG = REPO / "configs" / "triage.json"
TRIAGECHECK = Path.home() / "Documents/repo/bench/runs/triagecheck-1017/triage.jsonl"   # private bench data
SHORTQ = Path.home() / "Documents/repo/bench/runs/triagecheck-10181435/triage.jsonl"     # conf09 wordings, 2026-10-18


@pytest.fixture
def cfg_path(gw, tmp_path, monkeypatch):
    p = tmp_path / "triage.json"
    shutil.copy(REPO_CONFIG, p)
    monkeypatch.setattr(gw, "TRIAGE_CONFIG", p)
    gw._triage_cache.update(mtime=None, cfg=None)
    return p


def rewrite(path: Path, **changes) -> None:
    cfg = json.loads(path.read_text())
    cfg.update(changes)
    path.write_text(json.dumps(cfg))


def call(identity="kent", group="auto", routed="default", budget=2048, tools=None, max_tokens=300):
    kw = {"model": "openai/locally-run-model", "api_base": "http://127.0.0.1:8080/v1", "litellm_call_id": "c1",
          "max_tokens": max_tokens, "messages": [{"role": "user", "content": "What is 17*23?"}],
          "metadata": {"user_api_key_alias": identity, "model_group": group,
                       "routing_decision": {"routed_model": routed, "tier": "fast"} if routed else None},
          "extra_body": {"chat_template_kwargs": {"enable_thinking": True}, "thinking_budget_tokens": budget}}
    if tools:
        kw["tools"] = tools
    return kw


def answer(digits: dict[str, float]):
    """A fake triage endpoint whose first token has these probabilities; records what it was sent."""
    sent = []

    async def post(url, body, timeout):
        sent.append((url, body, timeout))
        top = [{"token": t, "logprob": math.log(p)} for t, p in digits.items()]
        return {"choices": [{"message": {"content": next(iter(digits))}, "logprobs": {"content": [
            {"token": next(iter(digits)), "logprob": top[0]["logprob"], "top_logprobs": top}]}}]}
    post.sent = sent
    return post


# --- settings -------------------------------------------------------------------
def test_repo_settings_are_valid_and_kent_only(gw):
    cfg = gw.triage_config(REPO_CONFIG)
    assert cfg and cfg["identities"] == ["kent"]
    assert cfg["model_group"] == "auto" and cfg["routed_model"] == "default"
    assert cfg["high_water"] == {"below": 5.0, "thinking_budget_tokens": 4096} and cfg["low_water"] is None
    assert cfg["options"] == "0123456789" and cfg["timeout_s"] == 30 and cfg["head"] == ""


def test_missing_disabled_or_invalid_settings_turn_triage_off(gw, cfg_path, capsys):
    rewrite(cfg_path, enabled=False)
    assert gw.triage_config() is None
    cfg_path.write_text("{not json")
    assert gw.triage_config() is None
    assert "triage_config_invalid" in capsys.readouterr().out
    cfg_path.unlink()
    assert gw.triage_config() is None


def test_settings_change_is_picked_up_without_restart(gw, cfg_path):
    assert gw.triage_config()["high_water"]["below"] == 5.0
    cfg = json.loads(cfg_path.read_text())
    cfg["high_water"]["below"] = 6.0
    cfg_path.write_text(json.dumps(cfg))
    import os
    os.utime(cfg_path, (1, 1))   # a different mtime even within the same second
    assert gw.triage_config()["high_water"]["below"] == 6.0


# --- scoring --------------------------------------------------------------------
def test_score_is_the_expected_digit_over_the_options(gw):
    top = [{"token": "8", "logprob": math.log(0.6)}, {"token": " 2", "logprob": math.log(0.3)},
           {"token": "Sure", "logprob": math.log(0.1)}]
    score, mass = gw.triage_score(top, "0123456789")
    assert mass == pytest.approx(0.9) and score == pytest.approx((8 * 0.6 + 2 * 0.3) / 0.9)


def test_no_option_tokens_gives_zero_mass(gw):
    assert gw.triage_score([{"token": "Sure", "logprob": -0.1}], "0123456789") == (4.5, 0.0)
    assert gw.triage_score(None, "0123456789")[1] == 0.0


def test_budget_rule(gw):
    cfg = gw.triage_config(REPO_CONFIG)
    assert gw.triage_budget(4.99, 0.9, cfg) == 4096
    assert gw.triage_budget(5.0, 0.9, cfg) is None
    assert gw.triage_budget(1.0, 0.4, cfg) is None          # unusable answer: leave as routed


@pytest.mark.skipif(not TRIAGECHECK.is_file(), reason="private bench data not present")
def test_gateway_scoring_reproduces_the_triagecheck_report(gw):
    """Same scores as tests/bench/triage_report.py on the stored conf09 answers. Of the 34 probes below the
    high-water mark, 33 go to 4096; the one with no usable answer (option mass < 0.5) stays at 2048."""
    sys.path.insert(0, str(REPO / "tests" / "bench"))
    from triage_report import dist, expect
    cfg = gw.triage_config(REPO_CONFIG)
    rows = [r for r in map(json.loads, TRIAGECHECK.open()) if r["variant"] == "conf09"]
    sent = below = 0
    for r in rows:
        top = [{"token": t, "logprob": lp} for t, lp in r["top"]]
        score, mass = gw.triage_score(top, cfg["options"])
        p, m = dist(r)
        assert score == pytest.approx(expect(p)) and mass == pytest.approx(m)
        sent += gw.triage_budget(score, mass, cfg) == 4096
        below += score < cfg["high_water"]["below"]
    assert len(rows) == 115 and below == 34 and sent == 33


# --- apply_triage ---------------------------------------------------------------
async def test_low_confidence_raises_the_budget(gw, cfg_path, capsys):
    kw = call()
    post = answer({"2": 0.9, "3": 0.1})
    rec = await gw.apply_triage(kw, post)
    assert rec["decision"] == "smart" and kw["extra_body"]["thinking_budget_tokens"] == 4096
    assert rec["version"] == "conf09-nohead-e5.0-2026-10-18" and rec["litellm_call_id"] == "c1" and rec["error"] is None
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["kent_event"] == "triage" and line["decision"] == "smart"


async def test_high_confidence_keeps_the_default_budget(gw, cfg_path):
    kw = call()
    rec = await gw.apply_triage(kw, answer({"8": 0.9, "9": 0.1}))
    assert rec["decision"] == "default" and kw["extra_body"]["thinking_budget_tokens"] == 2048


async def test_triage_request_shape(gw, cfg_path):
    tools = [{"type": "function", "function": {"name": "terminal", "parameters": {"type": "object"}}}]
    kw = call(tools=tools)
    post = answer({"8": 1.0})
    await gw.apply_triage(kw, post)
    url, body, timeout = post.sent[0]
    assert url == "http://127.0.0.1:8080/v1/chat/completions" and timeout == 30
    assert body["messages"][:-1] == kw["messages"] and body["tools"] == tools
    assert body["messages"][-1]["role"] == "user" and body["messages"][-1]["content"].startswith("How likely is it")
    assert "0 = very unlikely, 9 = certain" in body["messages"][-1]["content"]
    assert body["max_tokens"] == 1 and body["temperature"] == 0 and body["logprobs"] and body["top_logprobs"] == 20
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and body["cache_prompt"] is True
    assert len(kw["messages"]) == 1                          # the real request is not changed


async def test_the_deployment_settings_are_not_mutated(gw, cfg_path):
    kw = call()
    original = kw["extra_body"]
    await gw.apply_triage(kw, answer({"1": 1.0}))
    assert original["thinking_budget_tokens"] == 2048 and kw["extra_body"] is not original


@pytest.mark.parametrize("kw", [
    call(identity="operator"), call(identity="gent"), call(identity="gent-1a2b3c4d"),
    call(group="default", routed=None), call(group="smart", routed=None),
    call(routed="frontier"), call(routed=None),
], ids=["operator", "gent", "gent-id", "default-by-name", "smart-by-name", "auto-to-frontier", "no-decision"])
async def test_triage_applies_only_to_kents_auto_calls_sent_to_default(gw, cfg_path, kw):
    post = answer({"0": 1.0})
    assert await gw.apply_triage(kw, post) is None
    assert not post.sent and kw["extra_body"]["thinking_budget_tokens"] == 2048


async def test_routing_metadata_in_litellm_metadata_also_counts(gw, cfg_path):
    kw = call()
    kw["litellm_metadata"], kw["metadata"] = kw.pop("metadata"), {}
    assert (await gw.apply_triage(kw, answer({"1": 1.0})))["decision"] == "smart"


@pytest.mark.parametrize("failure", ["raise", "no_logprobs", "no_choices"])
async def test_any_triage_failure_keeps_the_default_budget(gw, cfg_path, failure):
    async def post(url, body, timeout):
        if failure == "raise":
            raise TimeoutError("triage timed out")
        if failure == "no_logprobs":
            return {"choices": [{"message": {"content": "8"}}]}
        return {"error": "x"}
    kw = call()
    rec = await gw.apply_triage(kw, post)
    assert rec["decision"] == "default" and kw["extra_body"]["thinking_budget_tokens"] == 2048
    assert rec["error"]


async def test_unusable_answer_keeps_the_default_budget(gw, cfg_path):
    kw = call()
    rec = await gw.apply_triage(kw, answer({"Sure": 0.9, "1": 0.1}))
    assert rec["decision"] == "default" and rec["error"] == "no usable answer"
    assert kw["extra_body"]["thinking_budget_tokens"] == 2048


async def test_deployment_without_extra_body_is_left_alone(gw, cfg_path):
    kw = call()
    kw["extra_body"] = None
    rec = await gw.apply_triage(kw, answer({"1": 1.0}))
    assert rec["decision"] == "default" and "extra_body" in rec["error"] and kw["extra_body"] is None


async def test_disabled_triage_sends_nothing(gw, cfg_path):
    rewrite(cfg_path, enabled=False)
    post = answer({"0": 1.0})
    assert await gw.apply_triage(call(), post) is None and not post.sent


async def test_hook_sizes_max_tokens_for_the_raised_budget(gw, cfg_path, monkeypatch):
    monkeypatch.setattr(gw, "_post_json", answer({"1": 1.0}))
    kw = await gw.activity_logger.async_pre_call_deployment_hook(call(max_tokens=300), "acompletion")
    assert kw["extra_body"]["thinking_budget_tokens"] == 4096 and kw["max_tokens"] == 300 + 4096


async def test_hook_without_triage_keeps_the_default_sizing(gw, cfg_path, monkeypatch):
    monkeypatch.setattr(gw, "_post_json", answer({"9": 1.0}))
    kw = await gw.activity_logger.async_pre_call_deployment_hook(call(max_tokens=300), "acompletion")
    assert kw["extra_body"]["thinking_budget_tokens"] == 2048 and kw["max_tokens"] == 300 + 2048


@pytest.mark.skipif(not SHORTQ.is_file(), reason="private bench data not present")
def test_installed_wording_and_threshold_send_the_same_share(gw):
    """The deployed question is the c09_nohead wording measured on 2026-10-18; at 5.0 it sends 33 of 115."""
    sys.path.insert(0, str(REPO / "tests" / "bench"))
    import triagecheck
    cfg = gw.triage_config(REPO_CONFIG)
    q, opts, head = triagecheck.VARIANTS["c09_nohead"]
    assert (cfg["head"], cfg["question"], cfg["options"]) == (head, q, opts)
    rows = [r for r in map(json.loads, SHORTQ.open()) if r["variant"] == "c09_nohead"]
    sent = sum(gw.triage_budget(*gw.triage_score([{"token": t, "logprob": lp} for t, lp in r["top"]], opts), cfg) == 4096
               for r in rows)
    assert len(rows) == 115 and sent == 33
