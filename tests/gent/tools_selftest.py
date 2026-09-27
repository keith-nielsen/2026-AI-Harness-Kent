"""Gent tools self-test. Runs INSIDE the Gent image on the Gent network, as an
unprivileged user, the way a Gent runs (see run_tools_selftest.sh):
workspace confinement, symlink escape, script confinement, proxy egress policy."""
import os
import sys
from pathlib import Path

os.environ.setdefault("GENT_WORKSPACE", "/data/workspace")
sys.path.insert(0, "/app")
from gent import tools  # noqa: E402

fails = []


def call(t, **kw):
    return getattr(t, "func", t)(**kw)


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail[:160]}]" if not cond else ""))
    if not cond:
        fails.append(name)


ws = Path(os.environ["GENT_WORKSPACE"])
ws.mkdir(parents=True, exist_ok=True)

r = call(tools.write_file, path="ok/hello.txt", content="hi")
check("write inside workspace", r.startswith("wrote") and (ws / "ok/hello.txt").read_text() == "hi", r)
for bad in ["../escape.txt", "/etc/passwd", "../../tmp/x", "ok/../../x", "/data/stack.db", "/data/project/tasks.yaml"]:
    r = call(tools.write_file, path=bad, content="x")
    check(f"write refused outside workspace: {bad}", r.startswith("error") or (ws / bad.lstrip('/')).resolve().is_relative_to(ws), r)
check("absolute /data/workspace path accepted", call(tools.write_file, path="/data/workspace/abs.txt", content="a").startswith("wrote"))
for bad in ["/etc/passwd", "../stack.db", "/run/kent/gent_key", "/proc/self/environ"]:
    r = call(tools.read_file, path=bad)
    check(f"read refused outside workspace: {bad}", r.startswith("error") or "sk-" not in r and "root:" not in r, r)
os.symlink("/run/kent/gent_key", ws / "link_to_key")
r = call(tools.read_file, path="link_to_key")
check("symlink escape refused", r.startswith("error"), r)
r = call(tools.write_file, path="s.sh", content="#!/bin/sh\necho ran\n")
check("shebang file executable", (ws / "s.sh").stat().st_mode & 0o100 != 0, r)
r = call(tools.run_script, path="s.sh")
check("run workspace script", "exit=0" in r and "ran" in r, r)
r = call(tools.run_script, path="/usr/bin/id")
check("run refused outside workspace", r.startswith("error"), r)
r = call(tools.run_script, path="ok/hello.txt")
check("run refused for non-script", r.startswith("error"), r)
r = call(tools.read_web_page, url="https://example.com/")
check("web read via proxy", "Example Domain" in r, r)
for url in ["http://127.0.0.1:3000/", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/",
            "http://localhost:9090/", "file:///etc/passwd"]:
    r = call(tools.read_web_page, url=url)
    check(f"internal target refused: {url}", any(k in r.lower() for k in ("error", "403", "denied", "only http")), r)
r = call(tools.web_search, query="kernel.org releases.json")
check("web search via proxy returns results", "http" in r and "error" not in r[:20].lower(), r)
print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
