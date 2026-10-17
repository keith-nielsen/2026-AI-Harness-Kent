"""Recording proxy against a fake upstream: bytes pass through unchanged, streamed tool calls are
reassembled for the log, the seed is injected only while an item is set and is stable per turn."""
from __future__ import annotations

import gzip
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import importlib.util

spec = importlib.util.spec_from_file_location("proxy", Path(__file__).with_name("proxy.py"))
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)

CHUNKS = [
    {"model": "m", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hel"}}]},
    {"model": "m", "choices": [{"index": 0, "delta": {"content": "lo"}}]},
    {"model": "m", "choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": "{\"pa"}}]}}]},
    {"model": "m", "choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": "th\": \"a.txt\"}"}}]}}]},
    {"model": "m", "choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 1, "id": "c2", "function": {"name": "web_search", "arguments": "{bad"}}]}}]},
    {"model": "m", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
     "usage": {"prompt_tokens": 11, "completion_tokens": 7}, "timings": {"prompt_ms": 5.0, "predicted_ms": 9.0}},
]


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        b = b'{"status":"ok"}'
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Upstream.seen.append(req)
        if req.get("stream"):
            self.send_response(200); self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close"); self.end_headers()
            for c in CHUNKS:
                self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n"); self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            self.close_connection = True
        else:
            b = json.dumps({"model": "m", "choices": [{"index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "hi"}}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode()
            self.send_response(200); self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def px(tmp_path):
    up = serve(Upstream)
    Upstream.seen = []
    state = proxy.State(tmp_path)
    p = serve(proxy.make_handler(state, proxy.urllib.parse.urlsplit(f"http://127.0.0.1:{up.server_port}")))
    yield f"http://127.0.0.1:{p.server_port}", tmp_path
    p.shutdown(); up.shutdown()


def post(url, obj):
    r = urllib.request.urlopen(urllib.request.Request(url, json.dumps(obj).encode(),
                                                      {"content-type": "application/json"}), timeout=10)
    return r.read()


def log(tmp):
    return [json.loads(l) for l in (tmp / "requests.jsonl").read_text().splitlines()]


def test_get_passes_through_and_is_not_logged(px):
    url, tmp = px
    assert json.loads(urllib.request.urlopen(url + "/health").read()) == {"status": "ok"}
    assert not (tmp / "requests.jsonl").exists()


def test_stream_bytes_unchanged_and_tool_calls_reassembled(px):
    url, tmp = px
    raw = post(url + "/v1/chat/completions", {"model": "fast", "stream": True, "messages": [{"role": "user", "content": "x"}],
                                              "tools": [{"type": "function", "function": {"name": "read_file"}}]})
    expect = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in CHUNKS) + b"data: [DONE]\n\n"
    assert raw == expect
    rec = log(tmp)[0]
    assert rec["finish"] == "tool_calls" and rec["stream"] and rec["tools_offered"] == 1
    assert rec["prompt_tokens"] == 11 and rec["completion_tokens"] == 7 and rec["predicted_ms"] == 9.0
    assert rec["tool_calls"] == [{"name": "read_file", "args_ok": True, "args_len": 17},
                                 {"name": "web_search", "args_ok": False, "args_len": 4}]
    body = json.load(gzip.open(tmp / "bodies" / "000001.json.gz", "rt"))
    assert body["response"]["choices"][0]["message"]["content"] == "Hello"


def test_seed_only_with_item_and_stable_per_turn(px):
    url, tmp = px
    msg = {"model": "fast", "messages": [{"role": "user", "content": "x"}]}
    post(url + "/v1/chat/completions", msg)
    assert "seed" not in Upstream.seen[-1]
    seeds = []
    for _ in range(2):
        post(url + "/_bench/item", {"item": "s1-007", "rep": 1})
        post(url + "/v1/chat/completions", msg); post(url + "/v1/chat/completions", msg)
        seeds.append((Upstream.seen[-2]["seed"], Upstream.seen[-1]["seed"]))
    assert seeds[0] == seeds[1] and seeds[0][0] != seeds[0][1]        # same item+rep -> same seeds; turns differ
    post(url + "/_bench/item", {"item": "s1-007", "rep": 2})
    post(url + "/v1/chat/completions", msg)
    assert Upstream.seen[-1]["seed"] != seeds[0][0]                    # another repeat -> another seed
    post(url + "/v1/chat/completions", {**msg, "seed": 42})
    assert Upstream.seen[-1]["seed"] == 42                             # a caller's own seed is kept
    recs = log(tmp)
    assert recs[0]["item"] is None and recs[1]["item"] == "s1-007" and recs[1]["turn"] == 0 and recs[2]["turn"] == 1


def test_header_attribution_overrides_current_item(px):
    url, tmp = px
    msg = json.dumps({"model": "fast", "messages": [{"role": "user", "content": "x"}]}).encode()
    def hpost(item, rep):
        urllib.request.urlopen(urllib.request.Request(url + "/v1/chat/completions", msg, {
            "content-type": "application/json", "x-bench-item": item, "x-bench-rep": str(rep)}), timeout=10).read()
    post(url + "/_bench/item", {"item": "global", "rep": 0})
    hpost("p1", 0); hpost("p2", 0); hpost("p1", 0)
    recs = log(tmp)
    assert [(r["item"], r["turn"]) for r in recs] == [("p1", 0), ("p2", 0), ("p1", 1)]
    assert recs[0]["seed"] != recs[1]["seed"] and Upstream.seen[0].get("seed") == recs[0]["seed"]
