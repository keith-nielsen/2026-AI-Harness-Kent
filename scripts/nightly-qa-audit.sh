#!/usr/bin/env bash
# =============================================================================
# Kent — Nightly QA Audit
# Samples recent tasks, sends to frontier for quality review.
# Cron: 02:00 UTC daily
# =============================================================================
set -euo pipefail

# Load runtime config (written by install phase 0)
source /etc/kent/kent.conf
# KENT_DB is loaded from kent.conf
AUDIT_KEY=$(cat "$SECRETS_DIR/audit_key.txt" 2>/dev/null || echo "")
GATEWAY_URL="http://localhost:${GATEWAY_PORT}"

if [[ -z "$AUDIT_KEY" ]]; then
    echo "[qa-audit] ERROR: No audit key found."
    exit 1
fi

AUDIT_RUN_ID=$(date +%Y%m%d-%H%M%S)
echo "[qa-audit] Starting run: $AUDIT_RUN_ID"

# Sample up to 20 recent tasks from inference_telemetry
# In production, this would query actual task outputs from Gent stacks.
# Placeholder: log the audit run.

TASK_COUNT=$(sqlite3 "$KENT_DB" \
    "SELECT COUNT(*) FROM inference_telemetry WHERE timestamp > datetime('now', '-1 day');")
echo "[qa-audit] Tasks in last 24h: $TASK_COUNT"

# TODO: Implement actual sampling + frontier review when task output storage
# is wired up. For now, record the audit run.
sqlite3 "$KENT_DB" << SQL
INSERT INTO qa_audit_log (audit_run_id, task_id, project_id, original_tier, finding, severity, timestamp)
VALUES ('${AUDIT_RUN_ID}', 'placeholder', 'system', 'N/A',
        'Audit framework operational. ${TASK_COUNT} tasks eligible.', 'info', datetime('now'));
SQL

echo "[qa-audit] Run $AUDIT_RUN_ID complete."
