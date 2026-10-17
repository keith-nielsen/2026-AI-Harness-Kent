#!/usr/bin/env python3
"""Speculative-decoding check: output speed and draft acceptance on Kent-shaped work.

  spec_bench.py URL LABEL OUT.json [REPS]

Three workloads, run REPS times (default 3) with production sampling and once greedy (temperature 0):
  edit     return a whole repo file with one small change (the reply mostly copies the prompt)
  agentic  a tool transcript (command output) and a report that quotes from it
  prose    a short explanation that copies nothing (the control)
Every request starts with a unique nonce and uses cache_prompt=false, so nothing is reused between runs.
NOTE (2026-10-05): the nonce makes greedy texts incomparable across variants; mtp_probe.py is the
deterministic version (fixed prompts, reuse checked from the server's own counters).
Numbers come from the server's own `timings` (predicted_per_second, draft_n, draft_n_accepted).
The greedy texts are saved so a later report can compare them with the baseline's.
"""
import json, statistics, sys, time, uuid, urllib.request

URL, LABEL, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
REPS = int(sys.argv[4]) if len(sys.argv) > 4 else 3
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from workloads import WORKLOADS  # noqa: E402


def chat(messages, max_tokens, temperature=None):
    body = {"messages": [{"role": "system", "content": f"[{uuid.uuid4().hex}]"}] + messages,
            "max_tokens": max_tokens, "cache_prompt": False}
    if temperature is not None:
        body["temperature"] = temperature
    req = urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        resp = json.load(r)
    t = resp.get("timings", {})
    return {"wall": round(time.time() - t0, 2), "gen_n": t.get("predicted_n"),
            "gen_tps": t.get("predicted_per_second"), "pp_tps": t.get("prompt_per_second"),
            "draft_n": t.get("draft_n", 0), "draft_acc": t.get("draft_n_accepted", 0),
            "finish": resp["choices"][0].get("finish_reason")}, resp["choices"][0]["message"].get("content") or ""


chat([{"role": "user", "content": "Say ok."}], 8)  # warm-up
res = {"label": LABEL, "url": URL, "reps": REPS, "workloads": {}}
for name, (msgs, mt) in WORKLOADS.items():
    runs = []
    for i in range(REPS):
        m, _ = chat(msgs, mt)
        runs.append(m)
        print(f"{LABEL} {name} {i + 1}/{REPS}: {m['gen_tps']:.1f} tok/s, {m['gen_n']} tok, "
              f"drafts {m['draft_acc']}/{m['draft_n']}", flush=True)
    g, text = chat(msgs, mt, temperature=0)
    tps = [r["gen_tps"] for r in runs]
    dn, da = sum(r["draft_n"] for r in runs), sum(r["draft_acc"] for r in runs)
    res["workloads"][name] = {"runs": runs, "mean_tps": statistics.mean(tps),
                              "stdev_tps": statistics.stdev(tps) if len(tps) > 1 else 0.0,
                              "accept_rate": da / dn if dn else None, "greedy": g, "greedy_text": text}
    print(f"{LABEL} {name}: mean {statistics.mean(tps):.1f} tok/s; greedy {g['gen_tps']:.1f} tok/s", flush=True)
json.dump(res, open(OUT, "w"), indent=1)
