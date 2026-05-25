#!/usr/bin/env bash
# =============================================================================
# Kent — spawn-gent
# Creates a new Gent fractal stack: UUID, Unix user, DB, secrets, container.
# Usage: spawn-gent "Project Name" [--seed-from <stack_id>]
# =============================================================================
set -euo pipefail

# Load runtime config (written by install phase 0)
source /etc/kent/kent.conf

KENT_ROOT="${KENT_ROOT:-}"
if [[ -z "$KENT_ROOT" ]]; then
    echo "ERROR: KENT_ROOT not set. Source /etc/kent/kent.conf first." >&2
    exit 1
fi

DISPLAY_NAME="${1:-Unnamed Project}"
SEED_FROM=""

shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --seed-from) SEED_FROM="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ─── Generate Stack ID ───────────────────────────────────────────────────────
STACK_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex[:8])")
STACK_DIR="$STACKS_DIR/$STACK_ID"
UNIX_USER="gent-${STACK_ID}"

echo "[spawn] Creating Gent: ${STACK_ID} (${DISPLAY_NAME})" >&2

# ─── Create Unix User ────────────────────────────────────────────────────────
useradd --system --home-dir "$STACK_DIR" --shell /usr/sbin/nologin "$UNIX_USER"
UNIX_UID=$(id -u "$UNIX_USER")
echo "[spawn] Unix user: ${UNIX_USER} (UID ${UNIX_UID})" >&2

# ─── Directory Structure ─────────────────────────────────────────────────────
mkdir -p "$STACK_DIR"/{data,secrets}
chown kent:kent "$STACK_DIR/data"
chown root:root "$STACK_DIR/secrets"
chmod 0700 "$STACK_DIR/secrets"

# ─── Initialise Database ─────────────────────────────────────────────────────
sqlite3 "$STACK_DIR/data/stack.db" < "${KENT_ROOT}/schemas/stack.sql"
chown "$UNIX_USER:$UNIX_USER" "$STACK_DIR/data/stack.db"
chmod 0600 "$STACK_DIR/data/stack.db"

# ─── Generate Gateway Key ────────────────────────────────────────────────────
ADMIN_KEY=$(grep LITELLM_ADMIN_KEY "$SECRETS_DIR/gateway.env" | cut -d= -f2)
GENT_KEY=$(curl -sf -X POST "http://localhost:${GATEWAY_PORT}/key/generate" \
    -H "Authorization: Bearer ${ADMIN_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"key_alias\": \"gent-${STACK_ID}\", \"models\": [\"fast\", \"smart\"]}" \
    | jq -r '.key')

echo "$GENT_KEY" > "$STACK_DIR/secrets/gent_key.txt"
chmod 0600 "$STACK_DIR/secrets/gent_key.txt"
KEY_HASH=$(echo -n "$GENT_KEY" | sha256sum | cut -d' ' -f1)

# ─── Generate Gitea PAT ──────────────────────────────────────────────────────
touch "$STACK_DIR/secrets/gitea_pat.txt"
chmod 0600 "$STACK_DIR/secrets/gitea_pat.txt"

# ─── Template Version ────────────────────────────────────────────────────────
TEMPLATE_VERSION=""
if [[ -d "$KENT_HOME/stack-template/.git" ]]; then
    TEMPLATE_VERSION=$(git -C "$KENT_HOME/stack-template" rev-parse HEAD 2>/dev/null || echo "unknown")
fi

# ─── Register in kent.db ─────────────────────────────────────────────────────
sqlite3 "$KENT_DB" << SQL
INSERT INTO stack_registry (
    stack_id, display_name, status, created_at, unix_user, unix_uid,
    gitea_repo, gateway_key_hash, project_description, template_version,
    seed_source_id
) VALUES (
    '${STACK_ID}', '${DISPLAY_NAME}', 'active', datetime('now'),
    '${UNIX_USER}', ${UNIX_UID}, 'gent-${STACK_ID}', '${KEY_HASH}',
    '${DISPLAY_NAME}', '${TEMPLATE_VERSION}',
    $([ -n "$SEED_FROM" ] && echo "'${SEED_FROM}'" || echo "NULL")
);
SQL

# ─── Seed-From Import ────────────────────────────────────────────────────────
if [[ -n "$SEED_FROM" ]]; then
    echo "[spawn] Seeding from archived stack: ${SEED_FROM}" >&2
    SOURCE_DB="$STACKS_DIR/${SEED_FROM}/data/stack.db"
    if [[ -f "$SOURCE_DB" ]]; then
        sqlite3 "$STACK_DIR/data/stack.db" << SQL
ATTACH '${SOURCE_DB}' AS source;
INSERT INTO seed_context (source_stack_id, source_display_name, imported_at, import_type, content, relevance_notes)
SELECT '${SEED_FROM}',
       (SELECT display_name FROM source.seed_context LIMIT 0),
       datetime('now'), 'domain_learning', summary || ': ' || COALESCE(detail, ''),
       'Auto-imported: applied locally but not globally adopted'
FROM source.shared_learnings
WHERE applied_locally = 1 AND (adopted IS NULL OR adopted = 0);

INSERT INTO seed_context (source_stack_id, imported_at, import_type, content, relevance_notes)
SELECT '${SEED_FROM}', datetime('now'), 'project_memory', key || ': ' || value,
       'Auto-imported: project memory from archived stack'
FROM source.project_memory;
DETACH source;
SQL
        echo "[spawn] Seed import complete." >&2
    else
        echo "[spawn] WARNING: Source DB not found at ${SOURCE_DB}. Skipping seed." >&2
    fi
fi

# ─── Docker Compose ──────────────────────────────────────────────────────────
export STACK_ID STACK_DIR
envsubst < "${KENT_ROOT}/templates/docker-compose.gent.yml" > "$STACK_DIR/docker-compose.yml"

# ─── Litestream Config ───────────────────────────────────────────────────────
cat > "$STACK_DIR/litestream.yml" << YAML
dbs:
  - path: /data/stack.db
    replicas:
      - type: file
        path: ${KENT_HOME}/replicas/${STACK_ID}/
YAML

# ─── Start Container ─────────────────────────────────────────────────────────
cd "$STACK_DIR"
docker compose up -d

echo "[spawn] Gent ${STACK_ID} is running." >&2

# Only the stack ID goes to stdout (for capture by calling scripts)
echo "$STACK_ID"
