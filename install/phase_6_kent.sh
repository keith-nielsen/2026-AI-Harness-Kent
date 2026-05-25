#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 6: Kent Agent
# Installs: Hermes Agent, configures as Kent, starts hermes gateway
# Kent IS a Hermes instance. The gateway runs as the kent user and exposes
# an OpenAI-compatible API for the operator. No custom systemd unit needed —
# Hermes manages its own hermes-gateway.service.
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

GATEWAY_URL="http://localhost:${GATEWAY_PORT}"
HERMES_HOME="${KENT_HOME}/.hermes"

# ─── 6.1 Clone Hermes Agent ──────────────────────────────────────────────────
log "Installing Hermes Agent from ${HERMES_REPO}..."
if [[ ! -d "$KENT_HOME/hermes" ]]; then
    git clone "$HERMES_REPO" "$KENT_HOME/hermes"
    cd "$KENT_HOME/hermes"
    git checkout "$HERMES_VERSION" 2>/dev/null || git checkout main
    log "  Cloned Hermes Agent (${HERMES_VERSION})"
else
    log "  Hermes already cloned."
fi

# Create venv and install
cd "$KENT_HOME/hermes"
if [[ ! -d "$KENT_HOME/venv" ]]; then
    python3.12 -m venv "$KENT_HOME/venv"
fi
"$KENT_HOME/venv/bin/pip" install --upgrade pip
"$KENT_HOME/venv/bin/pip" install -e ".[all]"

# Fix ownership — venv/hermes created as root, service runs as kent
chown -R kent:kent "$KENT_HOME/hermes" "$KENT_HOME/venv"
chown kent:kent "$KENT_HOME"
log "  Ownership set: hermes + venv → kent:kent"

# ─── 6.2 Configure Hermes as Kent ────────────────────────────────────────────
log "Configuring Hermes..."
sudo -u kent mkdir -p "$HERMES_HOME"

# Read the Kent API key
KENT_KEY=$(cat "${SECRETS_DIR}/kent_key.txt" 2>/dev/null || echo "")
if [[ -z "$KENT_KEY" ]]; then
    warn "No Kent API key found at ${SECRETS_DIR}/kent_key.txt"
    warn "  Generate keys by running: sudo ./install.sh --phase 3"
fi

# Hermes config — model provider pointing at our LiteLLM gateway
sudo -u kent sh -c "
    HOME='${KENT_HOME}'
    export HERMES_HOME='${HERMES_HOME}'
    '${KENT_HOME}/venv/bin/hermes' config set model.provider custom
    '${KENT_HOME}/venv/bin/hermes' config set model.base_url '${GATEWAY_URL}/v1'
    '${KENT_HOME}/venv/bin/hermes' config set model.default smart
    '${KENT_HOME}/venv/bin/hermes' config set api_server.enabled true
    '${KENT_HOME}/venv/bin/hermes' config set api_server.port 8642
    '${KENT_HOME}/venv/bin/hermes' config set api_server.host 127.0.0.1
"

# API key in .env (Hermes's native secret storage)
if [[ -n "$KENT_KEY" ]]; then
    echo "OPENAI_API_KEY=${KENT_KEY}" | sudo -u kent tee "$HERMES_HOME/.env" > /dev/null
    sudo chmod 600 "$HERMES_HOME/.env"
    log "  API key written to ${HERMES_HOME}/.env"
fi

# ─── 6.3 Deploy Kent Skills ──────────────────────────────────────────────────
log "Deploying Kent skills to Hermes..."
SKILLS_SRC="${KENT_ROOT}/skills"
SKILLS_DST="${HERMES_HOME}/skills"

