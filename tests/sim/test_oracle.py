"""The oracle fixture turns answer files into OpenAI-style replies (text or tool calls), and the
sim-routed gateway config routes exactly like prod with the oracle standing in for Anthropic."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "sim"))
from oracle import parse_answer  # noqa: E402


def test_markdown_answer_is_plain_text(tmp_path):
    f = tmp_path / "r1.md"
    f.write_text("forty-two")
    assert parse_answer(f, "r1") == ("forty-two", [])


def test_json_answer_becomes_openai_tool_calls(tmp_path):
    f = tmp_path / "r2.json"
    f.write_text(json.dumps({"content": "checking", "tool_calls": [
        {"name": "web_search", "arguments": {"query": "x"}}, {"name": "terminal"}]}))
    text, calls = parse_answer(f, "r2")
    assert text == "checking"
    assert [c["function"]["name"] for c in calls] == ["web_search", "terminal"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"query": "x"}
    assert calls[1]["function"]["arguments"] == "{}"
    assert len({c["id"] for c in calls}) == 2 and all(c["type"] == "function" for c in calls)


def _models(name: str) -> dict:
    cfg = yaml.safe_load((ROOT / "configs" / name).read_text())
    return {m["model_name"]: m["litellm_params"] for m in cfg["model_list"]}


def test_sim_routed_routes_like_prod_with_oracle_for_anthropic():
    prod, sim = _models("litellm_config.yaml"), _models("litellm_config.sim-routed.yaml")
    router = lambda m: m["auto"]["complexity_router_config"]  # noqa: E731
    assert router(sim)["tiers"] == router(prod)["tiers"] == {"fast": "fast", "smart": "smart", "frontier": "frontier"}
    assert router(sim)["fallback_tier"] == router(prod)["fallback_tier"]
    assert prod["frontier"]["model"].startswith("anthropic/")
    assert sim["frontier"]["model"] == "openai/oracle-frontier"
    assert sim["frontier"]["api_base"] == "http://127.0.0.1:4010/v1"
    # smart is the local model thinking everywhere: identical in prod and sim-routed
    assert sim["fast"] == prod["fast"] and sim["router"] == prod["router"] and sim["smart"] == prod["smart"]


def test_smart_is_the_local_model_thinking_and_fast_is_not():
    for name in ("litellm_config.yaml", "litellm_config.dev.yaml", "litellm_config.sim.yaml",
                 "litellm_config.sim-routed.yaml"):
        m = _models(name)
        assert m["smart"]["api_base"] == "http://127.0.0.1:8080/v1", name
        assert m["smart"]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True}, name
        assert m["smart"]["extra_body"]["thinking_budget_tokens"] > 0, name
        # thinking to the 8192 budget took up to 823 s (think-v1-1016); the router's 600 s would cut it
        assert m["smart"]["timeout"] == m["smart"]["stream_timeout"] >= 1200, name
        for tier in ("router", "fast"):
            assert m[tier]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}, (name, tier)


def test_oracle_tiers_drop_unsupported_params():
    # Hermes sends reasoning_effort; without drop_params every oracle call fails and falls back.
    for name in ("litellm_config.sim.yaml", "litellm_config.sim-routed.yaml"):
        models = _models(name)
        assert models["smart"].get("drop_params") is True and models["frontier"].get("drop_params") is True, name
