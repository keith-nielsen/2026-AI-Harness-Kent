#!/usr/bin/env python3
"""Deterministic MTP probe: one server variant, the three fixed workloads, greedy, system-monitored.

  mtp_probe.py URL LABEL OUT.json SERVER_PID

Fixed prompts (workloads.py, no nonce), temperature 0. cache_prompt=false, and every request is checked against
the server's own counters: cache_n must be 0 and prompt_n the full prompt (else the request is flagged "reuse").
Per request: tokens, drafts offered/accepted, generation ms -> decode steps and ms per step; the generated
text (to compare variants token for token); and sysmon.Window's record of the machine during the request.
CONCURRENT=1: the edit and agentic requests run at the same time on the server's two slots (production runs
2 slots); recorded as workload "pair": both requests' records plus aggregate tok/s = all generated tokens /
the longest generation time, and wall-clock tokens per second over the whole window.
"""
import hashlib, json, os, sys, threading, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from workloads import WORKLOADS  # noqa: E402
import sysmon  # noqa: E402

URL, LABEL, OUT, PID = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])


def chat(messages, max_tokens):
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0, "cache_prompt": False}
    req = urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)


chat([{"role": "user", "content": "Say ok."}], 8)  # warm-up (CUDA graphs, first-use allocations)
res = {"label": LABEL, "url": URL, "pid": PID, "static": sysmon.static_state(), "started": time.time(), "requests": {}}
ONLY = [w for w in os.environ.get("WORKLOADS", "").split(",") if w]   # e.g. WORKLOADS=edit,agentic


def record(resp, sysres):
    t = resp["timings"]
    text = resp["choices"][0]["message"].get("content") or ""
    n, dn, da, ms = t["predicted_n"], t.get("draft_n", 0), t.get("draft_n_accepted", 0), t["predicted_ms"]
    steps = n - da                                   # every step yields one sampled token plus its accepted drafts
    flags = list(sysres["flags"])
    if t.get("cache_n", 0) != 0: flags.append(f"reuse cache_n={t['cache_n']}")
    rec = {"prompt_n": t["prompt_n"], "cache_n": t.get("cache_n"), "gen_n": n, "draft_n": dn, "draft_acc": da,
           "gen_ms": ms, "tok_s": n / ms * 1000, "steps": steps, "ms_per_step": ms / steps,
           "tok_per_step": n / steps, "text_sha": hashlib.sha256(text.encode()).hexdigest()[:16], "text": text,
           "finish": resp["choices"][0].get("finish_reason"), "sys": sysres, "flags": flags}
    return rec


if os.environ.get("CONCURRENT") == "1":
    pair, out = ("edit", "agentic"), {}
    def go(name):
        out[name] = chat(*WORKLOADS[name])
    t0 = time.time()
    with sysmon.Window(PID) as w:
        ths = [threading.Thread(target=go, args=(n,)) for n in pair]
        for th in ths: th.start()
        for th in ths: th.join()
    wall = time.time() - t0
    recs = {n: record(out[n], w.result) for n in pair}
    tokens = sum(r["gen_n"] for r in recs.values())
    agg = tokens / max(r["gen_ms"] for r in recs.values()) * 1000
    flags = sorted({f for r in recs.values() for f in r["flags"]})
    res["requests"]["pair"] = {"members": recs, "gen_n": tokens, "agg_tok_s": agg, "wall_s": wall,
                               "wall_tok_s": tokens / wall, "flags": flags, "sys": w.result}
    print(f"{LABEL} pair: {agg:.2f} tok/s aggregate ({' + '.join(f'{recs[n]['tok_s']:.2f}' for n in pair)})  "
          f"{tokens} tok  wall {wall:.1f} s  {' '.join(flags) or 'clean'}", flush=True)
else:
    for name, (msgs, mt) in WORKLOADS.items():
        if ONLY and name not in ONLY:
            continue
        with sysmon.Window(PID) as w:
            resp = chat(msgs, mt)
        rec = record(resp, w.result)
        res["requests"][name] = rec
        print(f"{LABEL} {name}: {rec['tok_s']:.2f} tok/s  {rec['gen_n']} tok  {rec['steps']} steps  "
              f"{rec['ms_per_step']:.1f} ms/step  drafts {rec['draft_acc']}/{rec['draft_n']}  text {rec['text_sha']}  "
              f"{' '.join(rec['flags']) or 'clean'}", flush=True)
json.dump(res, open(OUT, "w"), indent=1)
