"""Live adversarial tests against a real LiteLLM process (see conftest.py)."""
from __future__ import annotations

import json

import pytest

from conftest import KEYS, call, chat

# --- Positive controls: prove the canary and the local tier both work ---------
def test_control_kent_reaches_cloud_tier_canary(canary):
    r = call(KEYS["kent"], body=chat("smart"))
    assert r.status_code == 200 and "CANARY-TIER-REACHED" in r.text
    assert canary() >= 1


def test_control_gent_reaches_local_fast(canary):
    r = call(KEYS["gent"], body=chat("fast"))
    assert r.status_code == 200 and r.headers.get("x-litellm-model-group") == "fast"
    assert canary() == 0


# --- gent must never reach a cloud tier ------------------------------------------
SMUGGLE = [
    dict(fallbacks=["frontier"]),
    dict(fallbacks=["smart"], mock_testing_fallbacks=True),
    dict(fallbacks=[{"fast": ["frontier"]}], mock_testing_fallbacks=True),
    dict(context_window_fallbacks=[{"fast": ["frontier"]}]),
    dict(api_base="http://127.0.0.1:9199/v1"),
    dict(api_base="http://127.0.0.1:9199/v1", api_key="sk-x"),
    dict(base_url="http://127.0.0.1:9199/v1"),
    dict(metadata={"model_group": "frontier"}),
    dict(litellm_metadata={"user_api_key_alias": "kent"}),
]


@pytest.mark.parametrize("extra", SMUGGLE, ids=lambda e: ",".join(e))
def test_gent_body_smuggling_never_reaches_cloud(canary, extra):
    r = call(KEYS["gent"], body=chat("fast", **extra))
    assert canary() == 0, f"BYPASS: canary hit via {extra} (status {r.status_code})"
    # 403 = kent_gateway deny-by-default; 401 = LiteLLM's own client-side
    # credential guard (api_base/base_url/api_key), which runs first.
    assert r.status_code in (401, 403)


def test_gent_user_field_is_attribution_not_identity(canary):
    # "user" is a standard OpenAI attribution field and is allowed; it must not
    # change the caller's identity or tier access.
    r = call(KEYS["gent"], body=chat("fast", user="kent"))
    assert r.status_code == 200 and r.headers.get("x-litellm-model-group") == "fast"
    r = call(KEYS["gent"], body=chat("frontier", user="kent"))
    assert r.status_code == 403 and canary() == 0


@pytest.mark.parametrize("model", ["smart", "frontier", "auto", "fast,smart", "smart,fast"])
def test_gent_direct_cloud_models_refused(canary, model):
    r = call(KEYS["gent"], body=chat(model))
    assert r.status_code == 403 and canary() == 0


def test_gent_streaming_cloud_refused(canary):
    r = call(KEYS["gent"], body=chat("frontier", stream=True))
    assert r.status_code == 403 and canary() == 0


@pytest.mark.parametrize("params", [{"model": "frontier"}, {"MODEL": "smart"}, {"target_model_names": "frontier"}])
def test_gent_model_via_query_string_refused(canary, params):
    # Regression: before deny-by-default, ?model=frontier reached the cloud tier.
    r = call(KEYS["gent"], body=chat("fast"), params=params)
    assert r.status_code == 403 and canary() == 0


def test_gent_model_via_routing_header_refused(canary):
    r = call(KEYS["gent"], body=chat("fast"), headers={"Authorization": f"Bearer {KEYS['gent']}", "x-litellm-model": "frontier"})
    assert r.status_code == 403 and canary() == 0


def test_kent_query_model_is_a_kent_privilege_only(canary):
    # Control: the same trick by a full-access identity reaches the cloud tier,
    # proving the gent refusal above is policy, not an inert parameter.
    call(KEYS["kent"], body=chat("fast"), params={"model": "frontier"})
    assert canary() >= 1


@pytest.mark.parametrize("path", [
    "/v1/embeddings", "/v1/responses", "/v1/messages", "/anthropic/v1/messages",
    "/openai/deployments/smart/chat/completions", "/engines/smart/chat/completions",
    "/openai/deployments/fast/chat/completions", "/v1/completions/../../key/generate",
    "/key/generate", "/model/new", "/config/update", "/v1/batches",
])
def test_gent_alternate_routes_refused(canary, path):
    r = call(KEYS["gent"], path=path, body=chat("smart"))
    assert r.status_code in (403, 404) and canary() == 0


