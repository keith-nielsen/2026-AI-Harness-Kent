"""Gent task triage (templates/app/gent/router.py): fast, think (local model, thinking) or escalate."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def router(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "templates" / "app"))
    sys.modules.pop("gent.router", None)
    mod = importlib.import_module("gent.router")
    replies = []

    class Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            if isinstance(self.text, Exception):
                raise self.text

        def json(self):
            return {"choices": [{"message": {"content": self.text}}]}

    def post(url, **kw):
        replies.append(kw["json"])
        return Resp(mod_reply.pop(0))

    mod_reply = []
    monkeypatch.setattr(mod.requests, "post", post)
    return mod, mod_reply, replies


@pytest.mark.parametrize("text, want", [
    ('{"route": "think", "question": ""}', ("think", "")),
    ('{"route": "fast", "question": ""}', ("fast", "")),
    ('{"route": "escalate", "question": "Is GPL-3 compatible here?"}', ("escalate", "Is GPL-3 compatible here?")),
    ('Sure. {"route": "think"}', ("think", "")),
    ('{"escalate": true, "question": "q?"}', ("escalate", "q?")),     # the earlier reply format
    ('{"escalate": false}', ("fast", "")),
    ('{"route": "frontier"}', ("fast", "")),                          # not a route a Gent can take
    ("no json at all", ("fast", "")),
    ("", ("fast", "")),
])
def test_triage_routes(router, text, want):
    mod, reply, sent = router
    reply.append(text)
    assert mod.triage("sk-test", "do a thing") == want
    assert sent[0]["model"] == "router"


def test_a_broken_router_means_fast(router):
    mod, reply, _ = router
    reply.append(RuntimeError("gateway down"))
    assert mod.triage("sk-test", "x") == ("fast", "")


def test_needs_escalation_still_answers_yes_or_no(router):
    mod, reply, _ = router
    reply.extend(['{"route": "escalate", "question": "q"}', '{"route": "think"}'])
    assert mod.needs_escalation("k", "x") == (True, "q")
    assert mod.needs_escalation("k", "x") == (False, "")
