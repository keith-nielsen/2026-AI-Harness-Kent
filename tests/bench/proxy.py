#!/usr/bin/env python3
"""Recording pass-through between the gateway and the model under test (MUTT).

  proxy.py LISTEN_PORT UPSTREAM_URL LOG_DIR

Forwards every request unchanged to UPSTREAM_URL (streamed responses are relayed chunk by
chunk), except that chat completions get a `seed` when the bench has set a current item.
Each chat completion becomes one line in LOG_DIR/requests.jsonl (item, tokens, finish reason,
tool calls and whether their arguments parse, llama timings) and its full request and
reassembled response are kept in LOG_DIR/bodies/<n>.json.gz for later analysis.

Control (bench runner only, loopback): POST /_bench/item {"item": "...", "rep": 0} sets the
item that following requests are attributed to; {"item": null} clears it. GET /_bench/stats.
A request may instead carry X-Bench-Item / X-Bench-Rep headers (parallel probes): they override the
current item for that request only, with their own turn counter.

Environment (for an upstream that is not llama-server, e.g. Strata, 2026-10-07):
  PROXY_AUTH_FILE    a file holding an API key: sent upstream as "Authorization: Bearer <key>"
  PROXY_SET_PARAMS   a JSON object: its keys are added to every chat completion that does not set them itself
                     (e.g. thinking off and the llama-server sampling defaults the Qwen3.6 runs had)
"""
from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection",
       "content-length", "host", "x-bench-item", "x-bench-rep"}


class State:
    def __init__(self, log_dir: Path):
        self.lock = threading.Lock()
        self.item: str | None = None
        self.rep = 0
        self.turn = 0          # chat completions seen for the current item
        self.n = 0             # chat completions seen in total
        self.turns: dict = {}  # per (item, rep) turn counters for header-attributed requests
        self.log_dir = log_dir
        (log_dir / "bodies").mkdir(parents=True, exist_ok=True)

    def next_request(self, item: str | None = None, rep: int | None = None) -> tuple[int, str | None, int, int, int | None]:
        with self.lock:
            self.n += 1
            if item is not None:                       # per-request attribution (header)
                key = (item, rep or 0)
                turn = self.turns.get(key, 0)
                self.turns[key] = turn + 1
                rep = rep or 0
            else:
                item, rep, turn = self.item, self.rep, self.turn
                self.turn += 1
            seed = None
            if item is not None:
                h = hashlib.sha256(f"{item}|{rep}|{turn}".encode()).digest()
                seed = int.from_bytes(h[:4], "big") & 0x7FFFFFFF
            return self.n, item, rep, turn, seed

    def write(self, rec: dict, body: dict):
        with self.lock:
            with open(self.log_dir / "requests.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
        with gzip.open(self.log_dir / "bodies" / f"{rec['n']:06d}.json.gz", "wt") as f:
            json.dump(body, f)


def tool_summary(calls: list[dict]) -> list[dict]:
    out = []
    for c in calls or []:
        fn = c.get("function") or {}
        args = fn.get("arguments")
        try:
            ok = isinstance(json.loads(args), dict) if isinstance(args, str) else isinstance(args, dict)
        except ValueError:
            ok = False
        out.append({"name": fn.get("name"), "args_ok": ok, "args_len": len(args or "") if isinstance(args, str) else None})
    return out


def merge_stream(events: list[dict]) -> dict:
    """Rebuild a non-streamed response (message, finish reason, usage, timings) from SSE chunks."""
    content, reasoning, calls = [], [], {}
    finish, usage, timings, model = None, None, None, None
    for ev in events:
        model = ev.get("model", model)
        usage = ev.get("usage") or usage
        timings = ev.get("timings") or timings
        for ch in ev.get("choices") or []:
            finish = ch.get("finish_reason") or finish
            d = ch.get("delta") or {}
            if d.get("content"):
                content.append(d["content"])
            if d.get("reasoning_content"):
                reasoning.append(d["reasoning_content"])
            for tc in d.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0), {"id": None, "type": "function",
                                                             "function": {"name": "", "arguments": ""}})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
    msg = {"role": "assistant", "content": "".join(content) or None}
    if reasoning:
        msg["reasoning_content"] = "".join(reasoning)
    if calls:
        msg["tool_calls"] = [calls[k] for k in sorted(calls)]
    return {"model": model, "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": usage, "timings": timings}


