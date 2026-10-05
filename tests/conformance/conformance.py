#!/usr/bin/env python3
"""Kent conformance check: the installed system against the design (docs/architecture.md,
as amended by the operator's decisions). Read-only; runs as root because Kent's credentials
and data belong to the kent account (the operator path is exercised as the invoking operator):

    sudo ./kent-admin conformance [--markdown FILE]

Each check prints PASS / FAIL / INFO with the design section it verifies.
Exit status: number of FAILs (0 = conformant).
"""
from __future__ import annotations

import argparse
import base64
import grp
import json
import os
import pwd
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OPERATOR = os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
HOME = Path(pwd.getpwnam(OPERATOR).pw_dir)
CFG = HOME / ".config/kent"                       # the operator's own credentials
KETC = Path("/etc/kent/kent")                     # Kent's config and credentials (root:kent)
KCRED = KETC / "credentials"
KDATA = Path("/var/lib/kent")                     # Kent's data (kent-owned)
KENT_TIMERS = ("kent-audit-ingest", "kent-poll-learnings", "kent-digest", "kent-qa-audit", "kent-audit-anchor")
results: list[tuple[str, str, str, str]] = []   # (status, area, check, detail)


def rec(status: str, area: str, check: str, detail: str = "") -> None:
    results.append((status, area, check, detail))
    print(f"{status:4} [{area}] {check}" + (f" — {detail}" if detail else ""))


def ok(cond: bool, area: str, check: str, detail: str = "") -> bool:
    rec("PASS" if cond else "FAIL", area, check, detail)
    return cond


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def http(url: str, *, key: str | None = None, basic: tuple[str, str] | None = None, data: dict | None = None,
         timeout: int = 30) -> tuple[int, str]:
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    if basic:
        h["Authorization"] = "Basic " + base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
    req = urllib.request.Request(url, headers=h, data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


SERVICES = {  # unit -> (account, expected listeners)
    "kent-litellm.service": ("litellm", ["127.0.0.1:4000"]),
    "kent-prometheus.service": ("prometheus", ["127.0.0.1:9090"]),
    "kent-node-exporter.service": ("node_exporter", ["127.0.0.1:9100"]),
    "kent-loki.service": ("loki", ["127.0.0.1:3100", "127.0.0.1:9095"]),
    "alloy.service": ("alloy", ["127.0.0.1:12345"]),
    "grafana-server.service": ("grafana", ["127.0.0.1:3001"]),
    "kent-gitea.service": ("gitea", ["127.0.0.1:3000"]),
    "kent-squid.service": ("kent-squid", ["127.0.0.1:3129"]),
}
MODULES = ["litellm", "prometheus", "node_exporter", "loki", "alloy", "grafana", "gitea", "searxng", "hermes",
           "kent-core", "gent"]


def check_services() -> None:
    listen = sh("ss", "-ltnH")
    addrs = {l.split()[3] for l in listen.splitlines() if l.strip()}
    accounts = set()
    for unit, (acct, ports) in SERVICES.items():
        active = sh("systemctl", "is-active", unit) == "active"
        user = sh("systemctl", "show", "-p", "User", "--value", unit) or "(root)"
        ok(active, "services", f"{unit} active")
        ok(user == acct, "§14 accounts", f"{unit} runs as its own account", f"User={user}")
        accounts.add(user)
        for p in ports:
            ok(p in addrs, "§16 network", f"{unit} listens on {p}")
    ok(len(accounts) == len(SERVICES), "§14 accounts", "no shared service account", f"{len(accounts)} distinct")
    ok(sh("systemctl", "is-active", "kent-searxng.service") == "active", "services", "kent-searxng.service active")
    # Every Kent container runs as a registered, non-root host account (no image-internal ids).
    for name in sh("docker", "ps", "--filter", "label=kent.module", "--format", "{{.Names}}").split():
        ids = {l.split()[1] for l in sh("docker", "top", name, "-o", "pid,uid").splitlines()[1:] if l.split()}
        known = all(i.isdigit() and sh("getent", "passwd", i) for i in ids)
        ok(bool(ids) and "0" not in ids and known, "§14 accounts", f"{name} runs as a registered non-root account",
           ",".join(sorted(ids)))
    ok("127.0.0.1:8888" in addrs, "§16 network", "kent-searxng listens on 127.0.0.1:8888")
    kent_ports = {"4000", "9090", "9100", "3100", "9095", "12345", "3001", "3000", "3129", "8888"}
    exposed = [a for a in addrs if a.rsplit(":", 1)[-1] in kent_ports and not a.startswith(("127.0.0.1:", "[::1]:", "172.30.0.1:"))]
    ok(not exposed, "§16 network", "no Kent service reachable beyond loopback / Gent bridge", ", ".join(exposed))
    for sock in ("kent-gent-egress.socket", "kent-gent-gateway.socket", "kent-gent-search.socket"):
        ok(sh("systemctl", "is-active", sock) == "active", "§15 egress", f"{sock} active")


def check_hardening() -> None:
    for unit in ["kent-litellm.service", "kent-prometheus.service", "kent-node-exporter.service", "kent-loki.service",
                 "alloy.service", "grafana-server.service", "kent-gitea.service", "kent-squid.service"]:
        out = sh("systemd-analyze", "security", "--no-pager", unit)
        m = re.search(r"exposure level for \S+: ([\d.]+)", out)
        score = float(m.group(1)) if m else 10.0
        ok(score <= 3.0, "§19 hardening", f"{unit} exposure ≤ 3.0", f"{score}")
        nnp = sh("systemctl", "show", "-p", "NoNewPrivileges", "--value", unit)
        ok(nnp == "yes", "§19 hardening", f"{unit} NoNewPrivileges")
    for t in KENT_TIMERS:  # Kent's jobs: informational (the poller must call sudo, so it cannot set NNP)
        out = sh("systemd-analyze", "security", "--no-pager", f"{t}.service")
        m = re.search(r"exposure level for \S+: ([\d.]+)", out)
        rec("INFO", "§19 hardening", f"{t}.service exposure", m.group(1) if m else "?")


def pins(path: Path = Path(__file__).resolve().parents[2] / "install/services/versions.env") -> dict[str, str]:
    """KEY=value lines of versions.env (the pinned, tested versions)."""
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if re.match(r"^[A-Z0-9_]+=", line))


