#!/usr/bin/env python3
"""Load test for llama-server: decode throughput under concurrency, prefill, and decode-during-prefill.

  bench.py URL LABEL MAXCONC [OUT.json]

Every prompt starts with a unique nonce and uses cache_prompt=false, so nothing is reused.
Numbers come from the server's own `timings` plus wall-clock time.
"""
import json, sys, time, uuid, glob, threading, urllib.request

URL, LABEL, MAXC = sys.argv[1], sys.argv[2], int(sys.argv[3])
OUT = sys.argv[4] if len(sys.argv) > 4 else None
REPO = "/home/administrator/Documents/repo/harness-kent"
CORPUS = "\n\n".join(open(f, errors="ignore").read() for f in sorted(glob.glob(f"{REPO}/docs/**/*.md", recursive=True)))


def post(path, body, timeout=1800):
    req = urllib.request.Request(URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ntok(text):
    return len(post("/tokenize", {"content": text})["tokens"])


CPT = None  # chars per token, calibrated once


def prompt(tokens):
    global CPT
    if CPT is None:
        sample = CORPUS[:40000]
        CPT = len(sample) / ntok(sample)
    nonce = f"[{uuid.uuid4().hex}] "
    need = int(tokens * CPT)
    body = (CORPUS * (need // len(CORPUS) + 1))[:need]
    start = hash(nonce) % max(1, len(CORPUS) - 1)  # different offset per request too
    body = (CORPUS[start:] + CORPUS)[:need] if len(CORPUS) > need else body
    return nonce + body + "\n\nContinue writing:"


def complete(ptoks, npred, res, i):
    t0 = time.time()
    r = post("/completion", {"prompt": prompt(ptoks), "n_predict": npred, "ignore_eos": True,
                             "cache_prompt": False, "temperature": 0.7})
    t = r["timings"]
    res[i] = {"wall": time.time() - t0, "p_n": t["prompt_n"], "p_ms": t["prompt_ms"],
              "g_n": t["predicted_n"], "g_ms": t["predicted_ms"], "t0": t0}


def run(conc, ptoks, npred, delays=None):
    res = [None] * conc
    ths = []
    t0 = time.time()
    for i in range(conc):
        if delays and delays[i]:
            time.sleep(delays[i])
        th = threading.Thread(target=complete, args=(ptoks[i] if isinstance(ptoks, list) else ptoks,
                                                     npred[i] if isinstance(npred, list) else npred, res, i))
        th.start(); ths.append(th)
    for th in ths:
        th.join()
    return time.time() - t0, res


results = {"label": LABEL, "url": URL}
props = post("/props", {}) if False else json.load(urllib.request.urlopen(URL + "/props"))
results["slots"], results["n_ctx"] = props.get("total_slots"), props["default_generation_settings"]["n_ctx"]
print(f"== {LABEL}: slots={results['slots']} n_ctx={results['n_ctx']}", flush=True)

run(1, 256, 16)  # warm-up

# 1. Decode: 1k prompt, 256 generated, at each concurrency up to MAXC.
results["decode"] = {}
for c in [x for x in (1, 2, 4) if x <= MAXC]:
    wall, res = run(c, 1000, 256)
    g = sum(r["g_n"] for r in res)
    per = [r["g_n"] / r["g_ms"] * 1000 for r in res]
    results["decode"][c] = {"wall": wall, "agg_gen_tps": g / wall, "per_req_gen_tps": per,
                            "per_req_pp_tps": [r["p_n"] / r["p_ms"] * 1000 for r in res]}
    print(f"decode c={c}: aggregate {g / wall:6.1f} tok/s over {wall:5.1f}s; per request "
          + " ".join(f"{x:5.1f}" for x in per), flush=True)

# 2. Prefill: 16k prompt, 16 generated.
results["prefill"] = {}
for c in [x for x in (1, 2) if x <= MAXC]:
    wall, res = run(c, 16000, 16)
    p = sum(r["p_n"] for r in res)
    results["prefill"][c] = {"wall": wall, "agg_pp_tps": p / wall,
                             "per_req_pp_tps": [r["p_n"] / r["p_ms"] * 1000 for r in res]}
    print(f"prefill c={c}: aggregate {p / wall:7.1f} tok/s over {wall:5.1f}s", flush=True)

# 3. Interference: a short request decoding 512 tokens; 3 s in, a 32k-token prompt arrives.
if MAXC >= 2:
    wall, res = run(2, [500, 32000], [512, 16], delays=[0, 3])
    a, b = res
    results["interference"] = {"wall": wall, "decode_req": a, "prefill_req": b,
                               "decode_tps": a["g_n"] / a["g_ms"] * 1000,
                               "prefill_tps": b["p_n"] / b["p_ms"] * 1000}
    print(f"interference: decoder {results['interference']['decode_tps']:5.1f} tok/s "
          f"(wall {a['wall']:5.1f}s), 32k prefill {results['interference']['prefill_tps']:6.1f} tok/s "
          f"(wall {b['wall']:5.1f}s)", flush=True)

if OUT:
    json.dump(results, open(OUT, "w"), indent=1)
