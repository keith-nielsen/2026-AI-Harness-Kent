"""Fixtures for kent_gateway unit tests.

kent_gateway reads identity keys from $CREDENTIALS_DIRECTORY at import time, so
each test session gets a throwaway credentials dir with known fake keys.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from starlette.requests import Request

MODULE_DIR = Path(__file__).resolve().parents[2] / "install" / "services" / "litellm"

KEYS = {
    "operator": "sk-kent-operator-" + "a" * 40,
    "kent": "sk-kent-kent-" + "b" * 40,
    "gent": "sk-kent-gent-" + "c" * 40,
    "metrics": "sk-kent-metrics-" + "d" * 40,
}

# Per-Gent keys (kent-spawn-gent writes /etc/kent/litellm/gent-keys/<id>.key).
GENT_KEYS = {
    "1a2b3c4d": "sk-kent-gent1-" + "e" * 40,
    "deadbeef": "sk-kent-gent2-" + "f" * 40,
}


@pytest.fixture(scope="session")
def gw(tmp_path_factory):
    creds = tmp_path_factory.mktemp("creds")
    for identity, key in KEYS.items():
        (creds / f"{identity}_key").write_text(key + "\n")
    import os
    os.environ["CREDENTIALS_DIRECTORY"] = str(creds)
    gents = tmp_path_factory.mktemp("gent-keys")
    for sid, key in GENT_KEYS.items():
        (gents / f"{sid}.key").write_text(key + "\n")
    os.environ["KENT_GENT_KEYS_DIR"] = str(gents)
    sys.path.insert(0, str(MODULE_DIR))
    sys.modules.pop("kent_gateway", None)
    return importlib.import_module("kent_gateway")


def make_request(path: str, method: str = "POST", body: bytes | dict | None = None,
                 raw_path: bytes | None = None) -> Request:
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    body = body or b""
    scope = {
        "type": "http", "method": method, "path": path,
        "raw_path": raw_path or path.encode(), "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 4000), "scheme": "http",
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)
