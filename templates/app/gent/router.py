"""
Kent — Gent CEO Router

Calls the T0 Router model via the gateway to classify tasks before
crew construction. Returns a tier decision that determines which LLM
bindings the crew factory uses.

The CEO does NOT call frontier directly — if the router says frontier,
the CEO tries smart first, then escalates to Kent via shared_learnings.
"""

import json
import os
from dataclasses import dataclass
from typing import Literal

import requests

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://172.30.0.1:4000")


@dataclass
class RouteDecision:
    tier: Literal["fast", "smart"]
    review_required: bool
    confidence: float
    escalation_reason: str = ""
    original_tier: str = ""  # what the router actually said (may be "frontier")


ROUTER_SYSTEM = """Classify the task. Respond ONLY with JSON:
{"tier":"fast"|"frontier",
 "confidence":0.0-1.0,
 "reason":""}

Rules:
- "fast" means: this is a PURELY MECHANICAL task requiring zero
  reasoning. Examples: formatting, extraction, transformation,
  classification, template filling, grammar correction, data
  cleaning, simple summarization, structured output conversion.
- "frontier" means: ANYTHING else — code generation, planning,
  analysis, creative writing, domain expertise, multi-step logic,
  problem-solving, debugging, tool orchestration, context >4K.
- Only choose "fast" if you are 100% certain. When in doubt,
  choose "frontier"."""


def route(task_description: str, api_key: str) -> RouteDecision:
    """Classify a task and return the tier decision.

    Gents cannot call frontier, so frontier decisions are downgraded to
    smart with escalation flagged. The CEO handles escalation to Kent
    separately if smart execution fails.
    """
    resp = requests.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "router",
            "messages": [
                {"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": task_description},
            ],
            "max_tokens": 256,
            "response_format": {"type": "json_object"},
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(raw)

    tier = data.get("tier", "smart")
    confidence = float(data.get("confidence", 0.5))
    review = bool(data.get("review_required", True))
    reason = data.get("escalation_reason", "")

    # Apply confidence floor — only route to fast at >= 0.99
    if confidence < 0.99:
        tier = "frontier"
        review = True

    original_tier = tier

    # Gents cannot call frontier — downgrade to smart, flag for escalation
    if tier == "frontier":
        tier = "smart"
        review = True
        if not reason:
            reason = "Router classified as frontier; attempting smart first."

    return RouteDecision(
        tier=tier,
        review_required=review,
        confidence=confidence,
        escalation_reason=reason,
        original_tier=original_tier,
    )
