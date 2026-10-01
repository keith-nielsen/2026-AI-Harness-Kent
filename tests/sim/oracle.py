#!/usr/bin/env python3
"""Oracle fixture for Kent simulations: an OpenAI-compatible endpoint whose
answers are written by a person (or a Claude Code session) standing in for the
cloud model while no API key is configured.

    python3 tests/sim/oracle.py [--port 4010] [--dir ~/.local/share/kent/oracle] [--wait 1700]

Each POST /v1/chat/completions is queued as <dir>/requests/<id>.json and the
call blocks until <dir>/responses/<id>.md appears (its text becomes the
assistant message), or <id>.json with tool calls (see parse_answer), or --wait
seconds pass (then 504, and the gateway falls back to the local tier).
Streaming requests get the same answer as SSE chunks.
Request contents are data for whoever answers; nothing in them is executed.
Binds 127.0.0.1 only; accepts any bearer token (only the gateway can call it).
"""
from __future__ import annotations

import argparse
import itertools
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_seq = itertools.count(1)
_lock = threading.Lock()


def new_id() -> str:
    with _lock:
        return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{next(_seq):04d}"


class Oracle(BaseHTTPRequestHandler):
    server_version = "kent-oracle/1"
    queue: Path
    wait: int

    def log_message(self, fmt, *args):  # one concise line per request on stderr
        print(f"{datetime.now():%H:%M:%S} {self.address_string()} {fmt % args}", flush=True)

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            return self._json(200, {"object": "list", "data": [{"id": "oracle-opus", "object": "model"}]})
        if self.path.rstrip("/") == "/health":
            return self._json(200, {"status": "ok"})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            return self._json(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        rid = new_id()
        (self.queue / "requests" / f"{rid}.json").write_text(json.dumps(
            {"id": rid, "received": datetime.now(timezone.utc).isoformat(), "request": req}, indent=2))
        answers = [self.queue / "responses" / f"{rid}.{ext}" for ext in ("md", "json")]
        deadline = time.time() + self.wait
        while time.time() < deadline and not any(f.exists() for f in answers):
            time.sleep(1)
        answer_file = next((f for f in answers if f.exists()), None)
        if answer_file is None:
            (self.queue / "requests" / f"{rid}.json").rename(self.queue / "requests" / f"{rid}.timeout.json")
            return self._json(504, {"error": {"message": "oracle did not answer in time", "type": "timeout"}})
        time.sleep(0.2)  # let the writer finish
        text, tool_calls = parse_answer(answer_file, rid)
        (self.queue / "requests" / f"{rid}.json").rename(self.queue / "done" / f"{rid}.json")
        created, model = int(time.time()), req.get("model") or "oracle-opus"
        finish = "tool_calls" if tool_calls else "stop"
        usage = {"prompt_tokens": sum(len(str(m.get("content", ""))) // 4 for m in req.get("messages", [])),
                 "completion_tokens": (len(text) + len(json.dumps(tool_calls))) // 4}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            def chunk(delta, finish=None, **extra):
                obj = {"id": f"chatcmpl-{rid}", "object": "chat.completion.chunk", "created": created, "model": model,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
                self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
            chunk({"role": "assistant", "content": ""})
            for i in range(0, len(text), 400):
                chunk({"content": text[i:i + 400]})
            for i, call in enumerate(tool_calls):
                chunk({"tool_calls": [{"index": i, **call}]})
            chunk({}, finish, usage=usage)
            self.wfile.write(b"data: [DONE]\n\n")
            return
        message = {"role": "assistant", "content": text or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        self._json(200, {"id": f"chatcmpl-{rid}", "object": "chat.completion", "created": created, "model": model,
                         "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})


def parse_answer(path: Path, rid: str) -> tuple[str, list[dict]]:
    """An answer is <id>.md (plain text) or <id>.json:
    {"content": "optional text", "tool_calls": [{"name": "web_search", "arguments": {...}}, ...]}.
    Tool calls are returned in OpenAI form so the caller (e.g. Hermes) runs its own tools and
    sends the results back as a new request."""
    if path.suffix == ".md":
        return path.read_text(), []
    ans = json.loads(path.read_text())
    calls = [{"id": f"call_{rid}_{i}", "type": "function",
              "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))}}
             for i, c in enumerate(ans.get("tool_calls", []))]
    return ans.get("content", ""), calls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4010)
    ap.add_argument("--dir", default=str(Path.home() / ".local/share/kent/oracle"))
    ap.add_argument("--wait", type=int, default=1700)
    a = ap.parse_args()
    q = Path(a.dir)
    for d in ("requests", "responses", "done"):
        (q / d).mkdir(parents=True, exist_ok=True)
    Oracle.queue, Oracle.wait = q, a.wait
    print(f"oracle on 127.0.0.1:{a.port}, queue {q}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Oracle).serve_forever()


if __name__ == "__main__":
    main()
