#!/usr/bin/python3
"""Kent daily digest: gateway usage, security, service health, audit chain, Gents.
Writes <KENT_DATA>/digests/YYYY-MM-DD.md and prints it."""
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kentlib  # noqa: E402


def section(title, rows):
    return [f"## {title}", *(rows or ["- (none)"]), ""]


def main() -> int:
    c = kentlib.conf()
    out = [f"# Kent daily digest — {datetime.now():%A %d %B %Y %H:%M}", ""]
    try:
        calls = kentlib.loki_instant(c, 'sum by (identity, model_group) (count_over_time({unit="kent-litellm.service", kent_event="llm_call"} | json [24h]))')
        out += section("Gateway calls (24h) by identity / requested model",
                       [f"- {r['metric'].get('identity','?')} → {r['metric'].get('model_group','?')}: {r['value'][1]}" for r in calls])
        tiers = kentlib.loki_instant(c, 'sum by (routed_tier) (count_over_time({unit="kent-litellm.service", kent_event="llm_call"} | json | routed_tier != "" [24h]))')
        out += section("Router decisions (24h)", [f"- {r['metric'].get('routed_tier')}: {r['value'][1]}" for r in tiers])
        den = kentlib.loki_instant(c, 'sum by (identity, reason) (count_over_time({unit="kent-litellm.service", kent_event="access_denied"} | json [24h]))')
        out += section("Access denials (24h)", [f"- {r['metric'].get('identity','(no key)')}: {r['metric'].get('reason')} ×{r['value'][1]}" for r in den])
    except Exception as e:  # noqa: BLE001
        out += section("Gateway", [f"- Loki unavailable: {e}"])
    try:
        up = kentlib.prom_query(c, "up")
        out += section("Scrape targets", [f"- {r['metric']['job']}: {'up' if r['value'][1] == '1' else 'DOWN'}" for r in up])
        disk = kentlib.prom_query(c, '1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}')
        out += section("Host", [f"- root filesystem used: {float(disk[0]['value'][1]):.0%}"] if disk else [])
    except Exception as e:  # noqa: BLE001
        out += section("Metrics", [f"- Prometheus unavailable: {e}"])
    failed = subprocess.run(["systemctl", "list-units", "--failed", "--no-legend", "--plain", "kent-*", "alloy*", "grafana*"],
                            capture_output=True, text=True).stdout.split("\n")
    out += section("Failed services", [f"- {l.split()[0]}" for l in failed if l.strip()])
    v = subprocess.run([sys.executable, str(Path(__file__).with_name("kent_audit.py")), "verify"], capture_output=True, text=True)
    out += section("Audit chain", [f"- {(v.stdout or v.stderr).strip().splitlines()[0]}"])
    db = kentlib.kent_db(c)
    out += section("Gents", [f"- {r['stack_id']} {r['display_name']} ({r['status']}, since {r['created_at']})"
                             for r in db.execute("SELECT * FROM stack_registry WHERE status IN ('active','paused')")])
    # kent.db timestamps are ISO 8601 with 'T' and offset; compare in the same format.
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    out += section("Learning reviews (24h)", [f"- {r['stack_id']}#{r['learning_id']}: {r['verdict']} — {r['notes']}"
                                              for r in db.execute("SELECT * FROM learning_reviews WHERE reviewed_at > ?", (since,))])
    out += section("Gent assessments (24h)", [f"- {r['stack_id']}: usefulness {r['usefulness']}/5, {r['verdict']} — {(r['notes'] or '')[:200]}"
                                              for r in db.execute("SELECT * FROM gent_assessments WHERE assessed_at > ?", (since,))])
    try:  # architecture §10.2: the human gets a daily summary of template changes
        commits = kentlib._gitea(c, "GET", f"/repos/{c['TEMPLATE_REPO']}/commits?sha=main&limit=50&since={since}")
        out += section("Template changes (24h)", [f"- {x['sha'][:10]} {x['commit']['message'].splitlines()[0]}" for x in commits])
    except Exception as e:  # noqa: BLE001
        out += section("Template changes (24h)", [f"- Gitea unavailable: {e}"])
    alerts = []
    try:
        for line in Path(c["AUDIT_LOG"]).read_text().splitlines()[-5000:]:
            f = line.split("|")
            if len(f) >= 4 and f[0] > since and f[2] in ("circuit_breaker", "escalation_budget_exceeded", "learning_blocked"):
                alerts.append(f"- {f[0][:16]} {f[2]}: {f[3][:200]}")
    except OSError as e:
        alerts.append(f"- audit log unreadable: {e}")
    out += section("Alerts (24h)", alerts)
    stale = []
    for r in db.execute("SELECT stack_id FROM stack_registry WHERE status='active'"):
        sdb = kentlib.stack_db_ro(c, r["stack_id"])
        if sdb is None:
            continue
        for t in sdb.execute("SELECT task_id, heartbeat_at FROM kanban WHERE status='in_progress' "
                             "AND heartbeat_at < datetime('now','-15 minutes')"):
            stale.append(f"- {r['stack_id']} {t['task_id']}: no heartbeat since {t['heartbeat_at']} UTC")
    out += section("Stale Gent tasks (no heartbeat > 15 min)", stale)
    text = "\n".join(out)
    d = Path(c["KENT_DATA"]) / "digests"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{datetime.now():%Y-%m-%d}.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
