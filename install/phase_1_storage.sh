#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 1: Storage & Git
# Installs: PostgreSQL (for LiteLLM), SQLite kent.db, Gitea
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

detect_distro

# ─── 1.0 Pre-flight ──────────────────────────────────────────────────────────
log "Pre-flight checks..."
test_gate "kent user exists"    "id kent >/dev/null 2>&1"
test_gate "litellm user exists" "id litellm >/dev/null 2>&1"
test_gate "gitea user exists"   "id gitea >/dev/null 2>&1"

# ─── 1.1 SQLite — kent.db ────────────────────────────────────────────────────
log "Verifying SQLite..."
test_gate "sqlite3 installed" "sqlite3 --version"

if [[ ! -f "$KENT_DB" ]]; then
    sqlite3 "$KENT_DB" < "${KENT_ROOT}/schemas/kent.sql"
    chown kent:kent "$KENT_DB"
    chmod 0600 "$KENT_DB"
    log "Created: $KENT_DB"
else
    log "Already exists: $KENT_DB"
fi

# ─── 1.2 PostgreSQL (for LiteLLM) ────────────────────────────────────────────
log "Installing PostgreSQL..."
if [[ "$DISTRO_FAMILY" == "debian" ]]; then
    pkg_install postgresql postgresql-client
    # Ensure a cluster exists (needed after purge+reinstall on Debian/Ubuntu)
    if ! sudo -u postgres pg_lsclusters 2>/dev/null | grep -q "online"; then
        PG_VER=$(ls /usr/lib/postgresql/ 2>/dev/null | sort -V | tail -1)
        if [[ -n "$PG_VER" ]]; then
            pg_createcluster "$PG_VER" main --start 2>/dev/null || true
            log "  Created PostgreSQL cluster: version $PG_VER"
        fi
    fi
else
    pkg_install postgresql-server postgresql
    postgresql-setup --initdb 2>/dev/null || true
fi

enable_and_start postgresql
wait_for_port "$POSTGRES_PORT" "PostgreSQL"
# Verify PostgreSQL is accepting connections (port open != ready to query)
log "  Waiting for PostgreSQL to accept connections..."
PG_READY=0
for i in $(seq 1 38); do
    if sudo -u postgres pg_isready -q 2>/dev/null; then
        PG_READY=1
        break
    fi
    sleep 1
done
if [[ $PG_READY -eq 0 ]]; then
    die "PostgreSQL not ready after 38 seconds"
fi
log "  PostgreSQL accepting connections (${i}s)"

# Create LiteLLM database and user (idempotent)
PG_PASS=$(openssl rand -hex 16)
PG_PASS_FILE="${SECRETS_DIR}/litellm_pg_password.txt"

if [[ ! -f "$PG_PASS_FILE" ]]; then
    sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${LITELLM_PG_USER}'" | \
        grep -q 1 || \
        sudo -u postgres psql -c "CREATE USER ${LITELLM_PG_USER} WITH PASSWORD '${PG_PASS}';"

    sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${LITELLM_PG_DB}'" | \
        grep -q 1 || \
        sudo -u postgres psql -c "CREATE DATABASE ${LITELLM_PG_DB} OWNER ${LITELLM_PG_USER};"

    echo "$PG_PASS" > "$PG_PASS_FILE"
    chmod 0600 "$PG_PASS_FILE"
    chown root:root "$PG_PASS_FILE"
    log "Created PostgreSQL database '${LITELLM_PG_DB}' with user '${LITELLM_PG_USER}'"
else
    PG_PASS=$(cat "$PG_PASS_FILE")
    log "PostgreSQL already configured (password in ${PG_PASS_FILE})"
fi

# Build the DATABASE_URL for LiteLLM
LITELLM_DATABASE_URL="postgresql://${LITELLM_PG_USER}:${PG_PASS}@localhost:${POSTGRES_PORT}/${LITELLM_PG_DB}"

# Save for Phase 3
echo "$LITELLM_DATABASE_URL" > "${SECRETS_DIR}/litellm_database_url.txt"
chmod 0600 "${SECRETS_DIR}/litellm_database_url.txt"
chown root:root "${SECRETS_DIR}/litellm_database_url.txt"

