"""Live adversarial harness: a scratch LiteLLM gateway (real kent_gateway + rendered
dev config) whose smart/frontier tiers point at a canary server. Any canary hit
from a restricted identity is a policy bypass.

Run with:  LITELLM_BIN=<venv>/bin/litellm python -m pytest tests/live
Requires the local llama.cpp server on 127.0.0.1:8080 (router/fast tiers).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GW_PORT, CANARY_PORT = 4099, 9199
GW = f"http://127.0.0.1:{GW_PORT}"
KEYS = {i: f"sk-live-{i}-" + os.urandom(16).hex() for i in ("operator", "kent", "gent", "metrics")}


def _wait(url, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"timeout waiting for {url}")


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    litellm_bin = os.environ.get("LITELLM_BIN")
    assert litellm_bin, "set LITELLM_BIN to a litellm installed from install/services/litellm/requirements.lock"
    try:
        httpx.get("http://127.0.0.1:8080/health", timeout=3).raise_for_status()
    except Exception:
        pytest.skip("local llama.cpp server not running on :8080")

    d = tmp_path_factory.mktemp("live")
    creds = d / "creds"; creds.mkdir()
    for i, k in KEYS.items():
        (creds / f"{i}_key").write_text(k)
    canary_log = d / "canary.jsonl"; canary_log.touch()

    # Render exactly as install.sh does, then point cloud tiers at the canary.
    render_src = (REPO / "install/services/litellm/install.sh").read_text()
    render_py = render_src.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    (d / "render.py").write_text(render_py)
    python = str(Path(litellm_bin).parent / "python")
    subprocess.run([python, str(d / "render.py"), str(REPO / "configs/litellm_config.dev.yaml"),
                    str(d / "config.yaml")], check=True)
    cfg = yaml.safe_load((d / "config.yaml").read_text())
    for m in cfg["model_list"]:
        if m["model_name"] in ("smart", "frontier"):
            m["litellm_params"] = {"model": "openai/canary", "api_base": f"http://127.0.0.1:{CANARY_PORT}/v1",
                                   "api_key": "sk-canary"}
    (d / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))

    canary = subprocess.Popen([sys.executable, str(Path(__file__).with_name("canary_server.py")),
                               str(CANARY_PORT), str(canary_log)], start_new_session=True)
    genv = dict(os.environ, CREDENTIALS_DIRECTORY=str(creds), LITELLM_NON_ROOT="true",
                LITELLM_UI_PATH=str(d / "ui"), LITELLM_LOCAL_MODEL_COST_MAP="True", HOME=str(d),
                PYTHONPATH=str(REPO / "install/services/litellm"))
    genv.pop("ANTHROPIC_API_KEY", None)
    gw_log = open(d / "gateway.log", "w")
    gw = subprocess.Popen([litellm_bin, "--config", str(d / "config.yaml"), "--host", "127.0.0.1",
                           "--port", str(GW_PORT), "--telemetry", "False"],
                          env=genv, stdout=gw_log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        _wait(f"{GW}/health/liveliness")
        yield {"dir": d, "canary_log": canary_log, "gateway_log": d / "gateway.log"}
    finally:
        for p in (gw, canary):
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        print(f"\n[live] artifacts kept in {d}")


@pytest.fixture
def canary(env):
    """Returns a callable giving the number of canary hits since the test started."""
    start = len(env["canary_log"].read_text().splitlines())
    return lambda: len(env["canary_log"].read_text().splitlines()) - start


def call(key=None, path="/v1/chat/completions", method="POST", body=None, headers=None, params=None,
         timeout=300):
    h = dict(headers or {})
    if key is not None and not any(k.lower() in ("authorization", "x-litellm-api-key", "api-key",
                                                  "x-api-key", "x-goog-api-key") for k in h):
        h["Authorization"] = f"Bearer {key}"
    return httpx.request(method, GW + path, headers=h, params=params,
                         content=None if body is None else (body if isinstance(body, bytes) else json.dumps(body)),
                         timeout=timeout)


def chat(model, **extra):
    return {"model": model, "max_tokens": 4, "messages": [{"role": "user", "content": "Reply OK"}], **extra}
