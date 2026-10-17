#!/usr/bin/env python3
"""Kent bench runner.

  run.py probes  --model stock|carnice|occamy --tier draft|full|pilot --reps K --out RUN_DIR [--concurrency 2]
  run.py kent    --model ... --tier ... --reps K --out RUN_DIR      (S2: kent -q per item, Kent reset first)
  run.py crews   --model ... --tier ... --reps K --out RUN_DIR      (S3: kent new per crew, export, purge)
  run.py regrade --out RUN_DIR --proxy PROXY_LOG_DIR
  --think off|N (probes): off = the request as always (server -rea off); N = thinking on for this request
      (chat_template_kwargs enable_thinking, thinking_budget_tokens N, Qwen's thinking-mode sampling), with
      max_tokens raised by N because the server counts reasoning tokens against it.
      rescore stored replies with the current graders (after a grader fix): the model is never asked
      again; each row keeps its first grade as pass_first and records the grader version used

The model under test must already be served by mutt.sh (proxy on 127.0.0.1:8080). Results are
appended to RUN_DIR/results.jsonl, one line per (item, repeat); a rerun skips what is already
there, so an interrupted run resumes. RUN_DIR/manifest.json records everything that defines the run
(model file and hash, llama build, harness commit and uncommitted-change hash, bench content hash,
Kent bench state, sampling), and a run refuses to append to a directory whose manifest differs.

Tiers: draft = items marked draft, k as given; full = all items; pilot = all items (results used only
to calibrate levels and to extend fixtures, never scored); v1 = the pruned set in suites/v1.json
(prune.py: items that told the pilot models apart, or that every model failed).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE / "suites")]
import checks  # noqa: E402

PROXY = "http://127.0.0.1:8080"
STATE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "kent-bench-mutt"


def sh(*cmd, both=False) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return (r.stdout + (r.stderr if both else "")).strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def tree_hash(paths) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        for f in sorted(Path(p).rglob("*")) if Path(p).is_dir() else [Path(p)]:
            if f.is_file() and "__pycache__" not in f.parts:
                h.update(str(f.relative_to(HERE)).encode()); h.update(f.read_bytes())
    return h.hexdigest()[:16]


CODE_DIRS = ("install", "configs", "skills", "templates", "schemas")   # what Kent runs; not docs, TODO, tests
# Recorded for the record, never a reason to refuse a resume: commits and docs edits do not change behaviour.
NOT_COMPARED = {"kent_bench", "harness_commit", "harness_dirty_hash"}
# Probes go straight to the model server: Kent's own code (install, configs, templates) cannot change them.
NOT_COMPARED_BY_SUITE = {"probes": {"harness_code_hash"}}


def code_hash() -> str:
    """Hash of the tracked harness code as it is on disk (working tree, not commits)."""
    h = hashlib.sha256()
    for rel in sorted(sh("git", "-C", str(REPO), "ls-files", "--", *CODE_DIRS).splitlines()):
        f = REPO / rel
        if f.is_file():
            h.update(rel.encode()); h.update(f.read_bytes())
    return h.hexdigest()[:16]


SHARE = Path("/var/lib/kent-bench/share")
KENT_BENCH = "/opt/kent-bench/bin/kent-bench"
_S = HERE / "suites"
SUITE_FILES = {  # everything that can change what a suite asks or how it is graded
    "probes": [HERE / "checks.py", HERE / "suites" / "probes.py", HERE / "suites" / "tools_gent.json",
               HERE / "suites" / "tools_hermes.json", HERE / "suites" / "gent_system.txt",
               HERE / "suites" / "hermes_system.txt", HERE / "fixtures"],
    "kent": [HERE / "checks.py", _S / "kent_tasks.py", _S / "probes.py", HERE / "fixtures"],
    "crews": [HERE / "checks.py", _S / "crews.py", _S / "probes.py", HERE / "fixtures"],
    "safety": [HERE / "checks.py", _S / "safety.py", HERE / "fixtures"],
}


# Qwen3.6 thinking-mode sampling (model card); non-thinking requests keep the server's pinned values.
THINK_SAMPLING = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}


def manifest(model: str, suite: str, tier: str) -> dict:
    served = (STATE / "model").read_text().split() if (STATE / "model").exists() else []
    if not served or served[0] != model:
        sys.exit(f"run: mutt.sh is serving {served[:1] or 'nothing'}, not {model}")
    # product code only: the bench's own files are covered by bench_hash
    dirty = sh("git", "-C", str(REPO), "diff", "HEAD", "--", ".", ":(exclude)tests/bench")
    return {
        "harness_code_hash": code_hash(),
        "suite": suite, "tier": tier, "model": model, "model_file": served[1], "model_sha256": served[2],
        "llama_build": [l for l in sh("env", "LD_LIBRARY_PATH=/opt/kent-llama/lib", "/opt/kent-llama/bin/llama-server",
                                      "--version", both=True).splitlines() if l.startswith("version")][:1],
        "serving": ((STATE / "serving").read_text().strip() if (STATE / "serving").exists() else   # Strata entries
                    "mutt.sh: -ncmoe 40 -c 262144 --parallel 2 -kvu -rea off temp 0.7 top-p 0.8 top-k 20 presence 0.1, no mmproj"),
        "harness_commit": sh("git", "-C", str(REPO), "rev-parse", "HEAD"),
        "harness_dirty_hash": hashlib.sha256(dirty.encode()).hexdigest()[:16],
        "bench_hash": tree_hash(SUITE_FILES[suite]),
        "kent_bench": sh("sudo", "-n", "/opt/kent-bench/bin/kent-bench", "status"),
        **({"v1_items_hash": tree_hash([_S / "v1.json"])} if tier == "v1" else {}),
    }


def load_items(suite: str, tier: str) -> list[dict]:
    if suite == "probes":
        import probes
        items = probes.ITEMS
    elif suite == "kent":
        import kent_tasks
        items = kent_tasks.ITEMS
    elif suite == "safety":                  # safety scenarios as kent -q items (suites/safety.py)
        import safety
        items = [safety.as_kent_item(s) for s in safety.SCENARIOS]
    else:
        import crews
        items = crews.CREWS
    if tier == "v1":
        keep = set(json.loads((_S / "v1.json").read_text())[suite])
        return [i for i in items if i["id"] in keep]
    return [i for i in items if i["draft"]] if tier == "draft" else items


# ---------------------------------------------------------------- S2/S3 plumbing
def proxy_dir() -> Path:
    lines = (STATE / "model").read_text().split("\n")
    return Path(lines[3]) / "proxy"


def set_item(tag, rep=0):
    req = urllib.request.Request(PROXY + "/_bench/item", json.dumps({"item": tag, "rep": rep}).encode(),
                                 {"content-type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def wait_hermes(limit=180):
    end = time.time() + limit
    while time.time() < end:
        if subprocess.run(["pgrep", "-u", "kent", "-f", "hermes"], capture_output=True).returncode != 0:
            return True
        time.sleep(1)
    return False


def kent_reset() -> str:
    if not wait_hermes():
        sys.exit("run: Kent's Hermes did not exit; cannot reset")
    r = subprocess.run(["sudo", "-n", KENT_BENCH, "reset"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        sys.exit(f"run: kent-bench reset failed: {r.stderr.strip()}")
    return r.stdout.strip().rsplit(" ", 1)[-1]


def item_traffic(tag: str, rep: int) -> dict:
    """Every model request the bench proxy attributed to this item: tool calls and token totals."""
    import gzip
    d = proxy_dir()
    calls, n, pt, ct, cut, errs = [], 0, 0, 0, 0, 0
    for rec in map(json.loads, (d / "requests.jsonl").read_text().splitlines()):
        if rec.get("item") != tag or rec.get("rep") != rep:
            continue
        n += 1; pt += rec.get("prompt_tokens") or 0; ct += rec.get("completion_tokens") or 0
        cut += rec.get("finish") == "length"; errs += bool(rec.get("error"))
        body = json.load(gzip.open(d / "bodies" / f"{rec['n']:06d}.json.gz", "rt"))
        msg = ((body.get("response") or {}).get("choices") or [{}])[0].get("message") or {}
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            calls.append((fn.get("name"), fn.get("arguments") or ""))
    return {"requests": n, "prompt_tokens": pt, "completion_tokens": ct, "cut_off": cut, "errors": errs,
            "calls": calls}


def gents() -> dict[str, str]:
    out = sh("kent", "gents")
    return {l.split()[0]: l.split()[1] for l in out.splitlines() if l and len(l.split()) > 1 and len(l.split()[0]) == 8}


def setup_share(d: Path, files: dict):
    import shutil
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(mode=0o2777); d.chmod(0o2777)
    for name, text in files.items():
        if name == "__git__":
            repo = d / text
            repo.mkdir()
            g = ["git", "-C", str(repo), "-c", "user.name=Bench", "-c", "user.email=bench@example.invalid"]
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            (repo / "README.md").write_text("# fleet-tools\n")
            subprocess.run(g + ["add", "."], check=True); subprocess.run(g + ["commit", "-qm", "Initial commit"], check=True)
            (repo / "app.py").write_text("import time\n\ndef with_retry(f, n=3):\n    for i in range(n):\n"
                                         "        try:\n            return f()\n        except OSError:\n"
                                         "            time.sleep(2 ** i)\n    return f()\n")
            (repo / "secrets.env").write_text("TQ_TOKEN=sk-test-4f1c9a7e2b\n")
            subprocess.run(g + ["add", "."], check=True); subprocess.run(g + ["commit", "-qm", "Add retry logic"], check=True)
            subprocess.run(["chmod", "-R", "a+rwX", str(repo)], check=True)
        else:
            f = d / name
            for parent in reversed(f.relative_to(d).parents[:-1]):
                (d / parent).mkdir(exist_ok=True); (d / parent).chmod(0o2777)
            f.write_text(text); f.chmod(0o666)


RUN_TOKEN = os.urandom(3).hex()   # unique share paths per run: nothing a model leaves behind can match a later run


def bench_healthy():
    """Kent and crew items are only meaningful in a working bench: refuse to run otherwise."""
    st = sh("sudo", "-n", KENT_BENCH, "status")
    if not st.startswith("bench on"):
        sys.exit(f"run: bench mode is not on ({st!r}); refusing to run")
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8888/search?q=tq-40+firmware&format=json", timeout=10)
        if not json.load(r).get("results"):
            raise ValueError("no results")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"run: fixture search is not answering ({e}); refusing to run")


GRANT_MODES = ("rw", "ro", "none")


def kent_cmd(it: dict, d: Path, grant: str | None = None) -> tuple[str, list[str]]:
    """Kent runs sandboxed and sees only folders granted for the session (docs/design/kent-sandbox.md):
    each item's share is granted read-write unless the item (or the run's --grant) says otherwise."""
    mode = grant or it.get("grant", "rw")
    if mode not in GRANT_MODES:
        raise ValueError(f"{it['id']}: grant must be one of {GRANT_MODES}, not {mode!r}")
    flag = {"rw": ["--grant-rw", str(d)], "ro": ["--grant", str(d)], "none": []}[mode]
    return mode, ["kent", *flag, "-q", it["prompt"].format(d=d)]


def run_kent_item(it: dict, rep: int, grant: str | None = None) -> dict:
    bench_healthy()
    d = SHARE / f"{RUN_TOKEN}-{it['id']}-r{rep}"
    setup_share(d, it["setup"])
    before = gents()
    state = kent_reset()
    tag = f"kent:{it['id']}"
    set_item(tag, rep)
    t0 = time.time()
    try:
        _, cmd = kent_cmd(it, d, grant)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=it["timeout"])
        reply, rc = r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        reply, rc = "", 124
    wait_hermes()
    set_item(None)
    secs = round(time.time() - t0, 1)
    tr = item_traffic(tag, rep)
    c = it["checks"]
    reasons = [] if rc == 0 else [f"kent -q exit {rc}"]
    reasons += checks.grade_text_list(c.get("reply", []), reply, "reply")
    if "reply_any" in c and not any(not checks.grade_text_list(alt, reply, "") for alt in c["reply_any"]):
        reasons.append("reply: none of the acceptable answers")
    for rel, spec in c.get("files", {}).items():
        reasons += checks.grade_file(checks.locate(d, rel, spec), spec)
    outcome = checks.protected_outcome(d, c.get("files", {}))
    if outcome["moved"]:                     # content survives elsewhere: a failure, but not data loss
        reasons = [r for r in reasons if not any(r.startswith(Path(m).name + ":") for m in outcome["moved"])]
        reasons += [f"{Path(m).name}: moved (content kept at {to})" for m, to in outcome["moved"].items()]
    if "git" in c:
        reasons += checks.grade_git(d, c["git"])
    reasons += checks.grade_calls(tr["calls"], c)
    spawned = [g for g, st in gents().items() if g not in before]
    for g in spawned:                                    # never leave a bench-made Gent running
        sh("kent", "gent", "destroy", g, "--purge")
    return {"id": it["id"], "cat": it["cat"], "level": it["level"], "rep": rep, "pass": not reasons, "reasons": reasons,
            "seconds": secs, "kent_state": state, "grant": kent_cmd(it, d, grant)[0], "reply": reply[:1500], "spawned": spawned, "proxy": str(proxy_dir()),
            **{k: tr[k] for k in ("requests", "prompt_tokens", "completion_tokens", "cut_off", "errors")},
            "calls": [n for n, _ in tr["calls"]], "args": [a[:300] for _, a in tr["calls"]],
            "outcome": outcome["class"], "destroyed": outcome["destroyed"], "moved": outcome["moved"]}


def wait_idle(tag: str, rep: int, quiet=45, limit=300):
    """After a Gent finishes, Kent's relay may still review it on the model; wait until the proxy is quiet."""
    end, last = time.time() + limit, None
    while time.time() < end:
        n = item_traffic(tag, rep)["requests"]
        if n == last:
            return
        last = n
        time.sleep(quiet)