def check_manifests() -> None:
    for m in MODULES:
        ok(Path(f"/var/lib/kent-install/manifest/{m}").is_file(), "install record", f"manifest for {m}")
    # Vendor packages Kent installs run at the pinned, tested version; an adopted (pre-existing)
    # package is the operator's and only reported.
    v = pins()
    for pkg, key in (("grafana", "GRAFANA_VERSION"), ("alloy", "ALLOY_VERSION")):
        have = sh("dpkg-query", "-W", "-f=${Version}", pkg)
        mf = Path(f"/var/lib/kent-install/manifest/{pkg}")
        if mf.is_file() and f"package {pkg}" in mf.read_text().splitlines():
            ok(have == v[key], "install record", f"{pkg} at the pinned version", f"installed {have or 'none'}, "
               f"pinned {v[key]}")
        else:
            rec("INFO", "install record", f"{pkg} package pre-existing (adopted, not pinned)", have or "not installed")
    added = [m for m in ("alloy", "grafana") if Path(f"/var/lib/kent-install/manifest/{m}").is_file()
             and "file /etc/apt/sources.list.d/grafana.list" in Path(f"/var/lib/kent-install/manifest/{m}").read_text()]
    ok(not added, "install record", "no apt source added by Kent (packages come from pinned files)",
       f"grafana.list recorded by {', '.join(added)}; re-run the module to retire it" if added else "")


def check_gateway() -> None:
    kent_key = (KCRED / "litellm_kent_key").read_text().strip()
    op_key = (CFG / "litellm_operator_key").read_text().strip()
    code, _ = http("http://127.0.0.1:4000/v1/models")
    ok(code == 401, "§3 gateway", "no key → 401", str(code))
    code, _ = http("http://127.0.0.1:4000/v1/models", key="sk-kent-forged-" + "0" * 40)
    ok(code == 401, "§3 gateway", "forged key → 401", str(code))
    code, body = http("http://127.0.0.1:4000/v1/models", key=kent_key)
    models = {m["id"] for m in json.loads(body).get("data", [])} if code == 200 else set()
    ok({"auto", "router", "fast", "smart", "frontier"} <= models, "§3 gateway", "kent sees all tiers incl. auto", ",".join(sorted(models)))
    code, body = http("http://127.0.0.1:4000/v1/chat/completions", key=op_key, timeout=300,
                      data={"model": "fast", "max_tokens": 3, "messages": [{"role": "user", "content": "Say OK"}]})
    ok(code == 200, "§2 tiers", "operator → fast (local) answers", str(code))
    cfg = sh("systemctl", "show", "-p", "EnvironmentFiles", "--value", "kent-litellm.service")
    ok("/etc/kent/litellm/litellm.env" in cfg, "§5 fallback", "FALLBACK_TIMEOUT env file wired")
    creds = sh("systemctl", "cat", "kent-litellm.service")  # `show` renders LoadCredential unprintable
    ok(all(k in creds for k in ("operator_key", "kent_key", "gent_key", "metrics_key", "anthropic_api_key")),
       "§13 secrets", "gateway keys via LoadCredential (not env)")
    has_key = Path("/etc/kent/litellm/credentials/anthropic_api_key").stat().st_size > 0
    rec("PASS" if has_key else "INFO", "§5 frontier", "Anthropic key",
        "configured" if has_key else "not configured (smart/frontier fall back to local fast)")


