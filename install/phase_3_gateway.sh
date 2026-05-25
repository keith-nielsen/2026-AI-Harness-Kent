#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 3: Gateway
# Installs: LiteLLM proxy (PostgreSQL backend), scoped API keys, auth tests
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

detect_distro

GATEWAY_URL="http://localhost:${GATEWAY_PORT}"

# ─── 3.1 Install LiteLLM ─────────────────────────────────────────────────────
log "Installing LiteLLM..."
if [[ ! -d "${LITELLM_HOME}/venv" ]]; then
    python3.12 -m venv "${LITELLM_HOME}/venv"
fi
"${LITELLM_HOME}/venv/bin/pip" install --upgrade pip
"${LITELLM_HOME}/venv/bin/pip" install 'litellm[proxy]' prisma

# Fix ownership BEFORE prisma — pip ran as root, prisma runs as litellm
chown -R litellm:litellm "${LITELLM_HOME}"
log "  Ownership set: ${LITELLM_HOME} → litellm:litellm"

# ─── 3.2 Prisma DB Push ──────────────────────────────────────────────────────
# Applies schema to PostgreSQL AND generates the Python client.
# MUST run as litellm user — Prisma caches the query engine binary
# under $HOME/.cache/prisma-python/. Running as root would cache it
# under /root/.cache/ which the litellm service user can't access.
SCHEMA_DIR=$(find "${LITELLM_HOME}/venv" -path "*/litellm/proxy/schema.prisma" -printf '%h\n' 2>/dev/null | head -1)
LITELLM_DATABASE_URL=$(cat "${SECRETS_DIR}/litellm_database_url.txt" 2>/dev/null || echo "")

if [[ -n "$SCHEMA_DIR" ]] && [[ -n "$LITELLM_DATABASE_URL" ]]; then
    log "  Running prisma db push from ${SCHEMA_DIR} (as litellm user)..."
    sudo -u litellm sh -c "
        cd '${SCHEMA_DIR}' && \
        PATH='${LITELLM_HOME}/venv/bin':\$PATH \
        DATABASE_URL='${LITELLM_DATABASE_URL}' \
        prisma db push --schema=./schema.prisma
    " || warn "Prisma db push had warnings (may be OK)"
elif [[ -z "$SCHEMA_DIR" ]]; then
    warn "Could not find LiteLLM's schema.prisma"
elif [[ -z "$LITELLM_DATABASE_URL" ]]; then
    die "Missing ${SECRETS_DIR}/litellm_database_url.txt — did Phase 1 complete?"
fi

# ─── 3.3 Deploy Config ───────────────────────────────────────────────────────
log "Deploying gateway config..."
mkdir -p "$LITELLM_CONF"

if [[ "${CLOUD_MODE:-0}" -eq 1 ]]; then
    cp "${KENT_ROOT}/configs/litellm_config.cloud.yaml" "${LITELLM_CONF}/config.yaml"
    log "  Using CLOUD config (all tiers → DeepSeek V4 Flash)"
elif [[ "$DEV_MODE" -eq 1 ]]; then
    cp "${KENT_ROOT}/configs/litellm_config.dev.yaml" "${LITELLM_CONF}/config.yaml"
    log "  Using DEV config (small local models)"
else
    cp "${KENT_ROOT}/configs/litellm_config.yaml" "${LITELLM_CONF}/config.yaml"
    log "  Using PRODUCTION config"
fi

chown root:litellm "${LITELLM_CONF}/config.yaml"
chmod 0640 "${LITELLM_CONF}/config.yaml"

# ─── 3.4 Generate Admin Key (if not exists) ──────────────────────────────────
if [[ ! -f "${SECRETS_DIR}/gateway.env" ]]; then
    log "Generating gateway secrets..."
    ADMIN_KEY="sk-admin-$(openssl rand -hex 32)"

    if [[ -z "$LITELLM_DATABASE_URL" ]]; then
        die "Missing ${SECRETS_DIR}/litellm_database_url.txt — did Phase 1 complete?"
    fi

    cat > "${SECRETS_DIR}/gateway.env" << EOF
LITELLM_ADMIN_KEY=${ADMIN_KEY}
DATABASE_URL=${LITELLM_DATABASE_URL}
DEEPSEEK_API_KEY=CHANGE_ME
ANTHROPIC_API_KEY=CHANGE_ME
EOF
    chmod 0600 "${SECRETS_DIR}/gateway.env"
    chown root:root "${SECRETS_DIR}/gateway.env"
    log "  Created gateway.env — EDIT cloud API keys before Phase 8."
