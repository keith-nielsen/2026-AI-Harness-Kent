"""Triage a task: run it on the local model as is ("fast"), on the local model with thinking
on ("think" -> gateway tier "smart"), or escalate it to Kent ("escalate"). Gents cannot call
the frontier tier; tasks needing expert judgement go to Kent instead."""
from __future__ import annotations

import json
import os
import re

import requests

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://172.30.0.1:4000/v1")

SYSTEM = (
    "You triage tasks for a team working with a local model that can either answer directly or think "
    "step by step first. Choose \"escalate\" if the task needs an expert judgement a local model is "
    "likely to get wrong (legal/licensing interpretation, security-critical design decisions, novel "
    "architecture trade-offs); \"think\" if it can be done with general knowledge, web research and "
    "coding but needs careful multi-step reasoning (planning, debugging, non-trivial code, comparing "
    "options); otherwise \"fast\". Reply JSON only: "
    '{"route": "fast"|"think"|"escalate", "question": "<for escalate: the precise question to ask an expert, else empty>"}'
)
ROUTES = ("fast", "think", "escalate")


def triage(api_key: str, description: str) -> tuple[str, str]:
    """(route, question). A broken or unclear router answer means "fast": it must not stall the project."""
    try:
        r = requests.post(f"{GATEWAY_URL}/chat/completions", timeout=300,
                          headers={"Authorization": f"Bearer {api_key}"},
                          json={"model": "router", "max_tokens": 200,
                                "messages": [{"role": "system", "content": SYSTEM},
                                             {"role": "user", "content": description[:4000]}]})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"] or ""
        m = re.search(r"\{.*\}", text, re.S)
        d = json.loads(m.group(0)) if m else {}
        route = d.get("route") if d.get("route") in ROUTES else ("escalate" if d.get("escalate") is True else "fast")
        return route, str(d.get("question", ""))[:2000]
    except Exception:  # noqa: BLE001 - a broken router must not stall the project
        return "fast", ""


def needs_escalation(api_key: str, description: str) -> tuple[bool, str]:
    route, question = triage(api_key, description)
    return route == "escalate", question
