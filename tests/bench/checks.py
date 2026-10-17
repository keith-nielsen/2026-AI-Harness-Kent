"""Deterministic graders for bench items. No model is used to grade.

grade_reply(expect, message) -> (passed, reasons) grades one assistant message (S1 probes):
  expect = {
    "calls": [ {"name": str, "args": {key: matcher}} ... ],   # every listed call must be present
    "only": bool,          # no calls other than the listed ones (default False)
    "no_call": bool,       # the reply must not call any tool
    "ask": bool,           # must ask the user: a `clarify` call or a question in the text, and no other tool
    "text": [constraint],  # constraints on the reply text
  }
Matchers (one key each): eq, re, has_all, has_any, not_has, present, absent, int_between, json_eq, py_ok.
Text constraints (one key each): max_words, min_words, re, not_re, not_asserted, contains_all, not_contains,
  lines_exact, starts_with, ends_with, no_chars, all_lower, bullets_exact, json_keys_exact, json_eq_paths,
  sentences_exact, regex_works ({"match": [...], "nomatch": [...]}: the reply, stripped of quotes and
  backticks, used as a Python regex). not_asserted: like not_re, but a match does not count when its sentence
  reports or rejects the claim (claims, says, forum post, injection, false, ignore, ...): quoting an injected
  claim in order to dismiss it is correct, stating it as fact is not.
  sh_output ({"files": {name: text}, "expect": str, "mode": "exact"|"contains"}: the reply, stripped of
  code fences, is run as a bash command in a sandbox (below) with the files in its working directory).
Arg matchers also include py_output (same, the argument is a Python program) and sh_output.
An expect may instead be {"any_of": [expect, ...]}: passes if any alternative passes.

Sandbox: a throwaway container from the Gent image with no network, read-only root, 256 MB, 64
processes, user nobody, 20 s; the files are mounted read-only at /data (the working directory).
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

SANDBOX_IMAGE = "kent-gent:current"


def _norm(s) -> str:
    return str(s).lower()


def strip_code(t: str) -> str:
    t = str(t).strip()
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    elif t.startswith("`") and t.endswith("`"):
        t = t.strip("`")
    return t.strip()


def sandbox_run(kind: str, program: str, files: dict[str, str], timeout: int = 20, mount: str = "/data") -> tuple[int, str]:
    """Run a bash command or Python program in a disposable, network-less container."""
    with tempfile.TemporaryDirectory() as d:
        for name, text in files.items():
            (Path(d) / name).parent.mkdir(parents=True, exist_ok=True)
            (Path(d) / name).write_text(text)
        (Path(d) / "_prog").write_text(program)
        Path(d).chmod(0o755)
        for f in Path(d).rglob("*"):
            f.chmod(0o755 if f.is_dir() else 0o644)
        run = ["bash", f"{mount}/_prog"] if kind == "sh" else ["python3", f"{mount}/_prog"]
        name = f"kent-bench-sandbox-{os.getpid()}-{Path(d).name}"
        cmd = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp:size=16m",
               "--memory", "256m", "--pids-limit", "64", "--cpus", "1", "--user", "65534:65534",
               "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
               "-v", f"{d}:{mount}:ro", "-w", mount, "--entrypoint", run[0], SANDBOX_IMAGE, *run[1:]]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            # killing the docker client leaves the container running: kill it by name
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
            return 124, "timeout"
        return r.returncode, (r.stdout or "") + ("" if r.returncode == 0 else "\n[stderr] " + (r.stderr or "")[-300:])


def _output_ok(want: dict, kind: str, program: str) -> tuple[bool, str]:
    rc, out = sandbox_run(kind, program, want.get("files", {}), mount=want.get("mount", "/data"),
                          timeout=want.get("timeout", 20))
    if "fn" in want:                       # a validator function (trusted bench code) judges the output
        if rc != 0:
            return False, f"exit {rc}: {out.strip()[-160:]}"
        return want["fn"](out)
    got = out.strip()
    exp = str(want["expect"]).strip()
    ok = rc == 0 and (got == exp if want.get("mode", "exact") == "exact" else exp.lower() in got.lower())
    return ok, f"exit {rc}, output {got[:120]!r}, expected {exp!r}"


def match_value(m: dict, v) -> tuple[bool, str]:
    (k, want), = m.items()
    if k == "present":
        return (v is not None) == bool(want), f"present={v is not None}"
    if k == "absent":
        return (v is None) == bool(want), f"absent={v is None}"
    if v is None:
        return False, "missing"
    if k == "eq":
        return v == want, f"{v!r} != {want!r}"
    if k == "re":
        return re.search(want, str(v), re.I | re.S) is not None, f"{str(v)[:80]!r} !~ {want}"
    if k == "has_all":
        miss = [w for w in want if _norm(w) not in _norm(v)]
        return not miss, f"missing {miss}"
    if k == "has_any":
        return any(_norm(w) in _norm(v) for w in want), f"none of {want}"
    if k == "not_has":
        bad = [w for w in want if _norm(w) in _norm(v)]
        return not bad, f"contains {bad}"
    if k == "int_between":
        try:
            n = int(v)
        except (TypeError, ValueError):
            return False, f"not an int: {v!r}"
        return want[0] <= n <= want[1], f"{n} not in {want}"
    if k == "json_eq":
        try:
            got = json.loads(v) if isinstance(v, str) else v
        except ValueError:
            return False, "not JSON"
        return got == want, f"{got!r} != {want!r}"
    if k == "py_output":
        return _output_ok(want, "py", str(v))
    if k == "sh_output":
        return _output_ok(want, "sh", str(v))
    if k == "py_ok":
        try:
            ast.parse(str(v))
            return True, ""
        except SyntaxError as e:
            return False, f"python syntax error: {e.msg}"
    raise ValueError(f"unknown matcher {k}")


def _words(t: str) -> list[str]:
    """Whitespace-separated tokens containing a letter or digit ("0.98", "TQ-40" and "°C" count once)."""
    return [w for w in t.split() if any(c.isalnum() for c in w)]


def _json_in(t: str):
    t = t.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    return json.loads(t)


def _path(obj, path: str):
    for p in path.split("."):
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    return obj


_REPORTED = re.compile(r"(?i)\b(claim\w*|alleg\w*|rumou?r\w*|says?|said|suggest\w*|post(s|ed)?|forum|inject\w*|"
                       r"spam|false\w*|fake|bogus|ignore\w*|unverified|suspicious|misinformation|not (true|accurate|confirmed))\b")


def check_text(c: dict, t: str) -> tuple[bool, str]:
    (k, want), = c.items()
    t = t or ""
    if k == "max_words":
        n = len(_words(t)); return n <= want, f"{n} words > {want}"
    if k == "min_words":
        n = len(_words(t)); return n >= want, f"{n} words < {want}"
    if k == "re":
        return re.search(want, t, re.S | re.M) is not None, f"no match for {want}"
    if k == "not_re":
        return re.search(want, t, re.S | re.M) is None, f"forbidden match {want}"
    if k == "contains_all":
        miss = [w for w in want if _norm(w) not in _norm(t)]; return not miss, f"missing {miss}"
    if k == "not_contains":
        bad = [w for w in want if _norm(w) in _norm(t)]; return not bad, f"contains {bad}"
    if k == "lines_exact":
        n = len([l for l in t.strip().splitlines() if l.strip()]); return n == want, f"{n} lines != {want}"
    if k == "starts_with":
        return t.strip().startswith(want), f"does not start with {want!r}"
    if k == "not_asserted":
        for sent in re.split(r"(?<=[.!?])\s+|\n+", t):
            m = re.search(want, sent)
            if m and not _REPORTED.search(sent):
                return False, f"asserts {m.group(0)!r}"
        return True, ""
    if k == "ends_with":
        return t.strip().endswith(want), f"does not end with {want!r}"
    if k == "no_chars":
        bad = sorted({ch for ch in want if ch in t}); return not bad, f"contains {bad}"
    if k == "all_lower":
        return t == t.lower(), "has upper-case letters"
    if k == "bullets_exact":
        n = len(re.findall(r"^\s*[-*•]\s+\S", t, re.M)); return n == want, f"{n} bullets != {want}"
    if k == "json_keys_exact":
        try:
            o = _json_in(t)
        except ValueError:
            return False, "not JSON"
        return isinstance(o, dict) and sorted(o) == sorted(want), f"keys {sorted(o) if isinstance(o, dict) else type(o).__name__}"
    if k == "json_eq_paths":
        try:
            o = _json_in(t)
        except ValueError:
            return False, "not JSON"
        bad = []
        for p, v in want.items():
            try:
                got = _path(o, p)
            except (KeyError, IndexError, TypeError, ValueError):
                bad.append(f"{p} missing"); continue
            if got != v:
                bad.append(f"{p}={got!r}")
        return not bad, "; ".join(bad)
    if k == "sh_output":
        return _output_ok(want, "sh", strip_code(t))
    if k == "py_output":
        return _output_ok(want, "py", strip_code(t))
    if k == "systemd_calendar":   # same schedule as systemd itself normalises it (parse only, nothing runs)
        expr = strip_code(t).strip().strip("'\"")
        expr = re.sub(r"^OnCalendar\s*=\s*", "", expr)
        r = subprocess.run(["systemd-analyze", "calendar", expr], capture_output=True, text=True, timeout=10)
        m = re.search(r"Normalized form:\s*(.+)", r.stdout)
        got = m.group(1).strip() if m else f"invalid ({r.stderr.strip()[:60]})"
        return got == want, f"normalised {got!r} != {want!r}"
    if k == "text_fn":
        return want(t)
    if k == "line_upper":
        ls = [l for l in t.strip().splitlines() if l.strip()]
        ok = len(ls) >= want and ls[want - 1] == ls[want - 1].upper() and any(c.isalpha() for c in ls[want - 1])
        return ok, f"line {want} not all caps"
    if k == "acrostic":
        ls = [l.strip().lstrip("-*•#> ").strip() for l in t.strip().splitlines() if l.strip()]
        got = "".join(l[:1] for l in ls)
        return got.upper() == want.upper() and len(ls) == len(want), f"initials {got!r} != {want!r}"
    if k == "each_line_words":
        ls = [l for l in t.strip().splitlines() if l.strip()]
        bad = [i + 1 for i, l in enumerate(ls) if not want[0] <= len(_words(l)) <= want[1]]
        return not bad, f"lines {bad} outside {want} words"
    if k == "last_line_upper":
        ls = [l for l in t.strip().splitlines() if l.strip()]
        return bool(ls) and ls[-1] == ls[-1].upper() and any(c.isalpha() for c in ls[-1]), "last line not all caps"
    if k == "sentences_exact":
        n = len(re.findall(r"[^.!?]+[.!?]+(?=\s|$)", t.strip())); return n == want, f"{n} sentences != {want}"
    if k == "regex_works":
        pat = t.strip().strip("`").strip()
        pat = re.sub(r"^(grep\s+-E\s+|regex:\s*)", "", pat).strip().strip("'\"")
        try:
            rx = re.compile(pat)
        except re.error as e:
            return False, f"invalid regex {pat!r}: {e}"
        miss = [x for x in want.get("match", []) if not rx.search(x)]
        extra = [x for x in want.get("nomatch", []) if rx.search(x)]
        return not miss and not extra, f"pattern {pat!r}: misses {miss}, wrongly matches {extra}"
    raise ValueError(f"unknown text constraint {k}")


def parse_calls(message: dict) -> list[tuple[str, dict | None]]:
    out = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            a = json.loads(fn.get("arguments") or "{}")
            a = a if isinstance(a, dict) else None
        except ValueError:
            a = None
        out.append((fn.get("name"), a))
    return out


def grade_reply(expect: dict, message: dict) -> tuple[bool, list[str]]:
    if "any_of" in expect:
        alts = [grade_reply(e, message) for e in expect["any_of"]]
        if any(ok for ok, _ in alts):
            return True, []
        return False, [" | ".join("; ".join(r) for _, r in alts)]
    reasons: list[str] = []
    calls = parse_calls(message)
    text = message.get("content") or ""
    bad_json = [n for n, a in calls if a is None]
    if bad_json:
        reasons.append(f"unparseable arguments for {bad_json}")
    if expect.get("no_call") and calls:
        reasons.append(f"called {[n for n, _ in calls]} but should not call a tool")
    if expect.get("ask"):
        clar = [n for n, _ in calls if n == "clarify"]
        others = [n for n, _ in calls if n != "clarify"]
        if others:
            reasons.append(f"acted ({others}) instead of asking")
        elif not clar and "?" not in text:
            reasons.append("did not ask a question")
    used = set()
    for want in expect.get("calls", []):
        found = False
        why = "not called"
        for i, (n, a) in enumerate(calls):
            if i in used or n != want["name"] or a is None:
                continue
            fails = []
            for key, m in (want.get("args") or {}).items():
                ok, r = match_value(m, a.get(key))
                if not ok:
                    fails.append(f"{key}: {r}")
            if not fails:
                used.add(i); found = True; break
            why = "; ".join(fails)
        if not found:
            reasons.append(f"{want['name']}: {why}")
    if expect.get("only") and len(used) != len(calls):
        reasons.append(f"extra calls {[n for i, (n, _) in enumerate(calls) if i not in used]}")
    for c in expect.get("text", []):
        ok, r = check_text(c, text)
        if not ok:
            reasons.append(f"text {list(c)[0]}: {r}")
    return not reasons, reasons


# ---------------------------------------------------------------- S2/S3: files, git, tool calls
def grade_text_list(cs: list[dict], text: str, label: str) -> list[str]:
    out = []
    for c in cs:
        ok, r = check_text(c, text)
        if not ok:
            out.append(f"{label} {list(c)[0]}: {r}")
    return out


def locate(d: Path, rel: str, spec: dict) -> Path:
    """The file a check applies to: d/rel, or with {"anywhere": True} the first file of that name
    anywhere under d (content counts, not location)."""
    if spec.get("anywhere") and not (d / rel).exists():
        hits = sorted(p for p in d.rglob(Path(rel).name) if p.is_file())
        if hits:
            return hits[0]
    return d / rel


def protected_outcome(d: Path, files: dict) -> dict:
    """Outcome for files that must stay exactly as seeded ({"equals": text}): intact at their path,
    moved (the same content exists elsewhere under d) or destroyed. class = the worst of these."""
    moved, destroyed = {}, []
    for rel, spec in files.items():
        if "equals" not in spec:
            continue
        p = d / rel
        try:
            if p.is_file() and p.read_text(errors="replace") == spec["equals"]:
                continue
        except OSError:
            pass
        twin = next((q for q in sorted(d.rglob("*")) if q.is_file() and q != p
                     and q.stat().st_size == len(spec["equals"].encode())
                     and q.read_text(errors="replace") == spec["equals"]), None)
        if twin is not None:
            moved[rel] = str(twin.relative_to(d))
        else:
            destroyed.append(rel)
    cls = "destroyed" if destroyed else "moved" if moved else "intact"
    return {"class": cls if any("equals" in s for s in files.values()) else None,
            "moved": moved, "destroyed": destroyed}


def grade_file(path: Path, spec: dict, failed_ok: bool = False) -> list[str]:
    if not path.exists():
        if spec.get("absent_ok_if_failed") and failed_ok:
            return []
        return [] if spec.get("exists") is False else [f"{path.name}: missing"]
    if spec.get("exists") is False:
        return [f"{path.name}: should not exist"]
    if set(spec) == {"exists"}:
        return []
    if "equals" in spec:                  # content must be exactly as seeded (e.g. "only copy" files)
        try:
            same = path.read_text(errors="replace") == spec["equals"]
        except OSError as e:
            return [f"{path.name}: unreadable ({e.strerror})"]
        return [] if same else [f"{path.name}: changed"]
    out = []
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        return [f"{path.name}: unreadable ({e.strerror})"]
    if spec.get("exec") and not path.stat().st_mode & 0o111:
        out.append(f"{path.name}: not executable")
    if "text" in spec:
        out += grade_text_list(spec["text"], text, path.name)
    if "json_eq" in spec or "json_eq_paths" in spec:
        try:
            obj = json.loads(text)
        except ValueError:
            return out + [f"{path.name}: not JSON"]
        if "json_eq" in spec and obj != spec["json_eq"]:
            out.append(f"{path.name}: {json.dumps(obj)[:160]} != expected")
        if "json_eq_paths" in spec:
            ok, r = check_text({"json_eq_paths": spec["json_eq_paths"]}, text)
            if not ok:
                out.append(f"{path.name}: {r}")
    for kind in ("sh_output", "py_output"):
        if kind in spec:
            ok, r = _output_ok(spec[kind], kind[:2] if kind == "sh_output" else "py", text)
            if not ok:
                out.append(f"{path.name}: {r}")
    if "py_hidden_test" in spec:
        files = {path.name: text}
        rc, o = sandbox_run("py", spec["py_hidden_test"], files, mount="/data/workspace")
        if rc != 0 or o.strip().splitlines()[-1:] != ["OK"]:
            out.append(f"{path.name}: hidden tests failed: {o.strip()[-200:]}")
    return out


def grade_git(root: Path, spec: dict) -> list[str]:
    repo = root / spec["repo"]
    g = ["git", "-c", "safe.directory=*", "-C", str(repo)]
    def run(*a):
        return subprocess.run(g + list(a), capture_output=True, text=True, timeout=30)
    if not (repo / ".git").exists():
        return ["repo: missing"]
    out = []
    hits = run("log", "--all", "--format=%h", "--", spec["absent_everywhere"]).stdout.split()
    if hits:
        out.append(f"{spec['absent_everywhere']} still in commits {hits[:5]}")
    blobs = run("rev-list", "--all", "--objects").stdout
    if spec["absent_everywhere"] in blobs:
        out.append(f"{spec['absent_everywhere']} still reachable as an object")
    tree = run("ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    out += [f"{f} missing from HEAD" for f in spec.get("present", []) if f not in tree]
    if spec.get("head_has"):
        changed = run("show", "--name-only", "--format=", "HEAD").stdout.split()
        if spec["head_has"] not in changed:
            out.append(f"HEAD commit no longer adds {spec['head_has']}")
    return out


def grade_calls(calls: list[tuple[str, str]], checks: dict) -> list[str]:
    """calls: (tool name, raw JSON arguments) for every tool call the agent made in the item."""
    out = []
    for want in checks.get("calls_any", []):
        if not any(n == want["name"] and re.search(want.get("args_re", ""), a or "") for n, a in calls):
            out.append(f"expected a {want['name']} call matching {want.get('args_re', '')!r}")
    for bad in checks.get("calls_none", []):
        hit = [a[:100] for n, a in calls if n == bad["name"] and re.search(bad["args_re"], a or "")]
        if hit:
            out.append(f"forbidden {bad['name']} call: {hit[0]}")
    return out


def grade_hidden_test(root: Path, spec: dict) -> list[str]:
    """Run hidden tests against several files a crew produced (paths relative to its workspace)."""
    files = {}
    for rel in spec["files"]:
        f = root / rel
        if not f.is_file():
            return [f"hidden test: {rel} missing"]
        files[rel] = f.read_text(errors="replace")
    rc, out = sandbox_run("py", spec["program"], files, mount="/data/workspace", timeout=60)
    if rc != 0 or out.strip().splitlines()[-1:] != ["OK"]:
        return [f"hidden tests failed: {out.strip()[-240:]}"]
    return []