def check_observability() -> None:
    code, body = http("http://127.0.0.1:9090/api/v1/targets")
    targets = {t["labels"]["job"]: t["health"] for t in json.loads(body)["data"]["activeTargets"]} if code == 200 else {}
    for job in ("prometheus", "node", "litellm", "loki", "alloy", "grafana", "gitea"):
        ok(targets.get(job) == "up", "§17 observability", f"Prometheus target {job} up", targets.get(job, "missing"))
    if shutil.which("nvidia-smi"):
        code, body = http("http://127.0.0.1:9090/api/v1/query?" + urllib.parse.urlencode({"query": "kent_gpu_up"}))
        res = json.loads(body)["data"]["result"] if code == 200 else []
        ok(bool(res) and res[0]["value"][1] == "1", "§17 observability", "GPU metrics reach Prometheus (kent-gpu-metrics, 15 s)",
           res[0]["value"][1] if res else "no kent_gpu_up series")
    q = 'sum(count_over_time({unit="kent-litellm.service", kent_event="llm_call"}[24h]))'
    code, body = http("http://127.0.0.1:3100/loki/api/v1/query?" + urllib.parse.urlencode({"query": q}))
    n = int(float(json.loads(body)["data"]["result"][0]["value"][1])) if code == 200 and json.loads(body)["data"]["result"] else 0
    ok(n > 0, "§17 observability", "gateway calls reach Loki with kent_event labels (24h)", f"{n} calls")
    q = 'sum(count_over_time({syslog_identifier=~"kent-gent-.+"}[24h]))'
    code, body = http("http://127.0.0.1:3100/loki/api/v1/query?" + urllib.parse.urlencode({"query": q}))
    n = int(float(json.loads(body)["data"]["result"][0]["value"][1])) if code == 200 and json.loads(body)["data"]["result"] else 0
    rec("PASS" if n > 0 else "INFO", "§17 observability", "Gent container logs reach Loki (journald driver)", f"{n} lines")
    pw = (CFG / "grafana_admin_password").read_text().strip()
    code, body = http("http://127.0.0.1:3001/api/datasources", basic=("admin", pw))
    uids = {d["uid"] for d in json.loads(body)} if code == 200 else set()
    ok({"kent-prometheus", "kent-loki"} <= uids, "§17 observability", "Grafana datasources provisioned")
    code, _ = http("http://127.0.0.1:3001/api/search")
    ok(code in (401, 403), "§12 security", "Grafana refuses anonymous", str(code))


