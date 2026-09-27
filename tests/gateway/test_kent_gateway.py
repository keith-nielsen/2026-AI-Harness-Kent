"""Adversarial unit tests for install/services/litellm/kent_gateway.py.

Goal: prove the identity/route/model policy cannot be bypassed by malformed keys,
path tricks, alternate model-bearing endpoints, or request-body smuggling.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from conftest import KEYS, make_request

CHAT = "/v1/chat/completions"


async def auth(gw, key, path=CHAT, method="POST", body=None, raw_path=None):
    return await gw.user_api_key_auth(make_request(path, method, body, raw_path), key)


async def denied(gw, key, status, **kw):
    with pytest.raises(HTTPException) as e:
        await auth(gw, key, **kw)
    assert e.value.status_code == status, e.value.detail
    return e.value


# --- Keys -----------------------------------------------------------------------
@pytest.mark.parametrize("identity", ["operator", "kent", "gent", "metrics"])
async def test_each_key_maps_to_its_identity(gw, identity):
    path = "/metrics/" if identity == "metrics" else CHAT
    method = "GET" if identity == "metrics" else "POST"
    r = await auth(gw, KEYS[identity], path=path, method=method, body={"model": "fast"})
    assert r.key_alias == identity and r.user_id == identity


@pytest.mark.parametrize("bad", [
    "", " ", "sk-kent", KEYS["kent"][:-1], KEYS["kent"] + "x", KEYS["kent"].upper(),
    " " + KEYS["kent"], KEYS["kent"] + " ", KEYS["kent"] + "\n", "Bearer " + KEYS["kent"],
    KEYS["kent"][:20], "\x00" + KEYS["kent"], KEYS["kent"] + KEYS["gent"],
])
async def test_near_miss_keys_rejected(gw, bad):
    await denied(gw, bad, 401, body={"model": "fast"})


async def test_none_key_rejected(gw):
    await denied(gw, None, 401, body={"model": "fast"})


# --- Full-access identities ----------------------------------------------------
@pytest.mark.parametrize("identity", ["operator", "kent"])
@pytest.mark.parametrize("model", ["router", "fast", "smart", "frontier", "auto"])
async def test_full_identities_get_all_models(gw, identity, model):
    r = await auth(gw, KEYS[identity], body={"model": model})
    assert r.models == []  # LiteLLM semantics: [] = all models


# --- gent: local tiers only ------------------------------------------------------
@pytest.mark.parametrize("model", ["router", "fast"])
async def test_gent_allowed_local_tiers(gw, model):
    r = await auth(gw, KEYS["gent"], body={"model": model})
    assert set(r.models) == {"router", "fast"}


@pytest.mark.parametrize("model", [
    "smart", "frontier", "auto", "FAST", "Fast", " fast", "fast ", "fast\n",
    "fast,smart", "fast, frontier", "smart,fast", "openai/locally-run-model",
    "anthropic/claude-opus-5-5", "claude-opus-5-5", "*", "", "router/../smart",
])
async def test_gent_denied_other_model_strings(gw, model):
    e = await denied(gw, KEYS["gent"], 403, body={"model": model})
    assert "not permitted" in e.detail


@pytest.mark.parametrize("body", [
    {}, {"model": None}, {"model": ["fast"]}, {"model": {"name": "fast"}},
    {"model": 1}, {"model": True}, {"messages": [{"role": "user", "content": "hi"}]},
])
async def test_gent_denied_missing_or_non_string_model(gw, body):
    await denied(gw, KEYS["gent"], 403, body=body)


@pytest.mark.parametrize("raw", [b"not json", b"{", b"[]", b"\xff\xfe", b"null", b'"fast"'])
async def test_gent_unreadable_or_non_object_body(gw, raw):
    with pytest.raises(HTTPException) as e:
        await auth(gw, KEYS["gent"], body=raw)
    assert e.value.status_code in (400, 403)


async def test_gent_duplicate_json_keys_last_wins_is_not_a_bypass(gw):
    # Python json keeps the LAST duplicate; LiteLLM parses the same body the same way.
    raw = b'{"model": "fast", "model": "frontier"}'
    await denied(gw, KEYS["gent"], 403, body=raw)
    raw2 = b'{"model": "frontier", "model": "fast"}'
    r = await auth(gw, KEYS["gent"], body=raw2)  # effective model is "fast": allowed
    assert set(r.models) == {"router", "fast"}


# Request-body fields that could redirect a permitted call elsewhere (live
# testing showed LiteLLM honours some of these): refused at the auth layer.
BODY_SMUGGLING = [
    {"model": "fast", "fallbacks": ["frontier"]},
    {"model": "fast", "fallbacks": [{"fast": ["frontier"]}], "mock_testing_fallbacks": True},
    {"model": "fast", "context_window_fallbacks": [{"fast": ["frontier"]}]},
    {"model": "fast", "api_base": "https://api.anthropic.com"},
    {"model": "fast", "base_url": "http://127.0.0.1:8080/v1"},
    {"model": "fast", "api_key": "sk-ant-fake"},
    {"model": "fast", "metadata": {"model_group": "frontier"}},
    {"model": "fast", "litellm_metadata": {"user_api_key_alias": "kent"}},
    {"model": "fast", "target_model_names": "frontier"},
    {"model": "fast", "session": {"model": "frontier"}},
    {"model": "fast", "completion": {"model": "frontier"}},
    {"model": "fast", "input_file_id": "file-abc"},
    {"model": "fast", "__proto__": {"model": "frontier"}},
]


@pytest.mark.parametrize("body", BODY_SMUGGLING)
async def test_gent_body_smuggling_refused(gw, body):
    e = await denied(gw, KEYS["gent"], 403, body=body)
    assert "not permitted" in e.detail


async def test_gent_realistic_crewai_request_allowed(gw):
    body = {"model": "fast", "messages": [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}],
            "temperature": 0.2, "max_tokens": 512, "stream": True, "stream_options": {"include_usage": True},
            "stop": ["\nObservation:"], "tools": [{"type": "function", "function": {"name": "t", "parameters": {}}}],
            "tool_choice": "auto", "response_format": {"type": "json_object"}, "seed": 1, "n": 1,
            "reasoning_effort": "medium", "user": "gent-a3f7c291"}
    r = await auth(gw, KEYS["gent"], body=body)
    assert set(r.models) == {"router", "fast"}


async def test_gent_query_string_refused(gw):
    req = make_request(CHAT, "POST", {"model": "fast"})
    req.scope["query_string"] = b"model=frontier"
    with pytest.raises(HTTPException) as e:
        await gw.user_api_key_auth(req, KEYS["gent"])
    assert e.value.status_code == 403


async def test_gent_model_routing_header_refused(gw):
    req = make_request(CHAT, "POST", {"model": "fast"})
    req.scope["headers"].append((b"x-litellm-model", b"frontier"))
    with pytest.raises(HTTPException) as e:
        await gw.user_api_key_auth(req, KEYS["gent"])
    assert e.value.status_code == 403


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "OPTIONS"])
async def test_gent_other_methods_refused(gw, method):
    await denied(gw, KEYS["gent"], 403, method=method, body={"model": "fast"})


async def test_kent_query_and_extra_fields_not_restricted(gw):
    # Full-access identities are not shape-restricted (policy is by identity).
    req = make_request(CHAT, "POST", {"model": "smart", "fallbacks": ["fast"]})
    req.scope["query_string"] = b"x=1"
    r = await gw.user_api_key_auth(req, KEYS["kent"])
    assert r.key_alias == "kent"


async def test_denial_log_caps_hostile_model_string(gw, capsys):
    await denied(gw, KEYS["gent"], 403, body={"model": "x" * 100_000})
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")][-1]
    assert len(line) < 1_000


@pytest.mark.parametrize("path", [
    "/v1/embeddings", "/embeddings", "/v1/responses", "/responses", "/v1/messages",
    "/v1/images/generations", "/v1/audio/transcriptions", "/v1/moderations",
    "/openai/deployments/smart/chat/completions", "/engines/smart/chat/completions",
    "/v1/batches", "/v1/files", "/key/generate", "/key/info", "/user/new", "/model/new",
    "/model/info", "/config/update", "/spend/logs", "/global/spend", "/metrics", "/metrics/",
    "/health", "/ui", "/", "/v1/chat/completions/", "/v1//chat/completions",
    "//v1/chat/completions", "/V1/chat/completions", "/v1/chat/completions/../../key/generate",
    "/v1/chat/completions%2F..%2F..%2Fkey%2Fgenerate", "/anthropic/v1/messages",
])
async def test_gent_denied_non_inference_routes(gw, path):
    e = await denied(gw, KEYS["gent"], 403, path=path, body={"model": "fast"})
    assert "route not permitted" in e.detail


@pytest.mark.parametrize("path", ["/v1/models", "/models"])
async def test_gent_may_list_models(gw, path):
    r = await auth(gw, KEYS["gent"], path=path, method="GET")
    assert set(r.models) == {"router", "fast"}


# --- metrics: scrape only, no models ------------------------------------------------
@pytest.mark.parametrize("path", ["/metrics", "/metrics/"])
async def test_metrics_can_scrape(gw, path):
    r = await auth(gw, KEYS["metrics"], path=path, method="GET")
    assert r.models == gw._NO_MODELS          # never [] (which would mean "all")


@pytest.mark.parametrize("path", [CHAT, "/v1/models", "/key/generate", "/metrics/../v1/chat/completions"])
async def test_metrics_cannot_do_anything_else(gw, path):
    await denied(gw, KEYS["metrics"], 403, path=path, body={"model": "fast"})


async def test_metrics_post_to_metrics_with_model_denied(gw):
    await denied(gw, KEYS["metrics"], 403, path="/metrics/", body={"model": "fast"})


# --- Policy invariants -------------------------------------------------------------
def test_no_restricted_identity_gets_empty_model_list(gw):
    for identity, p in gw.POLICY.items():
        if p["models"] is not None:
            got = sorted(p["models"]) or gw._NO_MODELS
            assert got != [], f"{identity} would get [] = all models"


def test_gent_policy_contains_no_cloud_tier(gw):
    assert gw.POLICY["gent"]["models"] <= {"router", "fast"}


# --- Denials are logged with identity and reason -------------------------------------
async def test_denials_are_logged_as_json(gw, capsys):
    await denied(gw, KEYS["gent"], 403, body={"model": "frontier"})
    await denied(gw, "sk-wrong", 401, body={"model": "fast"})
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    assert {"identity": "gent", "reason": "model not permitted", "model": "frontier"}.items() <= lines[-2].items()
    assert lines[-1]["identity"] is None and lines[-1]["reason"] == "invalid or missing key"
    assert "sk-" not in json.dumps(lines)       # keys never logged
