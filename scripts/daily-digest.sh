#!/usr/bin/env bash
# =============================================================================
# Kent — Daily Digest
# Generates a morning report: inference stats, stack health, security events.
# Cron: 07:00 local daily
# =============================================================================
set -euo pipefail

# Load runtime config (written by install phase 0)
source /etc/kent/kent.conf
# KENT_DB is loaded from kent.conf

echo "═══════════════════════════════════════════════"
echo "  Kent Daily Digest — $(date '+%A, %B %d %Y')"
echo "═══════════════════════════════════════════════"
echo ""

# ─── Inference Stats ──────────────────────────────────────────────────────────
echo "── Inference (last 24h) ──"
sqlite3 "$KENT_DB" << 'SQL'
SELECT
    model_logical AS tier,
    COUNT(*) AS calls,
    SUM(input_tokens + COALESCE(output_tokens, 0)) AS total_tokens,
    ROUND(AVG(total_latency_ms)) AS avg_latency_ms
FROM inference_telemetry
WHERE timestamp > datetime('now', '-1 day')
GROUP BY model_logical
ORDER BY calls DESC;
SQL
echo ""

# ─── Stack Health ─────────────────────────────────────────────────────────────
echo "── Active Stacks ──"
sqlite3 "$KENT_DB" << 'SQL'
SELECT stack_id, display_name, created_at
FROM stack_registry
WHERE status = 'active'
ORDER BY created_at DESC;
SQL
echo ""

# ─── Frontier Spend ──────────────────────────────────────────────────────────
echo "── Frontier Spend (last 24h) ──"
sqlite3 "$KENT_DB" << 'SQL'
SELECT
    provider,
    COUNT(*) AS calls,
    ROUND(SUM(cost_usd), 4) AS total_cost_usd
FROM frontier_log
WHERE timestamp > datetime('now', '-1 day')
GROUP BY provider;
SQL
echo ""

# ─── Security Events ─────────────────────────────────────────────────────────
echo "── Egress Anomalies (last 24h) ──"
FLAGGED=$(sqlite3 "$KENT_DB" \
    "SELECT COUNT(*) FROM egress_telemetry WHERE flagged = 1 AND timestamp > datetime('now', '-1 day');")
echo "Flagged requests: $FLAGGED"
echo ""

# ─── Template Changes ────────────────────────────────────────────────────────
echo "── Template Changes (last 24h) ──"
sqlite3 "$KENT_DB" << 'SQL'
SELECT timestamp, change_summary, CASE auto_applied WHEN 1 THEN 'auto' ELSE 'manual' END
FROM template_changes
WHERE timestamp > datetime('now', '-1 day')
ORDER BY timestamp DESC;
SQL
echo ""

echo "═══════════════════════════════════════════════"
echo "  End of digest. For details, see Grafana."
echo "═══════════════════════════════════════════════"