# ─── 1.3 Gitea ───────────────────────────────────────────────────────────────
log "Installing Gitea ${GITEA_VERSION}..."

if ! command -v gitea &>/dev/null; then
    wget -q "https://dl.gitea.io/gitea/${GITEA_VERSION}/gitea-${GITEA_VERSION}-linux-amd64" \
         -O /usr/local/bin/gitea
    chmod +x /usr/local/bin/gitea
fi

mkdir -p "${GITEA_HOME}"/{custom/conf,data,log}
chown -R gitea:gitea "$GITEA_HOME"

# Gitea systemd unit
cat > /etc/systemd/system/gitea.service << EOF
[Unit]
Description=Gitea (Git with a cup of tea)
After=network.target

[Service]
Type=simple
User=gitea
Group=gitea
WorkingDirectory=${GITEA_HOME}
ExecStart=/usr/local/bin/gitea web --work-path ${GITEA_HOME} --custom-path ${GITEA_HOME}/custom --config ${GITEA_HOME}/custom/conf/app.ini
Restart=always
RestartSec=3

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=${GITEA_HOME}

[Install]
WantedBy=multi-user.target
EOF

# Gitea config — all paths explicit
if [[ ! -f "${GITEA_HOME}/custom/conf/app.ini" ]]; then
    cat > "${GITEA_HOME}/custom/conf/app.ini" << EOF
APP_DATA_PATH = ${GITEA_HOME}/data

[server]
HTTP_ADDR = 127.0.0.1
HTTP_PORT = ${GITEA_PORT}
ROOT_URL  = http://localhost:${GITEA_PORT}/
DISABLE_SSH = false
START_SSH_SERVER = false

[database]
DB_TYPE = sqlite3
PATH    = ${GITEA_HOME}/data/gitea.db

[repository]
ROOT = ${GITEA_HOME}/data/gitea-repositories

[log]
ROOT_PATH = ${GITEA_HOME}/log

[security]
INSTALL_LOCK = true

[service]
DISABLE_REGISTRATION = true
REQUIRE_SIGNIN_VIEW  = false
EOF
    chown gitea:gitea "${GITEA_HOME}/custom/conf/app.ini"
    log "  Created app.ini"
fi

# Stop any crash-looping instance from a previous failed run
systemctl stop gitea 2>/dev/null || true
systemctl reset-failed gitea 2>/dev/null || true

enable_and_start gitea
wait_for_port "$GITEA_PORT" "Gitea"
wait_for_url "http://localhost:${GITEA_PORT}/api/v1/version" "Gitea API" 150

# ─── 1.4 Gitea Admin User ────────────────────────────────────────────────────
log "Creating Gitea admin user (if not exists)..."
su -s /bin/bash gitea -c \
    "gitea admin user create \
     --work-path ${GITEA_HOME} \
     --custom-path ${GITEA_HOME}/custom \
     --config ${GITEA_HOME}/custom/conf/app.ini \
     --admin --username ${ENTERPRISE_USER} \
     --password 'CHANGE_ME_IMMEDIATELY' --email ${ENTERPRISE_USER}@localhost" \
    2>/dev/null || log "  Admin user already exists."

# ─── 1.5 Template Repo ───────────────────────────────────────────────────────
log "Template repo will be created in Phase 7 (Gent Template)."

# ─── Tests ────────────────────────────────────────────────────────────────────
test_gate "kent.db tables" \
    "sqlite3 '$KENT_DB' \"SELECT name FROM sqlite_master WHERE type='table' AND name='stack_registry';\""
test_gate "kent.db telemetry tables" \
    "sqlite3 '$KENT_DB' \"SELECT name FROM sqlite_master WHERE type='table' AND name='inference_telemetry';\""
test_gate "PostgreSQL running" \
    "sudo -u postgres psql -tc \"SELECT 1 FROM pg_database WHERE datname='${LITELLM_PG_DB}'\" | grep -q 1"
test_gate "Gitea responding" \
    "curl -s -o /dev/null -w '%{http_code}' http://localhost:${GITEA_PORT}/api/v1/version | grep -qE '^(200|403)'"

log "Phase 1 complete."
