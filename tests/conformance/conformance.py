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
import json
import os
import pwd
import re
import sqlite3
import subprocess
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


def check_manifests() -> None:
    for m in MODULES:
        ok(Path(f"/var/lib/kent-install/manifest/{m}").is_file(), "install record", f"manifest for {m}")


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
        nxt = sh("systemctl", "show", f"{t}.timer", "-p", "NextElapseUSecRealtime", "--value")
        running = sh("systemctl", "is-active", f"{t}.service") == "active"
        armed = bool(re.search(r"\d", nxt)) or running
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
    code, body = http("http://127.0.0.1:8888/search?q=linux&format=json")
    n = len(json.loads(body).get("results", [])) if code == 200 else 0
    ok(n > 0, "web search", "SearXNG JSON search answers", f"{n} results")


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
    adopted = db.execute("SELECT COUNT(*) FROM learning_reviews WHERE verdict='adopt' AND notes LIKE '%template commit%'").fetchone()[0]
    rec("PASS" if adopted else "INFO", "§10 knowledge", "learnings adopted into the template", str(adopted))
    assessed = db.execute("SELECT COUNT(*) FROM gent_assessments").fetchone()[0]
    rec("PASS" if assessed else "INFO", "§7.2 Kent duties", "Kent assessments recorded", str(assessed))
    st = subprocess.run([str(Path(__file__).resolve().parents[1] / "gent" / "run_tools_selftest.sh")],
                        capture_output=True, text=True)
    last = (st.stdout.strip().splitlines() or ["?"])[-1]
    ok(st.returncode == 0, "§12/§15 isolation", "Gent tools self-test in a locked-down container", last)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("run as root: sudo ./kent-admin conformance")
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown")
    a = ap.parse_args()
    for fn in (check_services, check_hardening, check_manifests, check_gateway, check_observability,
               check_gitea, check_kent_account, check_kent_core, check_gents):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - a crashing check is a failing check
            rec("FAIL", fn.__name__, "check crashed", repr(e)[:200])
    for area, item in [("§5 frontier", "Claude Opus 5.5 live calls (needs Anthropic key)"),
                       ("§16 firewall", "firewall hardening (deferred by operator)"),
                       ("§18.4 AIDE / CVE scans", "deferred by operator"),
                       ("llama.cpp", "runs under the operator, not a managed service (by decision)")]:
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
