#!/usr/bin/env bash
# =============================================================================
# Kent — submit-task
# Submits a task to a Gent's kanban board.
# Usage: submit-task <stack_id> "Task description" [--priority N]
# =============================================================================
set -euo pipefail

# Load runtime config (written by install phase 0)
source /etc/kent/kent.conf

STACK_ID="${1:?Usage: submit-task <stack_id> \"description\" [--priority N]}"
DESCRIPTION="${2:?Usage: submit-task <stack_id> \"description\" [--priority N]}"
PRIORITY=0

shift 2 || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --priority) PRIORITY="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

STACK_DB="$STACKS_DIR/$STACK_ID/data/stack.db"
if [[ ! -f "$STACK_DB" ]]; then
    echo "ERROR: Stack DB not found: $STACK_DB"
    exit 1
fi

TASK_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex[:12])")

sqlite3 "$STACK_DB" << SQL
INSERT INTO kanban (task_id, title, description, status, priority, created_at)
VALUES ('${TASK_ID}', '${DESCRIPTION}', '${DESCRIPTION}', 'backlog', ${PRIORITY}, datetime('now'));
SQL

echo "[submit] Task ${TASK_ID} submitted to gent-${STACK_ID} (priority=${PRIORITY})"
echo "$TASK_ID"
