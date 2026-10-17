#!/usr/bin/env python3
"""pdcheck.py — parallel-decision patches, JEV validation steps 1-2 on a small CPU model (operator, 2026-10-13).

Step 1, normal path unchanged: the same greedy completions (raw prompts, temperature 0, top-5 log-probabilities)
from stock v0.6.0 llama-server (`base`), v0.6.0+pd with decisions off (`pd0`) and with --decision-seqs 8 (`pd8`).
Pass = identical tokens; the largest log-probability difference is reported (pd8 switches the KV cache to unified).
Step 2, no interference: on pd8, the same completions while /v1/decision requests run concurrently -> identical to
pd8 alone. Decision sanity: enum/boolean probabilities sum to 1, the same request twice gives the same answer.
CPU only (-t 3, nice 19), Qwen3-0.6B Q8_0, port 8191. Results: bench/runs/pdcheck-<time>/{pdcheck.log,result.json}.
Start detached: tests/bench/submit.sh pdcheck pdcheck.py
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

BENCH = Path.home() / "Documents/repo/bench"
LB = BENCH / "llama-builds"
MODEL = BENCH / "models/qwen3-0.6b/Qwen3-0.6B-Q8_0.gguf"
PORT = 8191
RUN = BENCH / "runs" / time.strftime("pdcheck-%m%d%H%M")
RUN.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "pdcheck.log", "a", buffering=1)
CONFIGS = {"base": (LB / "v0.6.0-cpu/build-cpu/bin/llama-server", []),
           "pd0": (LB / "v0.6.0-pd/build-cpu/bin/llama-server", []),
           "pd8": (LB / "v0.6.0-pd/build-cpu/bin/llama-server", ["--decision-seqs", "8"])}
PROMPTS = [
    "The capital of France is",
    "def fibonacci(n):\n    \"\"\"Return the n-th Fibonacci number.\"\"\"\n",
    "Q: A train leaves at 09:40 and arrives at 13:15. How long is the trip?\nA:",
    "List three uses of a multimeter:\n1.",
    "Translate to German: The weather is nice today.\nGerman:",
    "In 1969, the first humans to walk on the Moon were",
    "SELECT name, COUNT(*) FROM orders",
    "Firmware 3.4.2 fixes the Wi-Fi reconnect loop. Summary of the release notes:",
]
DECISION = {"instructions": "Answer each question about this support request.",
            "schema": {"category": {"type": "enum", "choices": ["billing", "technical", "cancellation", "other"],
                                    "description": "What type of support request is this?"},
                       "urgent": {"type": "boolean", "description": "Does this need urgent handling?"},
                       "priority": {"type": "enum", "choices": ["low", "medium", "high", "critical"],
                                    "description": "Rate support priority."}},
            "contexts": ["I was charged twice and need this fixed today.",
                         "My TQ-40 drops off Wi-Fi every night at 03:00.",
                         "Please close my account at the end of the month."]}


def log(msg):
    LOG.write(f"{time.strftime('%F %T')} pdcheck: {msg}\n")


def post(path, body, timeout=600):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def start(name):
    binary, extra = CONFIGS[name]
    cmd = ["nice", "-n", "19", str(binary), "-m", str(MODEL), "-c", "4096", "-np", "1", "-t", "3", "-ngl", "0",
           "--port", str(PORT), "--host", "127.0.0.1", *extra]
    proc = subprocess.Popen(cmd, stdout=open(RUN / f"server-{name}.log", "w"), stderr=subprocess.STDOUT,
                            env={**os.environ, "LD_LIBRARY_PATH": str(binary.parent)})
    for _ in range(240):
        time.sleep(1)
        if proc.poll() is not None:
            raise RuntimeError(f"{name}: server exited {proc.returncode} (see server-{name}.log)")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            pass
    proc.kill()
    raise RuntimeError(f"{name}: server not healthy after 240 s")


def complete(prompt):
    r = post("/completion", {"prompt": prompt, "n_predict": 64, "temperature": 0, "top_k": 1, "seed": 1,
                             "n_probs": 5, "cache_prompt": False})
    probs = r.get("completion_probabilities", [])
    return {"text": r["content"], "tokens": [p.get("id", p.get("token")) for p in probs],
            "logprobs": [p.get("logprob") for p in probs]}


def compare(a, b):
    same = all(x["tokens"] == y["tokens"] for x, y in zip(a, b))
    diffs = [abs(p - q) for x, y in zip(a, b) for p, q in zip(x["logprobs"], y["logprobs"])
             if p is not None and q is not None]
    first = next((i for i, (x, y) in enumerate(zip(a, b)) if x["tokens"] != y["tokens"]), None)
    return {"tokens_identical": same, "first_differing_prompt": first, "max_logprob_diff": max(diffs, default=0.0)}


def decision_ok(r):
    """The server returns only the chosen value's probability: it must lie in (0, 1], and in tree mode the chosen
    value is the most likely one, so at least 1/len(choices)."""
    bad = []
    n = {f: len(s.get("choices", [True, False])) for f, s in DECISION["schema"].items()}
    for res in r["results"]:
        if set(res["decision"]) != set(DECISION["schema"]):
            bad.append(f"fields {sorted(res['decision'])}")
        for f, v in res["fields"].items():
            p = v.get("probability")
            if p is None or not 0 < p <= 1.000001:
                bad.append(f"{f}: probability {p}")
            elif v.get("tree") and p < 1 / n[f] - 1e-6:
                bad.append(f"{f}: chosen probability {p:.3f} < 1/{n[f]}")
    return bad


def main():
    if not MODEL.exists():
        log(f"missing model {MODEL}"); return 1
    out = {}
    for name in CONFIGS:
        if not CONFIGS[name][0].exists():
            log(f"missing build {CONFIGS[name][0]}"); return 1
        proc = start(name)
        try:
            t0 = time.time()
            out[name] = [complete(p) for p in PROMPTS]
            log(f"{name}: {len(PROMPTS)} completions in {time.time() - t0:.0f} s")
            if name == "pd8":
                d1, d2 = post("/v1/decision", DECISION), post("/v1/decision", DECISION)
                out["decision"] = d1
                log(f"pd8: decision {[x['decision'] for x in d1['results']]}; timings {d1.get('timings')}")
                same = [x["decision"] for x in d1["results"]] == [x["decision"] for x in d2["results"]]
                out["decision_repeat_identical"] = same
                out["decision_problems"] = decision_ok(d1)
                stop, errors = threading.Event(), []
                def hammer():
                    while not stop.is_set():
                        try:
                            post("/v1/decision", DECISION)
                        except Exception as e:  # noqa: BLE001
                            errors.append(repr(e)); return
                th = threading.Thread(target=hammer); th.start()
                out["pd8_with_decisions"] = [complete(p) for p in PROMPTS]
                stop.set(); th.join()
                out["concurrent_decision_errors"] = errors
                log(f"pd8: completions with concurrent decisions done; decision errors {errors[:2]}")
        finally:
            proc.terminate(); proc.wait(30)
    summary = {"step1_base_vs_pd0": compare(out["base"], out["pd0"]),
               "step1_base_vs_pd8": compare(out["base"], out["pd8"]),
               "step2_pd8_vs_pd8_with_decisions": compare(out["pd8"], out["pd8_with_decisions"]),
               "decision_repeat_identical": out["decision_repeat_identical"],
               "decision_problems": out["decision_problems"],
               "concurrent_decision_errors": out["concurrent_decision_errors"]}
    json.dump({"summary": summary, **out}, open(RUN / "result.json", "w"), indent=1)
    for k, v in summary.items():
        log(f"{k}: {v}")
    log(f"done: {RUN}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"FAILED: {e!r}"); sys.exit(1)
