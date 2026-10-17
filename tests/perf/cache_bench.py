#!/usr/bin/env python3
"""Prefix-reuse check: how much of a repeated prompt the server reads again (Hermes-shaped chats).

  cache_bench.py URL LABEL OUT.json

A fixed ~8k-token system prompt (the start of docs/architecture.md), short questions, 48-token greedy answers,
prompt caching on (the server default). Steps, on a freshly started server:
  A1  new chat A                      (reads everything: the reference)
  A2  A continues                     (should read only the new turn)
  B1  new chat B, same system prompt  (the Strata question: is the system prompt reused?)
  B2  B continues
  A3  back to A after B               (does A survive B on the other slot / in the RAM cache?)
  C1+D1  two new chats at the same time
Reported per step: prompt_n (tokens actually read), cache_n (tokens reused) and prompt_ms (time to first token).
"""
import json, sys, threading, time, urllib.request

URL, LABEL, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
REPO = "/home/administrator/Documents/repo/harness-kent"
SYSTEM = open(f"{REPO}/docs/architecture.md").read()[:30000]


def chat(messages):
    body = {"messages": [{"role": "system", "content": SYSTEM}] + messages, "max_tokens": 48, "temperature": 0}
    req = urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        resp = json.load(r)
    t = resp["timings"]
    return ({"prompt_n": t.get("prompt_n"), "cache_n": t.get("cache_n"), "prompt_ms": round(t.get("prompt_ms", 0)),
             "wall": round(time.time() - t0, 2)}, resp["choices"][0]["message"].get("content") or "")


res = {"label": LABEL, "url": URL, "steps": {}}


def step(name, msgs):
    m, ans = chat(msgs)
    res["steps"][name] = m
    print(f"{LABEL} {name}: read {m['prompt_n']}, reused {m['cache_n']}, {m['prompt_ms']} ms", flush=True)
    return msgs + [{"role": "assistant", "content": ans}]


a = step("A1", [{"role": "user", "content": "Chat A. Which version does this document describe?"}])
a = step("A2", a + [{"role": "user", "content": "Which service holds the cloud key?"}])
b = step("B1", [{"role": "user", "content": "Chat B. What runs the local model?"}])
b = step("B2", b + [{"role": "user", "content": "How many slots does it run?"}])
a = step("A3", a + [{"role": "user", "content": "Summarise your two answers in one line."}])

out = {}
def conc(name, q):
    out[name] = chat([{"role": "user", "content": q}])[0]
ths = [threading.Thread(target=conc, args=("C1", "Chat C. What is a Gent?")),
       threading.Thread(target=conc, args=("D1", "Chat D. What is the gateway?"))]
for th in ths: th.start()
for th in ths: th.join()
for k, m in out.items():
    res["steps"][k] = m
    print(f"{LABEL} {k}: read {m['prompt_n']}, reused {m['cache_n']}, {m['prompt_ms']} ms", flush=True)
json.dump(res, open(OUT, "w"), indent=1)