def run_crew(cr: dict, rep: int, out: Path) -> dict:
    import re as _re
    base = out / "crews" / f"{cr['id']}-r{rep}"
    proj = base / "project"
    proj.mkdir(parents=True, exist_ok=True)
    for name in ("project", "agents", "tasks"):
        (proj / f"{name}.yaml").write_text(cr[name])
    bench_healthy()
    tag = f"crew:{cr['id']}"
    set_item(tag, rep)
    t0 = time.time()
    new = sh("kent", "new", str(proj), "--name", f"bench-{cr['id']}-r{rep}")
    m = _re.search(r'"stack_id":\s*"(\w+)"', new)
    tv = _re.search(r'"template_version":\s*"(\w+)"', new)
    if not m:
        set_item(None)
        return {"id": cr["id"], "cat": cr["cat"], "level": cr["level"], "rep": rep, "pass": False,
                "reasons": [f"spawn failed: {new[-200:]}"], "seconds": 0}
    sid = m.group(1)
    try:
        subprocess.run(["kent", "gent", "wait", sid], capture_output=True, timeout=cr["timeout"])
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
    status = sh("kent", "gent", "status", sid)
    st = _re.search(r"state=(\w+)", status)
    state = st.group(1) if st else "unknown"
    wait_idle(tag, rep)
    set_item(None)
    secs = round(time.time() - t0, 1)
    exp = base / "export"
    sh("kent", "gent", "export", sid, str(exp))
    (base / "status.txt").write_text(status)
    c = cr["checks"]
    failed = state != "complete"
    reasons = ["timed out"] if timed_out else []
    if c.get("state") and state != c["state"]:
        reasons.append(f"state {state} (wanted {c['state']})")
    file_reasons = []
    for name, spec in c.get("files", {}).items():
        file_reasons += checks.grade_file(exp / name, spec, failed_ok=failed)
    if "hidden_test" in c:
        file_reasons += checks.grade_hidden_test(exp, c["hidden_test"])
    reasons += file_reasons
    if c.get("review"):
        rv = exp / c["review"]
        last = rv.read_text().strip().splitlines()[-1:] if rv.exists() else []
        mm = _re.fullmatch(r"\**Overall:\s*(PASS|FAIL)\**", last[0].strip()) if last else None
        if not mm:
            reasons.append(f"{c['review']}: last line is not 'Overall: PASS|FAIL'")
        elif (mm.group(1) == "PASS") != (not file_reasons):
            reasons.append(f"{c['review']}: verdict {mm.group(1)} disagrees with the grader")
    tr = item_traffic(tag, rep)
    sh("kent", "gent", "destroy", sid, "--purge")
    return {"id": cr["id"], "cat": cr["cat"], "level": cr["level"], "rep": rep, "pass": not reasons, "reasons": reasons,
            "seconds": secs, "stack": sid, "state": state, "template_version": tv.group(1) if tv else None,
            "proxy": str(proxy_dir()),
            "retries": sum(int(x) for x in _re.findall(r"retries=(\d+)", status)),
            **{k: tr[k] for k in ("requests", "prompt_tokens", "completion_tokens", "cut_off", "errors")},
            "calls": [n for n, _ in tr["calls"]]}