def check_gitea() -> None:
    code, _ = http("http://127.0.0.1:3000/api/healthz")
    ok(code == 200, "§10 templates", "Gitea healthy", str(code))
    tok = (KCRED / "gitea_kent_token").read_text().strip()
    req = urllib.request.Request("http://127.0.0.1:3000/api/v1/repos/kent/stack-template", headers={"Authorization": f"token {tok}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        repo = json.load(r)
    ok(repo.get("private") is True, "§10 templates", "kent/stack-template is private")
    code, _ = http("http://127.0.0.1:3000/api/v1/repos/kent/stack-template")
    ok(code in (401, 403, 404), "§12 security", "template repo not readable anonymously", str(code))


def as_kent(*cmd: str) -> str:
    return sh("runuser", "-u", "kent", "--", "env", f"KENT_CONF={KETC}/kent.conf", f"HOME={KDATA}", *cmd)


def check_kent_account() -> None:
    try:
        pw = pwd.getpwnam("kent")
    except KeyError:
        ok(False, "§14 accounts", "kent account exists")
        return
    ok(pw.pw_shell.endswith("nologin"), "§14 accounts", "kent is a no-login system account", pw.pw_shell)
    groups = set(sh("id", "-nG", "kent").split())
    ok(not groups & {"docker", "sudo", "adm", "kent-operators"}, "§14 accounts",
       "kent is not in docker/sudo/adm", " ".join(sorted(groups)))
    ok(KDATA.stat().st_uid == pw.pw_uid and KDATA.stat().st_mode & 0o007 == 0, "§9 data",
       "/var/lib/kent owned by kent, no world access", oct(KDATA.stat().st_mode & 0o777))
    sudoers = Path("/etc/sudoers.d/92-kent-operators")
    txt = sudoers.read_text() if sudoers.exists() else ""
    rules = [l for l in txt.splitlines() if l.strip() and not l.startswith("#")]
    ok(rules == ["%kent-operators ALL=(kent) NOPASSWD: /opt/kent-core/libexec/kent-exec"], "§14 accounts",
       "operators may run only kent-exec as kent", rules[0] if rules else "missing")
    members = sh("getent", "group", "kent-operators").split(":")[-1]
    ok(OPERATOR in members.split(","), "§14 accounts", f"{OPERATOR} is a kent operator", members)
    st = subprocess.run(["runuser", "-u", OPERATOR, "--", "/usr/local/bin/kent", "status"], capture_output=True, text=True)
    ok(st.returncode == 0, "operator", "`kent status` works for the operator", (st.stdout or st.stderr).splitlines()[0][:80]
       if (st.stdout or st.stderr) else "")
    managed = Path("/etc/kent/hermes/managed/config.yaml")
    txt = managed.read_text() if managed.exists() else ""
    ok("127.0.0.1:4000" in txt and "auto" in txt and "single_query_mode: deny" in txt, "§3 gateway",
       "Kent's Hermes (managed scope) → gateway, model auto, one-shot approvals denied")
    ok("keyless_fallback: false" in txt, "§3 web",
       "web calls never fall back to anonymous outside vendors (keyless_fallback off)")
    profile = Path("/etc/kent/profile").read_text().strip() if Path("/etc/kent/profile").exists() else "?"
    rec("INFO", "profile", "Kent profile", profile)
    ver = as_kent("/opt/kent-hermes/bin/kent-hermes", "--version").splitlines()
    rec("INFO", "Hermes", "Kent's pinned Hermes", ver[0] if ver else "?")
    tirith = Path("/opt/kent-hermes/bin/tirith")
    menv = Path("/etc/kent/hermes/managed/.env").read_text()
    ok(tirith.is_file() and tirith.stat().st_uid == 0 and f"TIRITH_BIN={tirith}" in menv and "TIRITH_ENABLED=true" in menv,
       "§12 security", "tirith pre-exec scanner pinned, root-owned, enforced by the managed scope")
    want = "false" if profile == "hardened" else "true"
    ok(f"TIRITH_FAIL_OPEN={want}" in menv, "§12 security", f"tirith fail-open={want} for profile {profile}")


def check_kent_core() -> None:
    out = as_kent("/usr/bin/python3", "/opt/kent-core/bin/kent_audit.py", "verify")
    verdict = next((l for l in out.splitlines() if l.startswith(("audit chain valid", "AUDIT CHAIN INVALID"))), out[:80])
    ok(verdict.startswith("audit chain valid"), "§18 audit", "HMAC audit chain valid", verdict)
    note = next((l for l in out.splitlines() if l.startswith("note:")), "")
    if note:
        rec("INFO", "§18 audit", "earlier chains in the journal (reinstalls)", note[6:120])
    anchors = sh("journalctl", "-t", "kent-audit-anchor", "--no-pager", "-o", "cat", "-n", "1")
    ok("count=" in anchors, "§18 audit", "chain anchored in the journal", anchors[:60])
    for t in KENT_TIMERS:
        # A listed timer can still be dormant (no next elapse); require a scheduled next run.
        # While the job runs the timer has no next elapse, and a oneshot job reports
        # "activating", not "active"; re-read briefly so a run ending between the reads counts.
        for _ in range(3):
            nxt = sh("systemctl", "show", f"{t}.timer", "-p", "NextElapseUSecRealtime", "--value")
            running = sh("systemctl", "is-active", f"{t}.service") in ("active", "activating", "deactivating")
            armed = bool(re.search(r"\d", nxt)) or running
            if armed:
                break
            time.sleep(1)
        ok(armed, "§7.2 Kent duties", f"{t}.timer armed (next run scheduled)", "running now" if running else (nxt or "none"))
        user = sh("systemctl", "show", "-p", "User", "--value", f"{t}.service")
        ok(user == "kent", "§14 accounts", f"{t}.service runs as kent", user or "(root)")
    conf = (KETC / "kent.conf").read_text()
    ok("STACKS_DIR=/var/lib/kent-gent/stacks" in conf, "§9 data", "kent.conf points at Gent stacks")
    for f in sorted(KCRED.iterdir()):
        st = f.stat()
        ok(st.st_mode & 0o777 == 0o640 and st.st_uid == 0 and sh("stat", "-c", "%G", str(f)) == "kent",
           "§13 secrets", f"{f} is root:kent 0640", oct(st.st_mode & 0o777))
    for f in ("litellm_operator_key", "gitea_admin_password", "grafana_admin_password"):
        mode = (CFG / f).stat().st_mode & 0o777
        ok(mode == 0o600, "§13 secrets", f"~{OPERATOR}/.config/kent/{f} is 0600", oct(mode))
    stale = [f for f in ("litellm_kent_key", "gitea_kent_token", "audit_hmac_secret") if (CFG / f).exists()]
    ok(not stale, "§13 secrets", "no Kent credentials in the operator's home", ",".join(stale))
    db = sqlite3.connect(f"file:{KDATA}/kent.db?mode=ro", uri=True)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ok({"stack_registry", "learning_reviews", "gent_assessments", "qa_audit_log"} <= tables, "§9 data", "kent.db schema")
    digest = sorted((KDATA / "digests").glob("*.md"))
    ok(bool(digest), "§7.2 Kent duties", "daily digest produced", digest[-1].name if digest else "")
    touched = [m.name for m in Path("/var/lib/kent-install/manifest").iterdir() if "ai-env" in m.read_text()]
    ok(not touched, "operator", "no Kent module records anything under ~/ai-env", ",".join(touched))
    # Upstream engines rate-limit and suspend themselves for minutes at a time; retry before
    # failing, and name the unresponsive engines so an upstream block is told apart from a fault.
    n, down = 0, []
    for q in ("linux", "linux kernel", "debian"):
        code, body = http("http://127.0.0.1:8888/search?" + urllib.parse.urlencode({"q": q, "format": "json"}))
        data = json.loads(body) if code == 200 else {}
        n, down = len(data.get("results", [])), [e[0] for e in data.get("unresponsive_engines", [])]
        if n:
            break
        time.sleep(3)
    ok(n > 0, "web search", "SearXNG JSON search answers",
       f"{n} results" + (f"; unresponsive: {','.join(down)}" if down else ""))


def check_gents() -> None:
    img = sh("docker", "image", "inspect", "-f", "{{index .Config.Labels \"kent.module\"}}", "kent-gent:current")
    ok(img == "gent", "§20 docker", "Gent image built by the module")
    net = json.loads(sh("docker", "network", "inspect", "kent-gent-net") or "[{}]")[0]
    ok(net.get("Internal") is True, "§12 security", "Gent network is internal (no route out)")
    sudoers = Path("/etc/sudoers.d/91-kent-gent")
    ok(sudoers.exists() and "kent ALL=(root)" in sudoers.read_text(), "§14 accounts",
       "Gent brokers (spawn/destroy/ctl) granted to the kent account only")
    db = sqlite3.connect(f"file:{KDATA}/kent.db?mode=ro", uri=True)
    reg = db.execute("SELECT COUNT(*), SUM(status='archived') FROM stack_registry").fetchone()
    if reg[0] == 0:
        rec("INFO", "§11 lifecycle", "no Gents spawned yet on this install")
    else:
        rec("PASS", "§11 lifecycle", "stack registry keeps every Gent", f"{reg[0]} rows, {reg[1]} archived")
    leftovers = []
    for (user,) in db.execute("SELECT unix_user FROM stack_registry WHERE status IN ('archived','destroyed')"):
        try:
            pwd.getpwnam(user)
            leftovers.append(user)
        except KeyError:
            pass
    ok(not leftovers, "§14 accounts", "retired Gent accounts removed", ",".join(leftovers))
    # Each live Gent must be able to read Kent's answers: inbox setgid to its group, files in it too.
    unreadable = []
    for sid, user in db.execute("SELECT stack_id, unix_user FROM stack_registry WHERE status NOT IN ('archived','destroyed')"):
        inbox = Path("/var/lib/kent-gent/stacks") / sid / "inbox"
        try:
            gid = grp.getgrnam(user).gr_gid
        except KeyError:
            continue
        if not inbox.is_dir():
            continue
        ist = inbox.stat()
        if ist.st_gid != gid or not ist.st_mode & 0o2000:
            unreadable.append(f"{sid}:inbox")
        unreadable += [f"{sid}:{f.name}" for f in inbox.iterdir()
                       if f.is_file() and (f.stat().st_gid != gid or not f.stat().st_mode & 0o040)]
    ok(not unreadable, "§8.1 escalation", "Gents can read Kent's answers (inbox setgid, files group-readable)",
       ",".join(unreadable)[:160])
    adopted = db.execute("SELECT COUNT(*) FROM learning_reviews WHERE verdict='adopt' AND notes LIKE '%template commit%'").fetchone()[0]
    rec("PASS" if adopted else "INFO", "§10 knowledge", "learnings adopted into the template", str(adopted))
    assessed = db.execute("SELECT COUNT(*) FROM gent_assessments").fetchone()[0]
    rec("PASS" if assessed else "INFO", "§7.2 Kent duties", "Kent assessments recorded", str(assessed))
    st = subprocess.run([str(Path(__file__).resolve().parents[1] / "gent" / "run_tools_selftest.sh")],
                        capture_output=True, text=True)
    last = (st.stdout.strip().splitlines() or ["?"])[-1]
    ok(st.returncode == 0, "§12/§15 isolation", "Gent tools self-test in a locked-down container", last)


LLAMA_UNITS = ("kent-llama.service", "srv-kent-models.mount")


def models_mode(manifest: Path = Path("/var/lib/kent-install/manifest/llama")) -> str:
    """The llama module's recorded models mode: lab (files left as the operator's) or hardened
    (root:kent-models). A recorded directory without a mode line is a pre-profile install: hardened."""
    lines = manifest.read_text().splitlines() if manifest.exists() else []
    modes = [l.split()[1] for l in lines if l.startswith("modelsmode ") and len(l.split()) > 1]
    if modes:
        return modes[-1]
    return "hardened" if any(l.startswith("modelsdir ") for l in lines) else "lab"


def check_llama() -> None:
    """The local model server: own account, models (per profile) behind a read-only mount, on demand."""
    try:
        pw = pwd.getpwnam("kent-llama")
    except KeyError:
        ok(False, "§14 accounts", "kent-llama account exists")
        return
    locked = sh("passwd", "-S", "kent-llama").split()[1:2] == ["L"]
    ok(pw.pw_shell.endswith("nologin") and locked, "§14 accounts", "kent-llama: no login, locked password", pw.pw_shell)
    groups = set(sh("id", "-nG", "kent-llama").split())
    ok(not groups & {"docker", "sudo", "adm", "kent-operators", "kent-models"}, "§14 accounts",
       "kent-llama has no privileged groups (kent-models only per unit)", " ".join(sorted(groups)))
    sudo_rules = [f.name for f in Path("/etc/sudoers.d").iterdir() if "kent-llama" in f.read_text()]
    ok(not sudo_rules, "§14 accounts", "no sudo rule mentions kent-llama", ",".join(sudo_rules))
    members = [m for m in sh("getent", "group", "kent-models").split(":")[-1].split(",") if m]
    bad = [m for m in members if m == "kent" or m.startswith("gent-") or m in SERVICES_ACCOUNTS]
    ok(not bad, "§12 security", "kent-models: no Kent, Gent or service accounts", ",".join(members))
    unit = sh("systemctl", "cat", "kent-llama.service")
    ok("User=kent-llama" in unit and "SupplementaryGroups=kent-models" in unit, "§14 accounts",
       "kent-llama.service runs as kent-llama, reads models via kent-models")
    ok(sh("systemctl", "is-enabled", "kent-llama.service") in ("static", "disabled"), "§2 tiers",
       "kent-llama.service on demand (not started at boot)")
    ok(sh("systemctl", "show", "-p", "NoNewPrivileges", "--value", LLAMA_UNITS[0]) == "yes", "§19 hardening",
       f"{LLAMA_UNITS[0]} NoNewPrivileges")
    # Host performance settings (SMT, boost, governor, swap, GPU clocks) are the operator's, set by hand with
    # tests/bench/hostprofile.sh: no Kent unit may change them on start or stop (retired 2026-10-05).
    tuning = Path("/etc/systemd/system/kent-llama-tuning.service").exists()
    deps = [l for l in unit.splitlines() if l.split("=", 1)[0] in ("Requires", "Wants", "After", "BindsTo")]
    ok(not tuning and not any("tuning" in l for l in deps), "§2 tiers",
       "kent-llama start/stop changes no host performance setting (no tuning unit)")
    out = sh("systemd-analyze", "security", "--no-pager", "kent-llama.service")
    m = re.search(r"exposure level for \S+: ([\d.]+)", out)
    score = float(m.group(1)) if m else 10.0
    ok(score <= 3.0, "§19 hardening", "kent-llama.service exposure ≤ 3.0", f"{score}")
    # Models: lab = the operator's files, read-only; hardened = root:kent-models 0750/0440; every GGUF hashed.
    mount = sh("systemctl", "cat", "srv-kent-models.mount")
    src = next((l.split("=", 1)[1] for l in mount.splitlines() if l.startswith("What=")), "")
    opts = next((l.split("=", 1)[1] for l in mount.splitlines() if l.startswith("Options=")), "")
    ok(set("bind,ro,nodev,nosuid,noexec".split(",")) <= set(opts.split(",")), "§12 security",
       "model mount declared ro,nodev,nosuid,noexec", opts)
    live = sh("findmnt", "-rn", "-o", "OPTIONS", "/srv/kent/models")
    if live:
        ok({"ro", "nodev", "nosuid", "noexec"} <= set(live.split(",")), "§12 security",
           "/srv/kent/models mounted ro,nodev,nosuid,noexec", live)
    else:
        rec("INFO", "§12 security", "/srv/kent/models not mounted (server stopped)")
    d = Path(src)
    if not src or not d.is_dir():
        ok(False, "§12 security", "model directory present", src or "no What= in srv-kent-models.mount")
        return
    mode = models_mode()
    rec("INFO", "§12 security", "models mode (profile)", mode)
    st = d.stat()
    files = [f for f in d.iterdir() if f.is_file() and not f.is_symlink()]
    if mode == "hardened":
        gid = grp.getgrnam("kent-models").gr_gid
        ok(st.st_uid == 0 and st.st_gid == gid and st.st_mode & 0o777 == 0o750, "§12 security",
           f"{d} is root:kent-models 0750", oct(st.st_mode & 0o777))
        wrong = [f.name for f in files if (f.stat().st_uid, f.stat().st_gid, f.stat().st_mode & 0o777) != (0, gid, 0o440)]
        ok(not wrong, "§12 security", f"all {len(files)} model files root:kent-models 0440", ",".join(wrong)[:120])
    else:
        # lab: the operator keeps their files; no write bits at all, and readable by kent-llama (other).
        ok(not st.st_mode & 0o002 and st.st_mode & 0o001, "§12 security",
           f"{d} not world-writable, reachable by kent-llama", oct(st.st_mode & 0o777))
        writable = [f.name for f in files if f.stat().st_mode & 0o222]
        ok(not writable, "§12 security", f"all {len(files)} model files read-only (no write bits)", ",".join(writable)[:120])
        unreadable = [f.name for f in files if f.suffix == ".gguf" and not f.stat().st_mode & 0o004]
        ok(not unreadable, "§12 security", "every GGUF readable by kent-llama", ",".join(unreadable)[:120])
    sums = Path("/etc/kent/llama/models.sha256")
    listed = {l.split(None, 1)[1].strip() for l in sums.read_text().splitlines() if l.strip()} if sums.exists() else set()
    conf = Path("/etc/kent/llama")
    cst = conf.stat() if conf.exists() else None
    ok(cst is not None and cst.st_uid == 0 and cst.st_mode & 0o777 == 0o751, "§12 security",
       "/etc/kent/llama 0751 (operators read the records by name, cannot list)", oct(cst.st_mode & 0o777) if cst else "missing")
    envst = (conf / "llama.env").stat() if (conf / "llama.env").exists() else None
    ok(envst is not None and envst.st_mode & 0o777 == 0o640, "§12 security", "llama.env 0640 (not world-readable)",
       oct(envst.st_mode & 0o777) if envst else "missing")
    srcf = conf / "models.source"
    src_rec = dict(l.split(" ", 1) for l in srcf.read_text().splitlines() if " " in l) if srcf.exists() else {}
    ok(srcf.exists() and srcf.stat().st_uid == 0 and src_rec.get("path") == str(d), "§12 security",
       "models.source records the models directory (filesystem UUID + path) for start diagnostics", src_rec.get("path", "missing"))
    if src_rec.get("automount") == "1":
        # lab: works once the operator logs in; hardened needs a system mount (the installer refuses it).
        if mode == "hardened":
            ok(False, "§12 security", "models on a system mount (hardened)", src_rec.get("mountpoint", ""))
        else:
            rec("INFO", "§12 security", "models on a desktop automount: the model server starts only after login "
                "(an /etc/fstab entry is more robust)", src_rec.get("mountpoint", ""))
    r = subprocess.run(["/opt/kent-llama/libexec/kent-llama-source", "diagnose"], capture_output=True, text=True)
    ok(r.returncode == 0, "§12 security", "models directory is on the recorded filesystem", r.stdout.strip()[:160])
    # Only the files the settings select are recorded (and checked at each start): Kent loads one set.
    envf = Path("/etc/kent/llama/llama.env")
    llama_env = dict(l.split("=", 1) for l in envf.read_text().splitlines()
                     if "=" in l and not l.lstrip().startswith("#")) if envf.exists() else {}
    r = subprocess.run(["/opt/kent-llama/libexec/kent-llama-launch", "--files"], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", **llama_env})
    selected = {Path(l).name for l in r.stdout.splitlines() if l.strip()} if r.returncode == 0 else set()
    ok(sums.exists() and sums.stat().st_uid == 0 and bool(selected) and selected <= listed, "§12 security",
       "the selected model files have a root-owned SHA-256 record (checked at each start)",
       ",".join(sorted(selected - listed))[:120] or r.stderr.strip()[:120])
    # The binary is the root-owned copy recorded at install.
    info = Path("/opt/kent-llama/BUILD-INFO")
    changed = []
    for line in (info.read_text().splitlines() if info.exists() else []):
        if line.startswith("sha256 "):
            _, want, rel = line.split(None, 2)
            f = Path("/opt/kent-llama") / rel
            if not f.exists() or f.stat().st_uid != 0 or sh("sha256sum", str(f)).split()[:1] != [want]:
                changed.append(rel)
    ok(info.exists() and not changed, "§12 security", "llama-server and its libraries match BUILD-INFO (root-owned)",
       ",".join(changed))
    rule = Path("/etc/polkit-1/rules.d/60-kent-llama.rules")
    txt = rule.read_text() if rule.exists() else ""
    ok(rule.exists() and rule.stat().st_uid == 0 and '"kent-llama.service"' in txt and 'isInGroup("kent-operators")' in txt
       and txt.count("polkit.Result.YES") == 1, "§14 accounts",
       "polkit: only kent-operators may start/stop kent-llama.service")
    if sh("systemctl", "is-active", "kent-llama.service") == "active":
        pid = sh("systemctl", "show", "-p", "MainPID", "--value", "kent-llama.service")
        ok(sh("ps", "-o", "user=", "-p", pid) == "kent-llama", "§14 accounts", "llama-server runs as kent-llama")
        listen = {l.split()[3] for l in sh("ss", "-ltnH", "( sport = :8080 )").splitlines() if l.strip()}
        ok(listen == {"127.0.0.1:8080"}, "§16 network", "llama-server listens on 127.0.0.1:8080 only", ",".join(listen))
    else:
        rec("INFO", "§2 tiers", "local model server stopped (kent llama start)")


SERVICES_ACCOUNTS = {acct for acct, _ in SERVICES.values()}


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("run as root: sudo ./kent-admin conformance")
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown")
    a = ap.parse_args()
    for fn in (check_services, check_hardening, check_manifests, check_gateway, check_observability,
               check_gitea, check_kent_account, check_kent_core, check_gents, check_llama):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - a crashing check is a failing check
            rec("FAIL", fn.__name__, "check crashed", repr(e)[:200])
    for area, item in [("§5 frontier", "Claude Opus 5.5 live calls (needs Anthropic key)"),
                       ("§16 firewall", "firewall hardening (deferred by operator)"),
                       ("§18.4 AIDE / CVE scans", "deferred by operator")]:
        rec("INFO", area, item, "deferred")
    fails = sum(1 for r in results if r[0] == "FAIL")
    passes = sum(1 for r in results if r[0] == "PASS")
    print(f"\n{passes} PASS, {fails} FAIL, {sum(1 for r in results if r[0] == 'INFO')} INFO")
    if a.markdown:
        rows = "\n".join(f"| {s} | {ar} | {c} | {d.replace('|', '/')} |" for s, ar, c, d in results)
        Path(a.markdown).write_text(f"| Result | Area | Check | Detail |\n|---|---|---|---|\n{rows}\n\n"
                                    f"**{passes} PASS, {fails} FAIL**\n")
    return fails


if __name__ == "__main__":
    raise SystemExit(main())
