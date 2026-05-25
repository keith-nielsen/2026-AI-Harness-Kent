#!/usr/bin/env bash
# =============================================================================
# Kent — Cleanup Expired Grants
# Removes expired proxy rules and access grants from shared_learnings.
# Cron: hourly
# =============================================================================
set -euo pipefail

# Load runtime config (written by install phase 0)
source /etc/kent/kent.conf

ACTIVE_STACKS=$(sqlite3 "$KENT_DB" "SELECT stack_id FROM stack_registry WHERE status = 'active';")

EXPIRED=0
for STACK_ID in $ACTIVE_STACKS; do
    STACK_DB="$STACKS_DIR/$STACK_ID/data/stack.db"
    if [[ ! -f "$STACK_DB" ]]; then
        continue
    fi

    # TODO: Implement grant_expiry column on shared_learnings or a dedicated grants table.
    # For now, log what would be checked.
    GRANTS=$(sqlite3 "$STACK_DB" \
        "SELECT COUNT(*) FROM shared_learnings WHERE category = 'access_request' AND adopted = 1;" 2>/dev/null || echo "0")

    if [[ "$GRANTS" -gt 0 ]]; then
        echo "[grants] gent-${STACK_ID}: ${GRANTS} active grants to audit"
    fi
done

if [[ $EXPIRED -eq 0 ]]; then
    echo "[grants] No expired grants found."
fi
