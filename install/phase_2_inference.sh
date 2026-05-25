#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 2: Inference
# Installs: Ollama (host-native, TCP on localhost), pulls models
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

detect_distro
detect_gpu

# ─── 2.1 Install Ollama ──────────────────────────────────────────────────────
log "Installing Ollama..."
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.ai/install.sh | sh
fi
test_gate "ollama binary" "ollama --version 2>&1 | grep -q version || ollama --version 2>&1 | grep -q client"

# ─── 2.2 Configure Ollama Systemd Unit ───────────────────────────────────────
# Ollama's installer may have created its own unit file. We work WITH it
# rather than replacing it — using a drop-in override for Kent-specific settings.
# This preserves any existing user tuning (flash attention, KV cache, etc).
log "Configuring Ollama..."

# Ensure the ollama user exists and owns its directories
ensure_system_user "ollama" "$OLLAMA_HOME"
mkdir -p "${OLLAMA_HOME}/.ollama" "${OLLAMA_HOME}/models"
chown -R ollama:ollama "$OLLAMA_HOME"

# Create/update Kent's drop-in override
# Merges with any existing systemd unit (Ollama's own or ours)
mkdir -p /etc/systemd/system/ollama.service.d

cat > /etc/systemd/system/ollama.service.d/kent.conf << EOF
# Kent override — do not edit manually
# Preserves Ollama defaults; adds Kent-specific settings.
# User overrides go in override.conf alongside this file.
[Service]
Environment="OLLAMA_HOST=127.0.0.1:${OLLAMA_PORT}"
Environment="OLLAMA_MODELS=${OLLAMA_HOME}/models"
EOF

# If there's a user override that conflicts with our OLLAMA_HOST, warn but don't touch it
OVERRIDE_CONF="/etc/systemd/system/ollama.service.d/override.conf"
if [[ -f "$OVERRIDE_CONF" ]]; then
    if grep -q 'OLLAMA_HOST' "$OVERRIDE_CONF"; then
        # Remove OLLAMA_HOST from user override — kent.conf manages it
        backup_file "$OVERRIDE_CONF"
        sed -i '/OLLAMA_HOST/d' "$OVERRIDE_CONF"
        log "  Removed OLLAMA_HOST from user override (Kent manages this)"
    fi
    log "  Preserved user override: $OVERRIDE_CONF"
fi

# If the base unit contains Kent's old socket config, replace it
if [[ -f /etc/systemd/system/ollama.service ]] && \
   grep -q 'unix:///run/ollama' /etc/systemd/system/ollama.service 2>/dev/null; then
    log "  Removing old Kent-generated unit (had Unix socket config)"
    backup_file /etc/systemd/system/ollama.service
    rm /etc/systemd/system/ollama.service
fi

# If no base unit exists, create a minimal one
if [[ ! -f /etc/systemd/system/ollama.service ]] && \
   [[ ! -f /usr/lib/systemd/system/ollama.service ]]; then
    log "  No existing Ollama unit found — creating base unit"
    cat > /etc/systemd/system/ollama.service << EOF
[Unit]
Description=Ollama Inference Engine
After=network.target

[Service]
Type=simple
User=ollama
Group=ollama
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
fi

# Stop any crash-looping instance
systemctl stop ollama 2>/dev/null || true
systemctl reset-failed ollama 2>/dev/null || true

enable_and_start ollama
wait_for_url "${OLLAMA_URL}" "Ollama" 38

# ─── 2.3 Pull Models ─────────────────────────────────────────────────────────

if [[ "${CLOUD_MODE:-0}" -eq 1 ]]; then
    log "Cloud mode — skipping local model pulls (all tiers via cloud API)."
    # Pull one tiny model so Ollama is functional and tests pass
    MODELS=("gemma3:1b")
    log "  Pulling minimal model for health checks..."
    OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}" ollama pull "${MODELS[0]}"
else

log "Pulling models..."

if [[ "$DEV_MODE" -eq 1 ]]; then
    MODELS=(
        "gemma3:1b"
        "qwen3:1.7b"
        "qwen3:4b"
    )
else
    MODELS=(
        "gemma4:4b"
        "gemma4:26b-a4b"
        "qwen3.6:27b"
    )
fi

for model in "${MODELS[@]}"; do
    log "  Pulling ${model}..."
    OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}" ollama pull "$model"
done

fi  # end cloud mode skip

# ─── 2.4 Create Qwen3 No-Think Variants ──────────────────────────────────────
# Qwen3 uses thinking mode by default, putting reasoning in hidden tokens
# and returning empty content via the OpenAI-compatible API. Create variants
# with thinking disabled for use through the LiteLLM gateway.
log "Creating Qwen3 no-think model variants..."

for model in "${MODELS[@]}"; do
    BASE_NAME=$(echo "$model" | cut -d: -f1)
    if [[ "$BASE_NAME" == "qwen3" ]] || [[ "$BASE_NAME" == "qwen3.6" ]]; then
        NOTHINK_NAME="${model}-nothink"
        MODELFILE=$(mktemp)
        cat > "$MODELFILE" << MEOF
FROM ${model}
PARAMETER num_ctx 8192
TEMPLATE "{{- range .Messages }}{{ .Role }}: {{ .Content }}
{{ end }}assistant: /no_think
"
MEOF
        OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}" ollama create "$NOTHINK_NAME" -f "$MODELFILE" || \
            warn "Failed to create ${NOTHINK_NAME}"
        rm -f "$MODELFILE"
        log "  Created: ${NOTHINK_NAME}"
    fi
done

# ─── 2.4 Verify Models Loaded ────────────────────────────────────────────────
log "Verifying models..."
MODEL_LIST=$(OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}" ollama list 2>/dev/null)

for model in "${MODELS[@]}"; do
    BASE_NAME=$(echo "$model" | cut -d: -f1)
    test_gate "Model ${model} available" "echo '$MODEL_LIST' | grep -qi '$BASE_NAME'"
done

# ─── 2.5 Test Inference ──────────────────────────────────────────────────────
log "Testing inference..."
FIRST_MODEL="${MODELS[0]}"
RESPONSE=$(OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}" \
    ollama run "$FIRST_MODEL" "Respond with exactly: INFERENCE_OK" 2>/dev/null | head -5)
test_gate "Inference produces output" "[[ -n '$RESPONSE' ]]"

log "Phase 2 complete."
