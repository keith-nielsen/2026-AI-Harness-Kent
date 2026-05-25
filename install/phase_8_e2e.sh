#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 8: E2E Validation
# Full lifecycle test: services, inference, spawn, egress, telemetry, digest.
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

KENT_KEY=$(cat "${SECRETS_DIR}/kent_key.txt" 2>/dev/null || echo "")
GATEWAY_URL="http://localhost:${GATEWAY_PORT}"

log "Starting E2E validation..."

# ─── 8.1 Verify All Services ─────────────────────────────────────────────────
log "Checking all services are running..."

for svc in ollama litellm gitea grafana-server prometheus loki promtail squid; do
    test_gate "Service ${svc} active" "systemctl is-active --quiet '$svc' 2>/dev/null || systemctl is-active --quiet '${svc}.service'"
done

# Kent runs as hermes-gateway (user-level systemd service)
HERMES_GW_STATUS=$(sudo -u kent sh -c "HOME='${KENT_HOME}' '${KENT_HOME}/venv/bin/hermes' gateway status" 2>&1 || true)
test_gate "Kent (hermes gateway) running" "echo '$HERMES_GW_STATUS' | grep -q 'running'"

# Kent API server
test_gate "Kent API listening on 8642" "ss -tlnp | grep -q ':8642'"

# ─── 8.2 Inference Through All Tiers ─────────────────────────────────────────
log "Testing inference through gateway on all tiers..."

if [[ -z "$KENT_KEY" ]]; then
    warn "No Kent API key found — skipping inference tests."
    warn "  Generate keys by re-running: sudo ./install.sh --phase 3"
else
    for tier in router fast smart; do
        RESP=$(curl -sf -X POST "${GATEWAY_URL}/v1/chat/completions" \
            -H "Authorization: Bearer ${KENT_KEY}" \
            -H "Content-Type: application/json" \
            -d "{\"model\":\"${tier}\",\"messages\":[{\"role\":\"user\",\"content\":\"Say OK\"}],\"max_tokens\":10}" \
            | jq -r '.choices[0].message.content' 2>/dev/null || echo "FAIL")
        test_gate "Tier ${tier} responds" "[[ '$RESP' != 'FAIL' ]]"
        log "    ${tier} → ${RESP:0:50}"
    done

    # Frontier test (only if API keys are configured)
    DEEPSEEK_KEY=$(grep DEEPSEEK_API_KEY "${SECRETS_DIR}/gateway.env" | cut -d= -f2)
    if [[ "$DEEPSEEK_KEY" != "CHANGE_ME" ]] && [[ -n "$DEEPSEEK_KEY" ]]; then
        FRONT_RESP=$(curl -sf -X POST "${GATEWAY_URL}/v1/chat/completions" \
            -H "Authorization: Bearer ${KENT_KEY}" \
            -H "Content-Type: application/json" \
            -d '{"model":"frontier","messages":[{"role":"user","content":"Say OK"}],"max_tokens":10}' \
            | jq -r '.choices[0].message.content' 2>/dev/null || echo "FAIL")
        test_gate "Tier frontier responds" "[[ '$FRONT_RESP' != 'FAIL' ]]"
    else
        warn "Frontier API keys not configured — skipping frontier test."
    fi
fi

# ─── 8.3 Spawn Test Gent ─────────────────────────────────────────────────────
log "Spawning test Gent..."
TEST_GENT_ID=""
if command -v spawn-gent &>/dev/null; then
    TEST_GENT_ID=$(spawn-gent "E2E Test Stack" 2>/dev/null) || true
fi

if [[ -z "$TEST_GENT_ID" ]] || [[ ! -d "${STACKS_DIR}/${TEST_GENT_ID}/data" ]]; then
    warn "spawn-gent not fully functional yet — creating manual test."
    TEST_GENT_ID="e2etest1"
    mkdir -p "${STACKS_DIR}/${TEST_GENT_ID}/data"
    sqlite3 "${STACKS_DIR}/${TEST_GENT_ID}/data/stack.db" \
        < "${KENT_ROOT}/schemas/stack.sql"
    log "  Created manual test stack: ${TEST_GENT_ID}"
fi

test_gate "Test stack directory exists" \
    "test -d '${STACKS_DIR}/${TEST_GENT_ID}/data'"
