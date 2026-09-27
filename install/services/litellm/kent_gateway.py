"""Kent — identity-based access and activity logging for the LiteLLM gateway.

Installed to /opt/kent-litellm/lib/ and loaded by LiteLLM through its config:

    general_settings:
      custom_auth: kent_gateway.user_api_key_auth
      custom_auth_run_common_checks: true
    litellm_settings:
      enable_post_custom_auth_checks: true
      callbacks: kent_gateway.activity_logger

Identities and their keys come from systemd credentials (one file per identity in
$CREDENTIALS_DIRECTORY). POLICY is the single place that says who may call what.
LiteLLM enforces the model list; this module enforces it again itself, so a change
in LiteLLM's flag semantics cannot silently open cloud tiers to restricted callers.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, Request
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth

# Access policy per identity. "models": model groups it may call (None = all).
# "routes": HTTP paths it may use (None = all). The key for each identity is the
# systemd credential "<identity>_key".
INFERENCE_ROUTES = frozenset({
    "/v1/chat/completions", "/chat/completions",
    "/v1/completions", "/completions",
    "/v1/models", "/models",
})
POLICY: dict[str, dict[str, frozenset[str] | None]] = {
    "operator": {"models": None, "routes": None},
    "kent":     {"models": None, "routes": None},
    # Local tiers only; Gents escalate to Kent for anything more.
    "gent":     {"models": frozenset({"router", "fast"}), "routes": INFERENCE_ROUTES},
    # Prometheus scrape: no models at all, metrics endpoint only.
    "metrics":  {"models": frozenset(), "routes": frozenset({"/metrics", "/metrics/"})},
}
# LiteLLM treats an empty model list as "all models", so identities with no
# model access get a sentinel that matches no deployment.
_NO_MODELS = ["kent-no-model-access"]

# Restricted identities (model list not None) get a deny-by-default request
# shape. LiteLLM reads the target model from many places besides body "model"
# (query "model", the x-litellm-model header, target_model_names, session/
# completion objects, managed file ids) and honours client-side fallbacks /
# api_base; a live test showed ?model=frontier reaching a cloud tier. So instead
# of mirroring LiteLLM's extraction logic, restricted callers may send no query
# string, no routing header, and only standard OpenAI chat/completions fields.
RESTRICTED_BODY_FIELDS = frozenset({
    "model", "messages", "prompt", "max_tokens", "max_completion_tokens", "temperature",
    "top_p", "n", "stream", "stream_options", "stop", "presence_penalty",
    "frequency_penalty", "logit_bias", "logprobs", "top_logprobs", "seed",
    "response_format", "tools", "tool_choice", "parallel_tool_calls",
    "reasoning_effort", "user",
})
RESTRICTED_FORBIDDEN_HEADERS = frozenset({"x-litellm-model"})

_CRED_DIR = Path(os.environ.get("CREDENTIALS_DIRECTORY", "/nonexistent"))


def _load_keys() -> dict[str, str]:
    keys = {}
    for identity in POLICY:
        path = _CRED_DIR / f"{identity}_key"
        if path.is_file() and (secret := path.read_text().strip()):
            keys[identity] = secret
    if not keys:
        raise RuntimeError(f"kent_gateway: no identity keys found in {_CRED_DIR}")
    return keys


_KEYS = _load_keys()

# Per-Gent keys: one file per spawned Gent, <stack_id>.key, written by
# kent-spawn-gent and removed by kent-destroy-gent. Each maps to the identity
# "gent-<stack_id>" under the "gent" policy, so every Gent's traffic is
# attributable. Re-read when the directory changes (spawn/destroy needs no restart).
_GENT_KEYS_DIR = Path(os.environ.get("KENT_GENT_KEYS_DIR", "/etc/kent/litellm/gent-keys"))
_GENT_ID = re.compile(r"^[0-9a-f]{8}$")
_gent_cache: tuple[int | None, dict[str, str]] = (None, {})


def _gent_keys() -> dict[str, str]:
    global _gent_cache
    try:
        stamp = _GENT_KEYS_DIR.stat().st_mtime_ns
    except OSError:
        return {}
    if stamp != _gent_cache[0]:
        keys = {}
        for path in _GENT_KEYS_DIR.glob("*.key"):
            if _GENT_ID.match(path.stem) and path.is_file() and (secret := path.read_text().strip()):
                keys[f"gent-{path.stem}"] = secret
        _gent_cache = (stamp, keys)
    return _gent_cache[1]


def _policy(identity: str) -> dict[str, frozenset[str] | None]:
    return POLICY["gent"] if identity.startswith("gent-") else POLICY[identity]


def _log_denial(identity: str | None, request: Request, reason: str, model: str | None = None) -> None:
    record = {
        "kent_event": "access_denied",
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "identity": identity,
        "route": request.url.path,
        "model": model[:200] if isinstance(model, str) else None,
        "reason": reason[:300],
        "client": request.client.host if request.client else None,
    }
    print(json.dumps(record), file=sys.stdout, flush=True)


def _identify(api_key: str) -> str | None:
    presented = api_key.encode()
    match = None
    for identity, secret in {**_KEYS, **_gent_keys()}.items():
        # Compare against every key so timing does not reveal which one matched.
        if hmac.compare_digest(presented, secret.encode()):
            match = identity
    return match


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    identity = _identify(api_key or "")
    if identity is None:
        _log_denial(None, request, "invalid or missing key")
        raise HTTPException(status_code=401, detail="invalid key")

    policy = _policy(identity)
    routes, allowed = policy["routes"], policy["models"]
    if routes is not None and request.url.path not in routes:
        _log_denial(identity, request, "route not permitted")
        raise HTTPException(status_code=403, detail=f"route not permitted for {identity}")
    if allowed is not None:
        if request.url.query:
            _log_denial(identity, request, "query string not permitted")
            raise HTTPException(status_code=403, detail=f"query parameters not permitted for {identity}")
        if RESTRICTED_FORBIDDEN_HEADERS & {h.lower() for h in request.headers.keys()}:
            _log_denial(identity, request, "model routing header not permitted")
            raise HTTPException(status_code=403, detail=f"routing headers not permitted for {identity}")
    if allowed is not None and request.method not in ("GET", "HEAD", "POST"):
        _log_denial(identity, request, f"method {request.method} not permitted")
        raise HTTPException(status_code=403, detail=f"method not permitted for {identity}")
    if allowed is not None and request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            _log_denial(identity, request, "unreadable request body")
            raise HTTPException(status_code=400, detail="unreadable request body")
        # Only a JSON object with a string "model" can be checked; anything else
        # (list/dict/number model, non-object body) is refused, never crashed on.
        model = body.get("model") if isinstance(body, dict) else None
        if isinstance(body, dict) and (extra := sorted(set(body) - RESTRICTED_BODY_FIELDS)):
            _log_denial(identity, request, f"body fields not permitted: {','.join(extra)[:200]}",
                        model if isinstance(model, str) else None)
            raise HTTPException(status_code=403, detail=f"request fields not permitted for {identity}: {extra}")
        if not isinstance(model, str) or model not in allowed:
            _log_denial(identity, request, "model not permitted", model if isinstance(model, str) else None)
            raise HTTPException(status_code=403, detail=f"model '{model}' not permitted for {identity}")

    if allowed is None:
        models = []                      # [] = all models in LiteLLM
    else:
        models = sorted(allowed) or _NO_MODELS
    return UserAPIKeyAuth(api_key=api_key, key_alias=identity, user_id=identity, models=models)


class KentActivityLogger(CustomLogger):
    """One JSON line per gateway call on stdout (journald → Alloy → Loki)."""

    def _emit(self, kwargs, start_time, end_time, status: str) -> None:
        slp = kwargs.get("standard_logging_object") or {}
        meta = slp.get("metadata") or {}
        routing = meta.get("routing_decision") or {}
        record = {
            "kent_event": "llm_call",
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "status": status,
            "identity": meta.get("user_api_key_alias") or meta.get("user_api_key_user_id"),
            "model_group": slp.get("model_group"),
            "routed_tier": routing.get("tier"),
            "routing_cause": routing.get("cause"),
            "model": slp.get("model"),
            "api_base": slp.get("api_base"),
            "prompt_tokens": slp.get("prompt_tokens"),
            "completion_tokens": slp.get("completion_tokens"),
            "latency_s": round((end_time - start_time).total_seconds(), 3) if start_time and end_time else None,
            "call_id": slp.get("id"),
        }
        print(json.dumps(record, default=str), file=sys.stdout, flush=True)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._emit(kwargs, start_time, end_time, "success")

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._emit(kwargs, start_time, end_time, "failure")


activity_logger = KentActivityLogger()
