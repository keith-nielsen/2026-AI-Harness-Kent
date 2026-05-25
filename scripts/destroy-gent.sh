#!/usr/bin/env bash
# =============================================================================
# Kent — destroy-gent
# Archives a Gent stack (DB + artifacts), then removes container and user.
# Usage: destroy-gent <stack_id> [--skip-archive]
# =============================================================================
set -euo pipefail

# Load runtime config (written by install phase 0)
source /etc/kent/kent.conf

STACK_ID="${1:?Usage: destroy-gent <stack_id> [--skip-archive]}"
SKIP_ARCHIVE=0

shift || true
[[ "${1:-}" == "--skip-archive" ]] && SKIP_ARCHIVE=1

STACK_DIR="$STACKS_DIR/$STACK_ID"
UNIX_USER="gent-${STACK_ID}"

if [[ ! -d "$STACK_DIR" ]]; then
    echo "[destroy] Stack directory not found: $STACK_DIR"
    exit 1
fi

echo "[destroy] Destroying Gent: ${STACK_ID}"

# ─── Stop Container ──────────────────────────────────────────────────────────
echo "[destroy] Stopping container..."
cd "$STACK_DIR"
docker compose down --remove-orphans 2>/dev/null || true
docker rm -f "gent-${STACK_ID}" 2>/dev/null || true
docker rm -f "litestream-${STACK_ID}" 2>/dev/null || true

# ─── Revoke Gateway Key ──────────────────────────────────────────────────────
echo "[destroy] Revoking gateway key..."
ADMIN_KEY=$(grep LITELLM_ADMIN_KEY "$SECRETS_DIR/gateway.env" | cut -d= -f2)
curl -sf -X POST http://localhost:${GATEWAY_PORT}/key/delete \
    -H "Authorization: Bearer ${ADMIN_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"keys\": [\"gent-${STACK_ID}\"]}" 2>/dev/null || \
    echo "[destroy] WARNING: Could not revoke key (gateway may be down)."

# ─── Archive ──────────────────────────────────────────────────────────────────
if [[ $SKIP_ARCHIVE -eq 0 ]]; then
    ARCHIVE_DIR="${KENT_HOME}/archives"
    mkdir -p "$ARCHIVE_DIR"
    ARCHIVE_PATH="${ARCHIVE_DIR}/${STACK_ID}-$(date +%Y%m%d).tar.zst"
    echo "[destroy] Archiving to ${ARCHIVE_PATH}..."
    tar -cf - -C "$STACKS_DIR" "$STACK_ID" | zstd -3 -o "$ARCHIVE_PATH"
    chown "${ENTERPRISE_USER}:${ENTERPRISE_USER}" "$ARCHIVE_PATH"

    # Update registry with archive path
    sqlite3 "$KENT_DB" << SQL
UPDATE stack_registry
SET status = 'archived',
    archived_at = datetime('now'),
    archive_path = '${ARCHIVE_PATH}'
WHERE stack_id = '${STACK_ID}';
SQL
    echo "[destroy] Archived."
else
    echo "[destroy] Skipping archive."
fi

# ─── Clean Up ─────────────────────────────────────────────────────────────────
echo "[destroy] Removing stack directory..."
rm -rf "$STACK_DIR"

echo "[destroy] Removing Unix user..."
userdel "$UNIX_USER" 2>/dev/null || echo "[destroy] User already removed."

# Update registry status to destroyed
sqlite3 "$KENT_DB" << SQL
UPDATE stack_registry
SET status = 'destroyed', destroyed_at = datetime('now')
WHERE stack_id = '${STACK_ID}';
SQL

echo "[destroy] Gent ${STACK_ID} destroyed."
