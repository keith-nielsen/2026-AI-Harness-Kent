#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 2: Local Inference (check only)
# Kent does not install or manage a local inference engine. The user runs their
# own llama.cpp server (llama-server) on a local port; this phase only verifies
# that its OpenAI-compatible endpoint is reachable.
# Endpoint: LOCAL_LLM_URL (default http://127.0.0.1:8080, override with
# KENT_LOCAL_LLM_URL).
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

# ─── 2.1 Probe the user's llama.cpp server ───────────────────────────────────
log "Checking local inference endpoint at ${LOCAL_LLM_URL}..."

# The server may legitimately be stopped at install time (models are started
# on demand), so an unreachable endpoint is a warning, not a failed gate.
if curl -sf --max-time 5 "${LOCAL_LLM_URL}/health" >/dev/null; then
    MODEL_IDS=$(curl -sf --max-time 5 "${LOCAL_LLM_URL}/v1/models" \
        | jq -r '.data[]?.id' 2>/dev/null | paste -sd, -)
    log "  Local llama.cpp server is up. Models: ${MODEL_IDS:-<none reported>}"
    test_gate "Local endpoint serves /v1/models" \
        "curl -sf --max-time 5 '${LOCAL_LLM_URL}/v1/models' | jq -e '.data | length > 0' >/dev/null"
else
    warn "No llama.cpp server answering at ${LOCAL_LLM_URL}."
    warn "  Start your llama-server before using local tiers, or set"
    warn "  KENT_LOCAL_LLM_URL if it listens on a different address/port."
fi

log "Phase 2 complete."
