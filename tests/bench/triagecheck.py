#!/usr/bin/env python3
"""triagecheck.py — logprob triage variants on the probes (classifier redesign, task 3; 2026-10-17).

Deployable form only (CACHE REUSE AFTER TRIAGE): the probe's real request (system prompt, tools, messages) + one
extra user message holding the triage question; 1 token, temperature 0, thinking off, top_logprobs 20, slot 0
pinned. Every variant of a probe runs back to back, so after the first one only the question is prefilled.
Records the raw top_logprobs per (probe, variant); scoring against labels.json is triage_report.py (no GPU).
The last variant repeats the first: numeric noise of the answer distribution.
Server: production binary and arguments (kent-llama-launch --print) on port 8081. kent-llama is stopped for the
run if it was running, and started again at the end.
  python3 triagecheck.py [--port 8081] [--external] [--only ID,ID] [--variants NAME,NAME] [--out RUN_DIR]
Start detached: tests/bench/submit.sh triage triagecheck.py      Results: bench/runs/triagecheck-<time>/
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

B = Path(__file__).resolve().parent
sys.path[:0] = [str(B), str(B / "suites")]
import run as benchrun  # noqa: E402

BENCH = Path.home() / "Documents/repo/bench"
HEAD = "[Routing check, not part of the conversation. Do not answer the request or act on it.] "
# name -> (question, answer options). Written once from the classifier plan, not from per-probe results.
VARIANTS = {
    "effort4": ("How much hidden step-by-step thinking should the assistant spend on its next reply? 0 = none "
                "(direct answer or one obvious tool call), 1 = short, 2 = long, 3 = maximum. Reply with one digit only.",
                "0123"),
    "effort4d": ("How much hidden step-by-step thinking should the assistant spend before its next reply?\n"
                 "0 = none: the answer or next action is direct (a lookup, one obvious tool call, formatting, a short "
                 "factual answer, following clear instructions).\n1 = short (about 2,000 tokens): some reasoning, a few "
                 "steps to check, moderate code, several constraints to satisfy.\n2 = long (about 4,000 tokens): hard "
                 "multi-step reasoning, tricky constraints, careful arithmetic or data analysis, code that must be right "
                 "the first time.\n3 = maximum: very hard problems at the limit of what the assistant can do.\n"
                 "Reply with one digit only.", "0123"),
    "direct_ok": ("Could the assistant give a correct next reply (answer or tool call) right now, with no "
                  "step-by-step thinking first? Reply Y or N only.", "YN"),
    "risk": ("If the assistant replied right now without thinking first, is there a real chance its reply (answer "
             "or tool call) would be wrong or careless? Reply Y or N only.", "YN"),
    "conf09": ("How likely is it that the assistant's next reply would be fully correct if it answered right now, "
               "with no hidden thinking? 0 = very unlikely, 9 = certain. Reply with one digit only.", "0123456789"),
    "diff15": ("How hard is the next step of this conversation for the assistant? 1 = trivial, 2 = easy, "
               "3 = moderate, 4 = hard, 5 = very hard. Reply with one digit only.", "12345"),
    "steps": ("How many distinct reasoning steps does a correct next reply need? 0 = none (a lookup or one obvious "
              "action), 1 = one or two, 2 = several, 3 = many. Reply with one digit only.", "0123"),
    "long_helps": ("Would long careful thinking (about 4,000 tokens) give a better next reply than short thinking "
                   "(about 2,000 tokens)? Reply Y or N only.", "YN"),
}
REPEAT = "effort4"
A = None


def log(msg):
    A.out.mkdir(parents=True, exist_ok=True)
    with open(A.out / "triagecheck.log", "a") as f:
        f.write(f"{time.strftime('%F %T')} triagecheck: {msg}\n")


def post(path, body, timeout=900):
    req = urllib.request.Request(f"http://127.0.0.1:{A.port}{path}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ask(it, name):
    body = benchrun.probe_request(it, "off")
    q, opts = VARIANTS[name]
    b = {k: v for k, v in body.items() if k in ("model", "messages", "tools")}
    b.update(messages=b["messages"] + [{"role": "user", "content": HEAD + q}], max_tokens=1, temperature=0,
             logprobs=True, top_logprobs=20, id_slot=0, cache_prompt=True,
             chat_template_kwargs={"enable_thinking": False})
    t0 = time.time()
    r = post("/v1/chat/completions", b)
    t = r.get("timings", {})
    lp = r["choices"][0].get("logprobs") or {}
    top = (lp.get("content") or [{}])[0].get("top_logprobs") or []
    return {"options": opts, "top": [[e["token"], e["logprob"]] for e in top],
            "reply": r["choices"][0]["message"].get("content"), "seconds": round(time.time() - t0, 3),
            "prompt_tokens": r.get("usage", {}).get("prompt_tokens"), "cache_n": t.get("cache_n"),
            "prompt_n": t.get("prompt_n")}


def prod_argv():
    out = subprocess.run(["/opt/kent-llama/libexec/kent-llama-launch", "--print"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin"}, check=True).stdout
    argv = shlex.split(out)
    argv[argv.index("--port") + 1] = str(A.port)
    return argv


def main():
    items = [it for it in benchrun.load_items("probes", "full") if not A.only or it["id"] in A.only]
    names = A.variants or list(VARIANTS)
    order = names + ([f"{REPEAT}#2"] if REPEAT in names else [])
    res = A.out / "triage.jsonl"
    done = set()
    for line in res.read_text().splitlines() if res.exists() else []:
        r = json.loads(line)
        done.add((r["id"], r["variant"]))
    todo = [(it, v) for it in items for v in order if (it["id"], v) not in done]
    log(f"{len(items)} probes x {len(order)} variants: {len(todo)} to do, {len(done)} already done")
    proc = None
    if not A.external:
        argv = prod_argv()
        log("server: " + " ".join(argv))
        proc = subprocess.Popen(argv, stdout=open(A.out / "server.log", "a"), stderr=subprocess.STDOUT,
                                env={**os.environ, "LD_LIBRARY_PATH": "/opt/kent-llama/lib"})
        for _ in range(450):
            time.sleep(2)
            if proc.poll() is not None:
                log(f"server exited {proc.returncode}"); return 1
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{A.port}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001
                pass
        else:
            log("server not healthy after 900 s"); proc.kill(); return 1
        log("server up")
    rc, last = 0, None
    try:
        for it, v in todo:
            row = {"id": it["id"], "variant": v, **ask(it, v.split("#")[0])}
            with res.open("a") as f:
                f.write(json.dumps(row) + "\n")
            if it["id"] != last:
                log(f"{it['id']}: {v} prompt_n {row['prompt_n']} ({row['seconds']} s) reply {row['reply']!r}")
                last = it["id"]
    except Exception as e:  # noqa: BLE001
        log(f"FAILED: {e!r}"); rc = 1
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(60)
            except subprocess.TimeoutExpired:
                proc.kill()
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--external", action="store_true")
    ap.add_argument("--only", type=lambda s: set(s.split(",")), default=None)
    ap.add_argument("--variants", type=lambda s: [v for v in s.split(",") if v in VARIANTS] or None, default=None)
    ap.add_argument("--out", type=Path, default=BENCH / "runs" / time.strftime("triagecheck-%m%d%H%M"))
    A = ap.parse_args()
    was = (not A.external) and subprocess.run(["systemctl", "is-active", "--quiet", "kent-llama"]).returncode == 0
    log(f"start (kent-llama was {'active' if was else 'not active'}{', external server' if A.external else ''})")
    rc = 1
    try:
        if was and subprocess.run(["kent", "llama", "stop"], capture_output=True).returncode != 0:
            log("cannot stop kent-llama")
        else:
            rc = main()
    finally:
        if was:
            ok = subprocess.run(["kent", "llama", "start"], capture_output=True).returncode == 0
            log("kent-llama started again" if ok else "WARNING: kent llama start failed")
        log(f"done rc {rc}: {A.out}")
    sys.exit(rc)