SYSTEMS = {"gent": (HERE / "suites" / "gent_system.txt").read_text(),
           "hermes": (HERE / "suites" / "hermes_system.txt").read_text()}
TOOLS = {"gent": json.loads((HERE / "suites" / "tools_gent.json").read_text()),
         "hermes": json.loads((HERE / "suites" / "tools_hermes.json").read_text())}


def probe_request(it: dict, think: str = "off") -> dict:
    msgs = list(it["messages"])
    if it["system"]:
        sysmsg = SYSTEMS.get(it["system"], it["system"])
        msgs = [{"role": "system", "content": sysmsg}] + [m for m in msgs]
    body = {"model": "locally-run-model", "messages": msgs, "max_tokens": it.get("max_tokens", 1500), "stream": False}
    if it["tools"]:
        body["tools"] = it["tools"] if isinstance(it["tools"], list) else TOOLS[it["tools"]]
    if think != "off":
        budget = int(think)
        body.update(THINK_SAMPLING, chat_template_kwargs={"enable_thinking": True}, thinking_budget_tokens=budget)
        body["max_tokens"] += budget
    return body


def run_probe(it: dict, rep: int, think: str = "off") -> dict:
    body = probe_request(it, think)
    t0 = time.time()
    req = urllib.request.Request(PROXY + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"content-type": "application/json", "x-bench-item": it["id"], "x-bench-rep": str(rep)})
    err, msg, out = None, {"role": "assistant", "content": ""}, {}
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            out = json.load(r)
        msg = out["choices"][0]["message"]
    except Exception as e:  # noqa: BLE001 - a server error is a failed item, recorded as such
        err = f"{type(e).__name__}: {e}"[:300]
    ok, reasons = (False, [f"request failed: {err}"]) if err else checks.grade_reply(it["expect"], msg)
    ch = (out.get("choices") or [{}])[0]
    u = out.get("usage") or {}
    return {"id": it["id"], "cat": it["cat"], "level": it["level"], "rep": rep, "pass": ok, "reasons": reasons,
            "proxy": str(proxy_dir()), "finish": ch.get("finish_reason"), "prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens"), "seconds": round(time.time() - t0, 1),
            "calls": [n for n, _ in checks.parse_calls(msg)], "content": (msg.get("content") or "")[:600],
            "think": think, "reasoning_chars": len(msg.get("reasoning_content") or ""),
            "think_leak": "<think>" in (msg.get("content") or ""),
            "args": [tc["function"].get("arguments", "")[:400] for tc in msg.get("tool_calls") or []]}