def test_gent_oversized_body_refused_or_handled(canary):
    big = chat("fast"); big["messages"][0]["content"] = "A" * 5_000_000
    r = call(KEYS["gent"], body=big, timeout=120)
    assert canary() == 0 and r.status_code != 200 or r.headers.get("x-litellm-model-group") == "fast"


# --- Key transport variants ------------------------------------------------------
HEADERS = [
    lambda k: {"Authorization": f"Bearer {k}"},
    lambda k: {"x-litellm-api-key": f"Bearer {k}"},
    lambda k: {"api-key": k},
    lambda k: {"x-api-key": k},
    lambda k: {"x-goog-api-key": k},
]


@pytest.mark.parametrize("hdr", HEADERS, ids=["authorization", "x-litellm-api-key", "api-key", "x-api-key", "x-goog-api-key"])
def test_gent_key_in_any_header_is_still_gent(canary, hdr):
    models = call(path="/v1/models", method="GET", headers=hdr(KEYS["gent"]))
    if models.status_code == 200:
        ids = {m["id"] for m in models.json()["data"]}
        assert ids <= {"router", "fast"}, f"gent saw {ids}"
    r = call(body=chat("frontier"), headers=hdr(KEYS["gent"]))
    assert r.status_code in (401, 403) and canary() == 0


def test_mixed_headers_do_not_escalate(canary):
    r = call(body=chat("frontier"), headers={"Authorization": f"Bearer {KEYS['gent']}", "x-api-key": "garbage"})
    assert r.status_code in (401, 403) and canary() == 0
    r = call(body=chat("frontier"), headers={"Authorization": "Bearer garbage", "x-api-key": KEYS["gent"]})
    assert r.status_code in (401, 403) and canary() == 0


def test_key_in_query_string_not_accepted(canary):
    r = call(body=chat("smart"), params={"api_key": KEYS["kent"], "key": KEYS["kent"]})
    assert r.status_code == 401 and canary() == 0


# --- kent: full access, but no client-side redirection of upstreams ----------------
def test_kent_cannot_redirect_fast_to_arbitrary_api_base(canary):
    r = call(KEYS["kent"], body=chat("fast", api_base="http://127.0.0.1:9199/v1"))
    assert canary() == 0, "client-side api_base override was honoured"


# --- metrics identity ---------------------------------------------------------------
def test_metrics_scrape_and_nothing_else(canary):
    call(KEYS["kent"], body=chat("fast"))                      # generate at least one series
    m = call(KEYS["metrics"], path="/metrics/", method="GET")
    assert m.status_code == 200 and "litellm_" in m.text
    assert call(None, path="/metrics/", method="GET").status_code == 401
    # Refused by kent_gateway ("route not permitted"); LiteLLM's metrics endpoint
    # reports every auth failure as 401, so either refusal code is accepted.
    assert call(KEYS["gent"], path="/metrics/", method="GET").status_code in (401, 403)
    assert call(KEYS["metrics"], body=chat("fast")).status_code == 403
    assert call(KEYS["metrics"], path="/v1/models", method="GET").status_code == 403
    assert canary() == 0


def test_no_key_and_bad_key(canary):
    assert call(None, body=chat("fast")).status_code == 401
    assert call("sk-nope", body=chat("fast")).status_code == 401
    assert call(KEYS["gent"][:-2], body=chat("fast")).status_code == 401


# --- Tool-call history repair: a reply cut off mid tool call must not 500 every later request ---
def _cut_off_history():
    return [{"role": "user", "content": "Write the CSV to data.py"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c9", "type": "function", "function": {
                "name": "write_file", "arguments": '{"path": "data.py", "content": "rows = \\"\\"\\"date,site,cou'}}]},
            {"role": "tool", "tool_call_id": "c9", "content": "Error: Failed to parse tool arguments as JSON."},
            {"role": "user", "content": "Reply OK"}]


def test_cut_off_tool_call_is_repaired_before_the_upstream(canary, env):
    r = call(KEYS["kent"], body={"model": "smart", "max_tokens": 4, "messages": _cut_off_history()})
    assert r.status_code == 200 and canary() == 1
    sent = json.loads(json.loads(env["canary_log"].read_text().splitlines()[-1])["body"])
    args = sent["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert "_invalid_arguments" in json.loads(args)          # the upstream got valid JSON


def test_cut_off_tool_call_no_longer_breaks_the_local_model(canary):
    tools = [{"type": "function", "function": {"name": "write_file", "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}}}}}]
    r = call(KEYS["gent"], body={"model": "fast", "max_tokens": 16, "messages": _cut_off_history(), "tools": tools})
    assert r.status_code == 200, r.text[:300]                 # was 500 "Failed to parse tool call arguments"