AUTH = (Path(os.environ["PROXY_AUTH_FILE"]).read_text().strip() if os.environ.get("PROXY_AUTH_FILE") else None)
SET_PARAMS = json.loads(os.environ.get("PROXY_SET_PARAMS") or "{}")


def make_handler(state: State, upstream: urllib.parse.SplitResult):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def _send_json(self, code: int, obj: dict):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _control(self, body: bytes):
            if self.command == "GET" and self.path == "/_bench/stats":
                return self._send_json(200, {"item": state.item, "rep": state.rep, "requests": state.n})
            try:
                d = json.loads(body or b"{}")
            except ValueError:
                return self._send_json(400, {"error": "bad json"})
            with state.lock:
                state.item, state.rep, state.turn = d.get("item"), int(d.get("rep") or 0), 0
            return self._send_json(200, {"item": state.item, "rep": state.rep})

        def _forward(self):
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            if self.path.startswith("/_bench/"):
                return self._control(body)
            chat = self.command == "POST" and self.path.rstrip("/").endswith("/chat/completions")
            req, meta = None, None
            if chat:
                try:
                    req = json.loads(body)
                except ValueError:
                    req = None
                if isinstance(req, dict):
                    hi = self.headers.get("x-bench-item")
                    hr = self.headers.get("x-bench-rep")
                    n, item, rep, turn, seed = state.next_request(hi, int(hr) if hr and hr.isdigit() else None)
                    if seed is not None and "seed" not in req:
                        req["seed"] = seed
                    for k, v in SET_PARAMS.items():
                        req.setdefault(k, v)
                    body = json.dumps(req).encode()
                    meta = {"n": n, "item": item, "rep": rep, "turn": turn, "seed": req.get("seed"),
                            "t0": time.time()}
            conn = http.client.HTTPConnection(upstream.hostname, upstream.port, timeout=3600)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            headers["content-length"] = str(len(body))
            if AUTH:
                headers["Authorization"] = "Bearer " + AUTH
            try:
                conn.request(self.command, self.path, body=body if body else None, headers=headers)
                resp = conn.getresponse()
            except OSError as e:
                return self._send_json(502, {"error": f"upstream unreachable: {e}"})
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            streaming = "text/event-stream" in (resp.getheader("content-type") or "")
            raw = b""
            if streaming:
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                events, buf = [], b""
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                    if meta:
                        buf += chunk
                        while b"\n\n" in buf:
                            block, buf = buf.split(b"\n\n", 1)
                            for line in block.split(b"\n"):
                                if line.startswith(b"data:"):
                                    data = line[5:].strip()
                                    if data and data != b"[DONE]":
                                        try:
                                            events.append(json.loads(data))
                                        except ValueError:
                                            pass
                if meta:   # log first: once a caller sees the end of the response, its record exists
                    self._record(meta, req, merge_stream(events), resp.status, True)
                self.wfile.write(b"0\r\n\r\n")
            else:
                raw = resp.read()
                if meta:
                    try:
                        out = json.loads(raw)
                    except ValueError:
                        out = {"error_body": raw[:2000].decode(errors="replace")}
                    self._record(meta, req, out if isinstance(out, dict) else {}, resp.status, False)
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            conn.close()

        def _record(self, meta, req, out, status, streaming):
            ch = (out.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            usage = out.get("usage") or {}
            t = out.get("timings") or {}
            tools = req.get("tools") or []
            rec = {**meta, "t1": time.time(), "status": status, "stream": streaming, "model": req.get("model"),
                   "messages": len(req.get("messages") or []), "tools_offered": len(tools),
                   "finish": ch.get("finish_reason"),
                   "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                   "content_chars": len(msg.get("content") or ""),
                   "tool_calls": tool_summary(msg.get("tool_calls")),
                   "prompt_ms": t.get("prompt_ms"), "predicted_ms": t.get("predicted_ms"),
                   "error": out.get("error") or out.get("error_body")}
            state.write(rec, {"request": req, "response": out})

        do_GET = do_POST = do_PUT = do_DELETE = _forward

    return H


def main():
    port, upstream, log_dir = int(sys.argv[1]), urllib.parse.urlsplit(sys.argv[2]), Path(sys.argv[3])
    state = State(log_dir)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, upstream))
    srv.daemon_threads = True
    print(f"proxy 127.0.0.1:{port} -> {upstream.geturl()} logging to {log_dir}"
          f"{' (auth key from PROXY_AUTH_FILE)' if AUTH else ''}{f', set params {SET_PARAMS}' if SET_PARAMS else ''}",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