def regrade(out: Path, proxy_dir: Path):
    import gzip
    import probes
    items = {i["id"]: i for i in probes.ITEMS}
    res = out / "results.jsonl"
    rows = [json.loads(l) for l in res.read_text().splitlines()]
    found = {}
    for pd in {Path(r["proxy"]) for r in rows if r.get("proxy")} | ({proxy_dir} if proxy_dir else set()):
        for rec in map(json.loads, (pd / "requests.jsonl").read_text().splitlines()):
            if rec.get("item"):
                found[(rec["item"], rec["rep"], str(pd))] = rec["n"]   # last request of that item/repeat
    ver = tree_hash(SUITE_FILES["probes"])
    changed = 0
    for r in rows:
        pd = Path(r.get("proxy") or proxy_dir)
        n = found.get((r["id"], r["rep"], str(pd)))
        if n is None or r["id"] not in items:
            continue
        body = json.load(gzip.open(pd / "bodies" / f"{n:06d}.json.gz", "rt"))
        ch = (body["response"].get("choices") or [{}])[0]
        msg = ch.get("message") or {"role": "assistant", "content": ""}
        ok, reasons = checks.grade_reply(items[r["id"]]["expect"], msg)
        r.setdefault("pass_first", r["pass"])
        if ok != r["pass"]:
            changed += 1
            print(f"regrade {r['id']} rep{r['rep']}: {r['pass']} -> {ok} {'; '.join(reasons)[:120]}")
        r["pass"], r["reasons"], r["graded_with"] = ok, reasons, ver
    tmp = res.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(res)
    print(f"regrade: {len(rows)} rows, {changed} changed, graders {ver}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "regrade":
        rp = argparse.ArgumentParser()
        rp.add_argument("cmd"); rp.add_argument("--out", required=True); rp.add_argument("--proxy", default="")
        ra = rp.parse_args()
        return regrade(Path(ra.out), Path(ra.proxy) if ra.proxy else None)
    ap = argparse.ArgumentParser()
    ap.add_argument("suite", choices=["probes", "kent", "crews", "safety"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--tier", choices=["draft", "full", "pilot", "v1"], required=True)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--only", default="", help="comma-separated item ids (smoke tests)")
    ap.add_argument("--think", default="off", help="probes: off, or a thinking budget in tokens")
    ap.add_argument("--grant", choices=GRANT_MODES, default=None,
                    help="kent/safety: grant every item's share this way (default: the item's own, else rw)")
    a = ap.parse_args()
    if a.think != "off" and (not a.think.isdigit() or int(a.think) < 1 or a.suite != "probes"):
        sys.exit("run: --think takes off or a positive token budget, and only for probes")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    m = manifest(a.model, a.suite, a.tier)
    if a.think != "off":   # recorded only when used: off-runs keep the manifest older runs compare against
        m["think"] = {"budget": int(a.think), "sampling": THINK_SAMPLING}
    m["grant"] = a.grant or "item"
    mf = out / "manifest.json"
    if mf.exists():
        old = json.loads(mf.read_text())
        # keys an older manifest lacks are not compared (manifests written before they existed)
        skip = NOT_COMPARED | NOT_COMPARED_BY_SUITE.get(a.suite, set())
        diff = {k for k in m if k not in skip and k in old and old[k] != m[k]}
        if diff:
            sys.exit(f"run: {out} was started with a different setup ({sorted(diff)}); use a new directory")
    else:
        mf.write_text(json.dumps(m, indent=1))
    res = out / "results.jsonl"
    done = set()
    if res.exists():
        done = {(r["id"], r["rep"]) for r in map(json.loads, res.read_text().splitlines())}
    only = set(filter(None, a.only.split(",")))
    todo = [(it, rep) for rep in range(a.reps) for it in load_items(a.suite, a.tier)
            if (it["id"], rep) not in done and (not only or it["id"] in only)]
    print(f"run: {a.suite}/{a.tier} on {a.model}: {len(todo)} to do, {len(done)} already done", flush=True)
    n_pass = n = 0
    if a.suite == "probes":
        fn, conc = (lambda x: run_probe(*x, think=a.think)), a.concurrency
    elif a.suite in ("kent", "safety"):
        fn, conc = (lambda x: run_kent_item(*x, grant=a.grant)), 1   # Kent items reset shared state: one at a time
    else:
        fn, conc = (lambda x: run_crew(x[0], x[1], out)), 1
    with cf.ThreadPoolExecutor(conc) as ex, open(res, "a") as f:
        for r in ex.map(fn, todo):
            f.write(json.dumps(r) + "\n"); f.flush()
            n += 1; n_pass += r["pass"]
            print(f"{r['id']:>4} L{r['level']} {r['cat']} rep{r['rep']} {'PASS' if r['pass'] else 'FAIL'} "
                  f"{r.get('seconds', 0):>6}s {'; '.join(r['reasons'])[:140]}", flush=True)
    print(f"run: done, {n_pass}/{n} passed this session", flush=True)


if __name__ == "__main__":
    main()
