#!/usr/bin/env python3
"""Context-depth probe: long prompts in equal-volume buckets, two tasks each, greedy and seeded-sampled; one CSV row
per request.

  depth_probe.py URL VARIANT ROUND CSV SERVER_PID CORPUS

Buckets (equal token volume, 64K each): 8 x 8K, 4 x 16K, 2 x 32K, 1 x 64K prompt tokens. Each prompt is an exact-length
slice of the frozen CORPUS (tokenized by the server itself), followed by a task:
  copy   reproduce the last 25 lines verbatim, then one sentence on what they are   (favours drafting)
  prose  about 200 words on what the text covers                                    (does not)
Per prompt, in this order: copy greedy (cache_prompt=false: the full prompt is read, the prefill measurement),
prose greedy, copy sampled, prose sampled. The later three reuse the cached long prefix, as a follow-up turn would.
Sampled = temperature 0.7 with a fixed seed per prompt and task (repeatable); the other sampling settings are the
server's production defaults. sysmon flags every request (see sysmon.py). Rows are appended, so several variants and
rounds share one CSV.
"""
import csv, hashlib, json, os, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sysmon  # noqa: E402

URL, VARIANT, ROUND, CSV_OUT, PID, CORPUS = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6]
BUCKETS = [(8192, 8), (16384, 4), (32768, 2), (65536, 1)]
if os.environ.get("BUCKETS"):   # e.g. BUCKETS=4096x1 for a smoke test
    BUCKETS = [(int(a), int(b)) for a, b in (x.split("x") for x in os.environ["BUCKETS"].split(","))]
TASKS = {"copy": ("Reproduce verbatim the last 25 lines of the text above in one code block, then say in one sentence "
                  "what they are.", 700),
         "prose": ("In about 200 words, summarize what the text above covers.", 450)}
RESERVE = 160          # tokens left for the chat template and the task line, so the whole prompt lands near the bucket
COLS = ["variant", "round", "bucket_k", "prompt", "task", "sampling", "seed", "prompt_tokens", "cache_n", "prefill_tok_s",
        "ttft_s", "gen_tokens", "decode_tok_s", "draft_n", "draft_acc", "accept_rate", "steps", "ms_per_step",
        "finish", "text_sha", "cpu_mhz_busy", "other_cores", "other_kernel_cores", "irq_cores", "flags", "corpus_sha",
        "unix_time"]


def post(path, body, timeout=3600):
    req = urllib.request.Request(URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


corpus = open(CORPUS).read()
corpus_sha = hashlib.sha256(corpus.encode()).hexdigest()[:16]
toks = post("/tokenize", {"content": corpus})["tokens"]
print(f"{VARIANT} r{ROUND}: corpus {len(toks)} tokens ({corpus_sha})", flush=True)

new = not os.path.exists(CSV_OUT)
out = open(CSV_OUT, "a", newline="")
w = csv.DictWriter(out, fieldnames=COLS)
if new:
    w.writeheader()

post("/v1/chat/completions", {"messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 8})  # warm-up
for bi, (size, count) in enumerate(BUCKETS):
    for p in range(count):
        start = (bi * 7919 + p * size) % max(1, len(toks) - size)      # distinct slices within a bucket
        body = post("/detokenize", {"tokens": toks[start:start + size - RESERVE]})["content"]
        for sampling in ("greedy", "sampled"):
            for task, (line, mt) in TASKS.items():
                seed = 1000 * (bi + 1) + 10 * p + (1 if task == "prose" else 0)
                req = {"messages": [{"role": "user", "content": "Here are files from a software repository:\n\n" + body
                                     + "\n\n" + line}], "max_tokens": mt,
                       "cache_prompt": not (sampling == "greedy" and task == "copy")}
                req.update({"temperature": 0} if sampling == "greedy" else {"temperature": 0.7, "seed": seed})
                t0 = time.time()
                with sysmon.Window(PID) as sw:
                    r = post("/v1/chat/completions", req)
                t = r["timings"]
                n, dn, da = t["predicted_n"], t.get("draft_n", 0), t.get("draft_n_accepted", 0)
                steps = max(1, n - da)
                text = r["choices"][0]["message"].get("content") or ""
                flags = list(sw.result["flags"])
                if req["cache_prompt"] is False and t.get("cache_n", 0):
                    flags.append(f"reuse cache_n={t['cache_n']}")
                row = {"variant": VARIANT, "round": ROUND, "bucket_k": size // 1024, "prompt": p, "task": task,
                       "sampling": sampling, "seed": seed if sampling == "sampled" else "",
                       "prompt_tokens": t["prompt_n"] + t.get("cache_n", 0), "cache_n": t.get("cache_n", 0),
                       "prefill_tok_s": round(t["prompt_per_second"], 1) if t["prompt_n"] > 64 else "",
                       "ttft_s": round(t["prompt_ms"] / 1000, 2), "gen_tokens": n,
                       "decode_tok_s": round(t["predicted_per_second"], 2), "draft_n": dn, "draft_acc": da,
                       "accept_rate": round(da / dn, 3) if dn else "", "steps": steps,
                       "ms_per_step": round(t["predicted_ms"] / steps, 1),
                       "finish": r["choices"][0].get("finish_reason"),
                       "text_sha": hashlib.sha256(text.encode()).hexdigest()[:16],
                       "cpu_mhz_busy": sw.result.get("cpu_avg_mhz"), "other_cores": sw.result.get("other_cores"),
                       "other_kernel_cores": sw.result.get("other_kernel_cores"), "irq_cores": sw.result.get("irq_cores"),
                       "flags": " ".join(flags), "corpus_sha": corpus_sha, "unix_time": int(t0)}
                w.writerow(row); out.flush()
                print(f"{VARIANT} r{ROUND} {size // 1024}K p{p} {task} {sampling}: prompt {row['prompt_tokens']} "
                      f"(reused {row['cache_n']}) prefill {row['prefill_tok_s']} tok/s, decode {row['decode_tok_s']} tok/s "
                      f"{n} tok, drafts {da}/{dn}  {row['flags'] or 'clean'}", flush=True)
out.close()