else
    log "  gateway.env already exists."
    ADMIN_KEY=$(grep LITELLM_ADMIN_KEY "${SECRETS_DIR}/gateway.env" | cut -d= -f2)
fi

# ─── 3.5 LiteLLM Systemd Unit ────────────────────────────────────────────────
log "Creating LiteLLM systemd unit..."

cat > /etc/systemd/system/litellm.service << EOF
[Unit]
Description=LiteLLM Inference Gateway
After=ollama.service postgresql.service
Requires=ollama.service
Wants=postgresql.service

[Service]
Type=simple
User=litellm
Group=litellm
WorkingDirectory=${LITELLM_HOME}
ExecStart=${LITELLM_HOME}/venv/bin/litellm --config ${LITELLM_CONF}/config.yaml --host 127.0.0.1 --port ${GATEWAY_PORT}
Restart=always
RestartSec=5

EnvironmentFile=${SECRETS_DIR}/gateway.env

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=${LITELLM_HOME}

[Install]
WantedBy=multi-user.target
EOF

# Idempotent start — don't clobber a running instance
ensure_running litellm
wait_for_port "$GATEWAY_PORT" "LiteLLM Gateway" 75
wait_for_url "${GATEWAY_URL}/health" "LiteLLM API" 75

# ─── 3.6 Generate Scoped API Keys ────────────────────────────────────────────
log "Generating scoped API keys..."

generate_key() {
    local key_alias="$1"
    local models="$2"
    local output_path="$3"

    RESPONSE=$(curl -sf -X POST "${GATEWAY_URL}/key/generate" \
        -H "Authorization: Bearer ${ADMIN_KEY}" \
        -H "Content-Type: application/json" \
        -d "{\"key_alias\": \"${key_alias}\", \"models\": [${models}]}")

    KEY=$(echo "$RESPONSE" | jq -r '.key // empty')
    if [[ -z "$KEY" ]]; then
        warn "Failed to generate key for ${key_alias}. Response: ${RESPONSE}"
        return 1
    fi

    echo -n "$KEY" > "$output_path"
    chmod 0600 "$output_path"
    chown root:root "$output_path"
    log "  Generated key: ${key_alias} → ${output_path}"
}

if [[ ! -f "${SECRETS_DIR}/kent_key.txt" ]] || [[ ! -s "${SECRETS_DIR}/kent_key.txt" ]]; then
    generate_key "kent" '"router","fast","smart","frontier"' "${SECRETS_DIR}/kent_key.txt"
fi

if [[ ! -f "${SECRETS_DIR}/audit_key.txt" ]] || [[ ! -s "${SECRETS_DIR}/audit_key.txt" ]]; then
    generate_key "audit" '"frontier"' "${SECRETS_DIR}/audit_key.txt"
fi

# ─── 3.7 Auth Tests ──────────────────────────────────────────────────────────
log "Running auth tests..."

KENT_KEY=$(cat "${SECRETS_DIR}/kent_key.txt" 2>/dev/null || echo "")
AUDIT_KEY=$(cat "${SECRETS_DIR}/audit_key.txt" 2>/dev/null || echo "")

if [[ -n "$KENT_KEY" ]]; then
    test_gate "Kent key → router allowed" \
        "curl -sf -X POST ${GATEWAY_URL}/v1/chat/completions \
            -H 'Authorization: Bearer ${KENT_KEY}' \
            -H 'Content-Type: application/json' \
            -d '{\"model\":\"router\",\"messages\":[{\"role\":\"user\",\"content\":\"test\"}],\"max_tokens\":5}' \
            | jq -e '.choices[0].message.content' > /dev/null"
fi

if [[ -n "$AUDIT_KEY" ]]; then
    AUDIT_FAST_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        "${GATEWAY_URL}/v1/chat/completions" \
        -H "Authorization: Bearer ${AUDIT_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"fast","messages":[{"role":"user","content":"test"}],"max_tokens":5}')
    test_gate "Audit key → fast DENIED (expect 403)" \
        "[[ '$AUDIT_FAST_STATUS' == '403' ]]"
fi

INVALID_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "${GATEWAY_URL}/v1/chat/completions" \
    -H "Authorization: Bearer sk-invalid-key" \
    -H "Content-Type: application/json" \
    -d '{"model":"fast","messages":[{"role":"user","content":"test"}],"max_tokens":5}')
test_gate "Invalid key rejected (expect 401)" \
    "[[ '$INVALID_STATUS' == '401' ]]"

log "Phase 3 complete."