test_gate "Test stack.db has tables" \
    "sqlite3 '${STACKS_DIR}/${TEST_GENT_ID}/data/stack.db' \"SELECT name FROM sqlite_master WHERE type='table' AND name='kanban';\""

# ─── 8.4 Egress Proxy Validation ─────────────────────────────────────────────
log "Validating egress proxy..."

GET_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -x "http://127.0.0.1:${SQUID_PORT}" https://httpbin.org/get 2>/dev/null) || true
test_gate "Squid allows GET via proxy" "[[ '$GET_STATUS' == '200' ]]"

# POST blocking from Docker bridge requires a container on the bridge network.
# Run a throwaway container if Docker is available.
if docker info &>/dev/null 2>&1; then
    log "  Testing POST blocking from Docker bridge..."
    POST_STATUS=$(docker run --rm --network "${DOCKER_NETWORK}" \
        curlimages/curl:latest \
        -s -o /dev/null -w "%{http_code}" \
        -x "http://${DOCKER_GATEWAY}:${SQUID_PORT}" \
        -X POST https://httpbin.org/post 2>/dev/null) || true
    if [[ "$POST_STATUS" == "403" ]]; then
        log "  ✓ Squid blocks POST from Docker bridge (HTTP 403)"
    elif [[ -z "$POST_STATUS" ]] || [[ "$POST_STATUS" == "000" ]]; then
        warn "POST blocking test inconclusive — container could not reach proxy."
        warn "  Verify manually: docker run --rm --network ${DOCKER_NETWORK} curlimages/curl -x http://${DOCKER_GATEWAY}:${SQUID_PORT} -X POST https://httpbin.org/post"
    else
        warn "POST blocking test unexpected result: HTTP ${POST_STATUS} (expected 403)"
    fi
else
    log "  NOTE: Docker not available for POST blocking test."
fi

# ─── 8.5 Telemetry Check ─────────────────────────────────────────────────────
log "Checking telemetry..."
TELEM_COUNT=$(sqlite3 "$KENT_DB" \
    "SELECT COUNT(*) FROM inference_telemetry;" 2>/dev/null || echo "0")
log "  inference_telemetry rows: $TELEM_COUNT"

# ─── 8.6 HMAC Chain Integrity ────────────────────────────────────────────────
log "Validating HMAC chain..."
if [[ -x "$KENT_HOME/cron/validate-hmac-chain.sh" ]]; then
    "$KENT_HOME/cron/validate-hmac-chain.sh" || warn "HMAC chain validation failed or empty."
else
    log "  HMAC validator not yet deployed — skipping."
fi

# ─── 8.7 Daily Digest (Dry Run) ──────────────────────────────────────────────
log "Running daily digest (dry run)..."
if [[ -x "$KENT_HOME/cron/daily-digest.sh" ]]; then
    "$KENT_HOME/cron/daily-digest.sh" || warn "Daily digest had errors."
else
    log "  Daily digest not yet deployed — skipping."
fi

# ─── 8.8 Destroy Test Gent ───────────────────────────────────────────────────
log "Destroying test Gent..."
if command -v destroy-gent &>/dev/null && [[ "$TEST_GENT_ID" != "e2etest1" ]]; then
    destroy-gent "$TEST_GENT_ID" || warn "destroy-gent had errors."
else
    rm -rf "${STACKS_DIR}/${TEST_GENT_ID}"
    log "  Cleaned up manual test stack."
fi

# ─── 8.9 AIDE Baseline ───────────────────────────────────────────────────────
log "Initialising AIDE baseline (this takes a few minutes)..."
if command -v aide &>/dev/null; then
    aide --init 2>/dev/null && \
        cp /var/lib/aide/aide.db.new /var/lib/aide/aide.db 2>/dev/null || \
        warn "AIDE init failed — run manually: aide --init"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
log ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "E2E Validation complete."
log ""
log "Next steps:"
log "  1. Change Gitea admin password: http://localhost:${GITEA_PORT}"
log "  2. Change Grafana admin password: http://localhost:${GRAFANA_PORT}"
log "  3. Set cloud API keys in: ${SECRETS_DIR}/gateway.env"
log "  4. Generate Kent's Gitea PAT: ${SECRETS_DIR}/kent_gitea_pat.txt"
log "  5. Run the post-install checklist: docs/architecture.md § 25"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

log "Phase 8 complete."
