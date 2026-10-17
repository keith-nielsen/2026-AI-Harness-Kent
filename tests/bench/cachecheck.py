#!/usr/bin/env python3
"""cachecheck.py — does a triage request leave the conversation in the cache for the real request? (2026-10-17)

Design under test: the gateway sends the conversation + one extra user message (the triage question, 1 token,
logprobs, thinking off), reads the answer, then sends the real request (thinking off or on) to the same slot.
On Qwen3.6 (hybrid: recurrent layers restore only from context checkpoints) the real request can reuse the
prefill only if a checkpoint lands where the two prompts diverge; llama-server checkpoints at user-message
starts (tools/server/server-context.cpp:3875-3905).
Per probe and per real-request mode (off, on), slot 0 pinned (id_slot), slot cleared between cases:
  cold:   real request alone                       -> prompt_n ~ whole prompt (control)
  triage: triage request, then the real request    -> pass if real cache_n ~ conversation, prompt_n small
Server: production binary and arguments (kent-llama-launch --print) on port 8081, -lv 4 (trace: checkpoint
create/restore lines). kent-llama is stopped for the run if it was running, and started again at the end.
  python3 cachecheck.py [--port 8081] [--external]   (--external: use a server already on --port)
Start detached: tests/bench/submit.sh cachecheck cachecheck.py   Results: bench/runs/cachecheck-<time>/
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
RUN = BENCH / "runs" / time.strftime("cachecheck-%m%d%H%M")
PROBES = ["a06", "b15", "c08", "h05", "t50", "i43"]   # hermes + gent system prompts, short and long
TRIAGE = ("[Routing check, not part of the conversation. Do not answer the request above.] How much hidden "
          "step-by-step thinking should the assistant spend on the last request? 0 = none (direct answer or one "
          "obvious tool call), 1 = short, 2 = long, 3 = maximum. Reply with one digit only.")
A = None


def log(msg):
    RUN.mkdir(parents=True, exist_ok=True)
    with open(RUN / "cachecheck.log", "a") as f:
        f.write(f"{time.strftime('%F %T')} cachecheck: {msg}\n")


def post(path, body, timeout=900):
    req = urllib.request.Request(f"http://127.0.0.1:{A.port}{path}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def erase():
    post("/slots/0?action=erase", {})


def chat(body, think):
    b = {**body, "id_slot": 0, "cache_prompt": True, "temperature": 0,
         "chat_template_kwargs": {"enable_thinking": think}}
    t0 = time.time()
    r = post("/v1/chat/completions", b)
    t = r.get("timings", {})
    return {"seconds": round(time.time() - t0, 3), "cache_n": t.get("cache_n"), "prompt_n": t.get("prompt_n"),
            "prompt_ms": t.get("prompt_ms"), "prompt_tokens": r.get("usage", {}).get("prompt_tokens"),
            "reply": (r["choices"][0]["message"].get("content") or "")[:40]}


def cases(it):
    body = benchrun.probe_request(it, "off")
    body.pop("chat_template_kwargs", None)
    base = {k: v for k, v in body.items() if k in ("model", "messages", "tools")}
    real = {**base, "max_tokens": 8}
    triage = {**base, "messages": base["messages"] + [{"role": "user", "content": TRIAGE}], "max_tokens": 1,
              "logprobs": True, "top_logprobs": 10}
    out = {}
    for mode, think in (("off", False), ("on", True)):
        erase(); cold = chat(real, think)
        erase(); tri = chat(triage, False); after = chat(real, think)
        out[mode] = {"cold": cold, "triage": tri, "real_after_triage": after}
        log(f"{it['id']} {mode}: cold prompt_n {cold['prompt_n']} ({cold['seconds']} s) | triage prompt_n "
            f"{tri['prompt_n']} ({tri['seconds']} s, reply {tri['reply']!r}) | real after triage cache_n "
            f"{after['cache_n']} prompt_n {after['prompt_n']} ({after['seconds']} s)")
    return out


def prod_argv():
    out = subprocess.run(["/opt/kent-llama/libexec/kent-llama-launch", "--print"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin"}, check=True).stdout
    argv = shlex.split(out)
    argv[argv.index("--port") + 1] = str(A.port)
    argv[argv.index("-lv") + 1] = "4"           # trace: context checkpoint create/restore lines
    (RUN / "slots").mkdir(parents=True, exist_ok=True)
    return argv + ["--slot-save-path", str(RUN / "slots")]   # enables /slots/0?action=erase; no effect on inference


def main():
    items = {it["id"]: it for it in benchrun.load_items("probes", "full")}
    proc = None
    if not A.external:
        argv = prod_argv()
        log("server: " + " ".join(argv))
        proc = subprocess.Popen(argv, stdout=open(RUN / "server.log", "w"), stderr=subprocess.STDOUT,
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
    res, rc = {}, 0
    try:
        for pid in PROBES:
            res[pid] = cases(items[pid])
            json.dump(res, open(RUN / "result.json", "w"), indent=1)
    except Exception as e:  # noqa: BLE001
        log(f"FAILED: {e!r}"); rc = 1
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(60)
            except subprocess.TimeoutExpired:
                proc.kill()
    if (RUN / "server.log").exists():
        lines = (RUN / "server.log").read_text(errors="replace").splitlines()
        log(f"server log: {sum('created context checkpoint' in l for l in lines)} checkpoints created, "
            f"{sum('restored context checkpoint' in l for l in lines)} restored")
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--external", action="store_true")
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
        log(f"done rc {rc}: {RUN}")
    sys.exit(rc)