if [[ -d "$SKILLS_SRC" ]]; then
    # Copy skill tree, preserving structure. Hermes auto-discovers on startup.
    mkdir -p "$SKILLS_DST"
    cp -r "$SKILLS_SRC"/* "$SKILLS_DST"/
    chown -R kent:kent "$SKILLS_DST"
    skill_count=$(find "$SKILLS_DST" -name "SKILL.md" | wc -l)
    log "  Deployed ${skill_count} skill(s) to ${SKILLS_DST}"
else
    log "  No skills directory found at ${SKILLS_SRC} — skipping."
fi

# ─── 6.4 Deploy SOUL.md ─────────────────────────────────────────────────────
SOUL_SRC="${KENT_ROOT}/templates/app/SOUL.md"
SOUL_DST="${HERMES_HOME}/SOUL.md"

if [[ -f "$SOUL_SRC" ]]; then
    chown kent:kent "$SOUL_DST"
    cp "$SOUL_SRC" "$SOUL_DST"
    chown kent:kent "$SOUL_DST"
    log "  SOUL.md deployed to ${SOUL_DST}"
else
    warn "No SOUL.md found at ${SOUL_SRC}"
fi

# ─── 6.5 Install and Start Hermes Gateway ────────────────────────────────────
log "Installing Hermes gateway service..."

# Enable systemd linger for kent user (gateway survives logout)
loginctl enable-linger kent 2>/dev/null || warn "loginctl enable-linger failed (may need manual setup)"

# Install the gateway as a user-level systemd service
sudo -u kent sh -c "
    HOME='${KENT_HOME}'
    export HERMES_HOME='${HERMES_HOME}'
    '${KENT_HOME}/venv/bin/hermes' gateway install
" || warn "hermes gateway install had issues"

# Start the gateway
sudo -u kent sh -c "
    HOME='${KENT_HOME}'
    export HERMES_HOME='${HERMES_HOME}'
    '${KENT_HOME}/venv/bin/hermes' gateway start
" || warn "hermes gateway start had issues"

sleep 5

# ─── 6.6 Operator Access ─────────────────────────────────────────────────────
log "Setting up operator access..."

# Create the kent CLI wrapper for the operator
cat > /usr/local/bin/kent << 'WRAPPER'
#!/usr/bin/env bash
# Kent CLI — thin wrapper for operator interaction.
# Kent's brain runs as the hermes gateway (kent user, port 8642).
# This wrapper provides convenient access to hermes commands.
export HERMES_HOME="${HOME}/.hermes"
exec /home/kent/venv/bin/hermes "$@"
WRAPPER
chmod +x /usr/local/bin/kent

# Make kent venv binaries accessible to operator (read+execute, not write)
chmod 0755 "$KENT_HOME"
find "$KENT_HOME/venv" -type d -exec chmod o+rx {} +
find "$KENT_HOME/venv" -type f -executable -exec chmod o+rx {} +
find "$KENT_HOME/venv" -type f ! -executable -exec chmod o+r {} +
# Fix symlinks in bin/
for f in "$KENT_HOME/venv/bin/python" "$KENT_HOME/venv/bin/python3" "$KENT_HOME/venv/bin/python3.12"; do
    [[ -L "$f" ]] && chmod o+rx "$f" 2>/dev/null || true
done

log "  Operator can use: kent chat (or any hermes subcommand)"

# ─── 6.7 Gitea PAT for Kent ──────────────────────────────────────────────────
log "Setting up Gitea PAT for Kent..."
if [[ ! -f "${SECRETS_DIR}/kent_gitea_pat.txt" ]]; then
    su -s /bin/bash gitea -c \
        "gitea admin user create \
         --work-path ${GITEA_HOME} \
         --custom-path ${GITEA_HOME}/custom \
         --config ${GITEA_HOME}/custom/conf/app.ini \
         --username kent \
         --password '$(openssl rand -hex 16)' --email kent@localhost" \
        2>/dev/null || log "  Kent gitea user already exists."

    log "  NOTE: Generate Kent's Gitea PAT manually after setting admin password."
    log "  Save to: ${SECRETS_DIR}/kent_gitea_pat.txt"
    touch "${SECRETS_DIR}/kent_gitea_pat.txt"
    chmod 0600 "${SECRETS_DIR}/kent_gitea_pat.txt"
    chown root:root "${SECRETS_DIR}/kent_gitea_pat.txt"
else
    log "  Kent Gitea PAT already exists."
fi

# ─── 6.8 Cron Jobs ───────────────────────────────────────────────────────────
log "Installing cron jobs..."

mkdir -p "$KENT_HOME/cron"
chown kent:kent "$KENT_HOME/cron"

cat > /etc/cron.d/kent << CRON
# Kent scheduled tasks
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin

# Nightly QA audit — 02:00 UTC
0 2 * * * kent ${KENT_HOME}/cron/nightly-qa-audit.sh >> ${LOGS_DIR}/kent/qa-audit.log 2>&1

# Daily digest — 07:00 local (adjust timezone as needed)
0 7 * * * kent ${KENT_HOME}/cron/daily-digest.sh >> ${LOGS_DIR}/kent/daily-digest.log 2>&1

# HMAC chain validation — 04:30 UTC
30 4 * * * root ${KENT_HOME}/cron/validate-hmac-chain.sh >> ${LOGS_DIR}/kent/hmac-check.log 2>&1

# Shared learnings poll — every 5 minutes
*/5 * * * * kent ${KENT_HOME}/cron/poll-learnings.sh >> ${LOGS_DIR}/kent/learnings.log 2>&1

# Expired grant cleanup — hourly
0 * * * * kent ${KENT_HOME}/cron/cleanup-expired-grants.sh >> ${LOGS_DIR}/kent/grants.log 2>&1
CRON

chmod 0644 /etc/cron.d/kent

# ─── Tests ────────────────────────────────────────────────────────────────────
test_gate "Hermes installed" "'$KENT_HOME/venv/bin/python' -c 'import hermes_cli; print(\"ok\")' 2>/dev/null || test -d '$KENT_HOME/hermes'"
test_gate "Hermes config exists" "test -f '$HERMES_HOME/config.yaml'"
test_gate "SOUL.md deployed" "test -f '$HERMES_HOME/SOUL.md'"
test_gate "crew-designer skill deployed" "test -f '$HERMES_HOME/skills/kent/crew-designer/SKILL.md'"
test_gate "kent CLI wrapper" "test -x /usr/local/bin/kent"
test_gate "Cron file installed" "test -f /etc/cron.d/kent"
test_gate "kent.db exists" "test -f '$KENT_DB'"

# Check gateway status (non-fatal — may take time to start)
if sudo -u kent sh -c "HOME='${KENT_HOME}' '${KENT_HOME}/venv/bin/hermes' gateway status" 2>&1 | grep -q "running"; then
    log "  ✓ Hermes gateway is running"
else
    warn "Hermes gateway not yet running — check: sudo -u kent hermes gateway status"
fi

log "Phase 6 complete."
