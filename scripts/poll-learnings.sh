#!/usr/bin/env bash
# =============================================================================
# Kent — Poll Shared Learnings
# Checks all active Gent stacks for new unreviewed shared_learnings entries.
# Cron: every 5 minutes
# =============================================================================
set -euo pipefail

# Load runtime config (written by install phase 0)
source /etc/kent/kent.conf

# Get active stacks
ACTIVE_STACKS=$(sqlite3 "$KENT_DB" "SELECT stack_id FROM stack_registry WHERE status = 'active';")

for STACK_ID in $ACTIVE_STACKS; do
    STACK_DB="$STACKS_DIR/$STACK_ID/data/stack.db"
    if [[ ! -f "$STACK_DB" ]]; then
        continue
    fi

    # Count unreviewed learnings
    COUNT=$(sqlite3 "$STACK_DB" "SELECT COUNT(*) FROM shared_learnings WHERE reviewed = 0;" 2>/dev/null || echo "0")

    if [[ "$COUNT" -gt 0 ]]; then
        echo "[learnings] gent-${STACK_ID}: ${COUNT} unreviewed learnings"

        # TODO: In production, Kent evaluates each learning via the smart tier:
        # 1. Read learning text
        # 2. Send to smart model: "Evaluate this learning for cross-stack applicability"
        # 3. If high-confidence: auto-adopt, commit to template, mark reviewed=1 adopted=1
        # 4. If medium-confidence: present to Human in daily digest
        # 5. If low-quality: mark reviewed=1 adopted=0 with reasoning
    fi
done
