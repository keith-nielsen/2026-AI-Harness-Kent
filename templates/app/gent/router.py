"""Decide whether a task needs knowledge beyond the Gent's local model. Gents
cannot call smart/frontier tiers; such tasks are escalated to Kent instead."""
from __future__ import annotations

import json
import os
import re

import requests

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://172.30.0.1:4000/v1")

SYSTEM = (
    "You triage tasks for a small local model. Decide if the task can be done with general "
    "knowledge, web research and coding, or if it needs an expert judgement a small model is "
    "likely to get wrong (legal/licensing interpretation, security-critical design decisions, "
    "novel architecture trade-offs). Reply JSON only: "
    '{"escalate": true|false, "question": "<the precise question to ask an expert, or empty>"}'
)


def needs_escalation(api_key: str, description: str) -> tuple[bool, str]:
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
        return bool(d.get("escalate")), str(d.get("question", ""))[:2000]
    except Exception:  # noqa: BLE001 - a broken router must not stall the project
        return False, ""
