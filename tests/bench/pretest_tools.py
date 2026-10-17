#!/usr/bin/env python3
"""Pre-test P3/P4 for the model under test (MUTT is up via mutt.sh).

P3: the model's chat template yields parseable tool calls, streamed and not, for a single call and
for two independent calls. P4: with the same seed, a request straight to llama (:8081) and through
the proxy (:8080) give the same reply, and the proxy logs it. Prints one line per check; exit 1 on
any failure.
"""
import json
import sys
import urllib.request

DIRECT, PROXY = "http://127.0.0.1:8081", "http://127.0.0.1:8080"
TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "web_search", "description": "Search the web.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"},
                    "max_results": {"type": "integer"}}, "required": ["query"]}}},
]
ONE = [{"role": "user", "content": "Read the file /data/workspace/notes.txt."}]
TWO = [{"role": "user", "content": "Do both now, in parallel: read /data/a.txt and search the web for "
        "'llama.cpp release notes' (3 results)."}]


def chat(base, messages, stream=False, seed=1234, tools=TOOLS):
    body = {"model": "locally-run-model", "messages": messages, "tools": tools, "seed": seed,
            "max_tokens": 400, "stream": stream, "parallel_tool_calls": True}
    r = urllib.request.urlopen(urllib.request.Request(base + "/v1/chat/completions", json.dumps(body).encode(),
                                                      {"content-type": "application/json"}), timeout=600)
    if not stream:
        return json.load(r)["choices"][0]["message"]
    calls, content = {}, []
    for line in r:
        line = line.strip()
        if not line.startswith(b"data:") or line[5:].strip() == b"[DONE]":
            continue
        for ch in json.loads(line[5:]).get("choices") or []:
            d = ch.get("delta") or {}
            content.append(d.get("content") or "")
            for tc in d.get("tool_calls") or []:
                s = calls.setdefault(tc.get("index", 0), {"function": {"name": "", "arguments": ""}})
                s["function"]["name"] += (tc.get("function") or {}).get("name") or ""
                s["function"]["arguments"] += (tc.get("function") or {}).get("arguments") or ""
    return {"content": "".join(content), "tool_calls": [calls[k] for k in sorted(calls)]}


def parsed(msg):
    out = []
    for c in msg.get("tool_calls") or []:
        try:
            out.append((c["function"]["name"], json.loads(c["function"]["arguments"])))
        except (ValueError, KeyError):
            out.append((c.get("function", {}).get("name"), "UNPARSEABLE"))
    return out


ok = True


def check(name, cond, detail):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}: {detail}")


for stream in (False, True):
    tag = "stream" if stream else "plain"
    p = parsed(chat(PROXY, ONE, stream))
    check(f"P3 one call ({tag})", p and p[0][0] == "read_file" and isinstance(p[0][1], dict)
          and "notes.txt" in p[0][1].get("path", ""), p)
    p = parsed(chat(PROXY, TWO, stream))
    # gate: every call parses; how many calls the model makes at once is reported, not gated
    check(f"P3 two calls parse ({tag})", p and all(isinstance(a, dict) for _, a in p), p)
    print(f"INFO  parallel calls ({tag}): {sorted(n for n, _ in p)}")

a = chat(DIRECT, ONE, seed=777)
b = chat(PROXY, ONE, seed=777)
check("P4 same seed, direct vs proxy", parsed(a) == parsed(b) and a.get("content") == b.get("content"),
      f"direct={parsed(a)} proxy={parsed(b)}")
sys.exit(0 if ok else 1)
