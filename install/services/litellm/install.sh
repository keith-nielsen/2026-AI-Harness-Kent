#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/litellm/install.sh
# Installs the LiteLLM gateway as a hardened system service:
#   account  litellm:litellm (system, no login, no home in /home)
#   code     /opt/kent-litellm            (root-owned venv, hash-locked deps)
#   config   /etc/kent/litellm/config.yaml (root:litellm 0640)
#   secrets  /etc/kent/litellm/credentials/ (root-only; via LoadCredential)
#   state    /var/lib/litellm             (systemd StateDirectory)
#   unit     kent-litellm.service         (127.0.0.1:4000)
# Access is identity-based (kent_gateway.py, no PostgreSQL): one generated key per
# identity -- operator and kent (all tiers), gent (local tiers only). The operator
# receives the operator and kent keys under ~/.config/kent/; the gent key stays
# root-only until Gents are deployed. The Anthropic key never leaves the service.
#
# Usage:
#   sudo ./install.sh [--config dev|prod|sim|sim-routed] [--fallback-timeout SECONDS] [--no-start] [--dry-run]
#   sudo ./install.sh --set-anthropic-key          # prompt for the key, restart
#   sudo ./install.sh --set-fallback-timeout SECONDS   # change it, restart
#
# FALLBACK_TIMEOUT (in /etc/kent/litellm/litellm.env) is how long a hung cloud
# call waits before falling back to the local fast tier. Fail-fast errors (bad
# or missing key, refused connection) fall back immediately regardless, so with
# no Anthropic key the value makes no difference.
#
# IMPORTANT: a short FALLBACK_TIMEOUT (e.g. 3s for testing) must be raised once a
# real Anthropic key is set. Opus 5.5 routinely thinks for longer than a few
# seconds, so every real smart/frontier call would time out and silently be
# answered by the local model instead. Use 120 in normal operation:
#   sudo ./install.sh --set-anthropic-key
#   sudo ./install.sh --set-fallback-timeout 120
# This script warns whenever a key is present and the timeout is below
# MIN_SAFE_FALLBACK_TIMEOUT.
# =============================================================================
set -euo pipefail
SERVICE="litellm"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"

ACCOUNT="litellm"
VENV="/opt/kent-litellm"
CONF_DIR="/etc/kent/litellm"
CRED_DIR="${CONF_DIR}/credentials"
STATE_DIR="/var/lib/litellm"
UNIT="kent-litellm.service"
UNIT_PATH="/etc/systemd/system/${UNIT}"
PORT=4000
PYTHON="/usr/bin/python3.12"
# Below this, real Opus calls are likely to time out and fall back (see header).
MIN_SAFE_FALLBACK_TIMEOUT=60

CONFIG_FLAVOR=""
START=1
SET_KEY=0
FALLBACK_TIMEOUT=""
SET_TIMEOUT=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)            CONFIG_FLAVOR="$2"; shift 2 ;;
        --no-start)          START=0; shift ;;
        --dry-run)           DRY_RUN=1; shift ;;
        --set-anthropic-key) SET_KEY=1; shift ;;
        --fallback-timeout)  FALLBACK_TIMEOUT="$2"; shift 2 ;;
        --set-fallback-timeout) FALLBACK_TIMEOUT="$2"; SET_TIMEOUT=1; shift 2 ;;
        -h|--help)           sed -n '2,/^# =====/p' "$0" | sed '$d'; exit 0 ;;
        *)                   die "unknown option: $1" ;;
    esac
done
export DRY_RUN
require_root
audit_event "install started (${INVOCATION_ARGS})"

if [[ -n "$FALLBACK_TIMEOUT" ]] && ! [[ "$FALLBACK_TIMEOUT" =~ ^[0-9]+$ && "$FALLBACK_TIMEOUT" -gt 0 ]]; then
    die "fallback timeout must be a positive whole number of seconds"
fi
ENV_FILE="${CONF_DIR}/litellm.env"
write_env_file() {  # write_env_file <seconds>
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] write ${ENV_FILE}: FALLBACK_TIMEOUT=$1 (0640 root:${ACCOUNT})"
        return 0
    fi
    cat > "$ENV_FILE" <<ENV
# Kent LiteLLM settings — change with: install.sh --set-fallback-timeout SECONDS
# Seconds a hung cloud call waits before falling back to the local fast tier.
# Keep this at 120 once a real Anthropic key is set; a few seconds (testing only)
# makes every real Opus call time out and silently fall back to the local model.
FALLBACK_TIMEOUT=$1
ENV
    chown root:"$ACCOUNT" "$ENV_FILE"; chmod 0640 "$ENV_FILE"
}

# Loud warning when a real Anthropic key is present but the timeout is short.
warn_if_timeout_too_short() {
    [[ "$DRY_RUN" -eq 1 ]] && return 0
    [[ -s "${CRED_DIR}/anthropic_api_key" && -s "$ENV_FILE" ]] || return 0
    local current
    current="$(sed -n 's/^FALLBACK_TIMEOUT=//p' "$ENV_FILE")"
    if [[ "$current" =~ ^[0-9]+$ && "$current" -lt "$MIN_SAFE_FALLBACK_TIMEOUT" ]]; then
        warn "════════════════════════════════════════════════════════════════════"
        warn "FALLBACK_TIMEOUT is ${current}s but an Anthropic key is set."
        warn "Real Opus calls will time out and silently fall back to the local model."
        warn "Fix:  sudo $0 --set-fallback-timeout 120"
        warn "════════════════════════════════════════════════════════════════════"
    fi
}

# ─── Fallback timeout update only ────────────────────────────────────────────
if [[ "$SET_TIMEOUT" -eq 1 ]]; then
    manifest_has path "$CONF_DIR" || die "LiteLLM is not installed by Kent; run install first"
    write_env_file "$FALLBACK_TIMEOUT"
    run systemctl restart "$UNIT"
    log "FALLBACK_TIMEOUT=${FALLBACK_TIMEOUT}s; ${UNIT} restarted."
    warn_if_timeout_too_short
    exit 0
fi

# ─── Secret update only ──────────────────────────────────────────────────────
if [[ "$SET_KEY" -eq 1 ]]; then
    manifest_has path "$CONF_DIR" || die "LiteLLM is not installed by Kent; run install first"
    read -r -s -p "Anthropic API key (input hidden): " KEY; echo
    [[ -n "$KEY" ]] || die "empty key"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] write ${CRED_DIR}/anthropic_api_key (0600 root:root)"
    else
        (umask 077; printf '%s' "$KEY" > "${CRED_DIR}/anthropic_api_key")
    fi
    unset KEY
    run systemctl restart "$UNIT"
    log "Anthropic key updated; ${UNIT} restarted."
    warn_if_timeout_too_short
    exit 0
fi

# Without --config, keep the flavour the installed config was rendered from (default dev).
if [[ -z "$CONFIG_FLAVOR" ]]; then
    case "$(head -1 "${CONF_DIR}/config.yaml" 2>/dev/null)" in
        *litellm_config.yaml*) CONFIG_FLAVOR=prod ;;
        *litellm_config.sim.yaml*) CONFIG_FLAVOR=sim ;;
        *litellm_config.sim-routed.yaml*) CONFIG_FLAVOR=sim-routed ;;
        *) CONFIG_FLAVOR=dev ;;
    esac
fi
case "$CONFIG_FLAVOR" in
    dev)  SRC_CONFIG="${KENT_ROOT}/configs/litellm_config.dev.yaml" ;;
    prod) SRC_CONFIG="${KENT_ROOT}/configs/litellm_config.yaml" ;;
    sim)  SRC_CONFIG="${KENT_ROOT}/configs/litellm_config.sim.yaml" ;;  # simulation only (oracle fixture)
    sim-routed) SRC_CONFIG="${KENT_ROOT}/configs/litellm_config.sim-routed.yaml" ;;  # prod routing, oracle as Opus
    *)    die "--config must be dev, prod, sim or sim-routed" ;;
esac
OPERATOR="$(operator_user)"
OPERATOR_HOME="$(getent passwd "$OPERATOR" | cut -d: -f6)"
OPERATOR_KEY_DIR="${OPERATOR_HOME}/.config/kent"

# ─── 1. Preflight ────────────────────────────────────────────────────────────
log "preflight"
[[ -x "$PYTHON" ]] || die "$PYTHON not found (install python3.12 and python3.12-venv)"
"$PYTHON" -c 'import ensurepip, venv' 2>/dev/null || die "python3.12-venv is not installed"
[[ -f "$SRC_CONFIG" ]] || die "config not found: $SRC_CONFIG"
[[ -f "$HERE/requirements.lock" ]] || die "requirements.lock missing"
[[ -f "$HERE/kent_gateway.py" ]] || die "kent_gateway.py missing"
if ! port_free "$PORT" && ! systemctl is-active --quiet "$UNIT" 2>/dev/null; then
    die "port $PORT is already in use by something other than ${UNIT}"
fi

# ─── 2. Account ──────────────────────────────────────────────────────────────
ensure_service_account "$ACCOUNT" "$STATE_DIR" "LiteLLM gateway (Kent)"

# ─── 3. Code: root-owned venv with hash-locked dependencies ──────────────────
claim_path path "$VENV"
if [[ ! -x "${VENV}/bin/python" ]]; then
    log "creating venv ${VENV}"
    run "$PYTHON" -m venv "$VENV"
fi
log "installing locked dependencies (--require-hashes)"
# Wheels come from the download cache when present; --require-hashes checks them either way.
PIPCACHE=(--no-cache-dir); cache_enabled && PIPCACHE=(--cache-dir "$(cache_dir pip)")
run "${VENV}/bin/pip" install --quiet --no-deps --require-hashes "${PIPCACHE[@]}" \
    -r "$HERE/requirements.lock"
run install -d -m 0755 -o root -g root "${VENV}/libexec"
run install -m 0755 -o root -g root "$HERE/kent-litellm-start" "${VENV}/libexec/kent-litellm-start"
run install -d -m 0755 -o root -g root "${VENV}/lib"
run install -m 0644 -o root -g root "$HERE/kent_gateway.py" "${VENV}/lib/kent_gateway.py"

# ─── 4. Config ───────────────────────────────────────────────────────────────
# No database: drop any database/master-key settings; auth is kent_gateway's.
claim_path path "$CONF_DIR"
ensure_dir "$CONF_DIR" 0750 root "$ACCOUNT"
log "rendering ${CONF_DIR}/config.yaml from $(basename "$SRC_CONFIG")"
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [dry-run] render $SRC_CONFIG -> ${CONF_DIR}/config.yaml (0640 root:${ACCOUNT})"
else
    "${VENV}/bin/python" - "$SRC_CONFIG" "${CONF_DIR}/config.yaml" <<'PY'
import sys, yaml
src, dst = sys.argv[1], sys.argv[2]
cfg = yaml.safe_load(open(src))
gs = cfg.setdefault("general_settings", {})
for key in ("database_url", "enable_key_tracking", "master_key"):
    gs.pop(key, None)
if gs.get("custom_auth") != "kent_gateway.user_api_key_auth":
    sys.exit("config must set general_settings.custom_auth: kent_gateway.user_api_key_auth")
with open(dst, "w") as f:
    f.write(f"# Rendered by Kent from {src} — edit the source and re-run install.sh\n")
    yaml.safe_dump(cfg, f, sort_keys=False)
PY
    chown root:"$ACCOUNT" "${CONF_DIR}/config.yaml"
    chmod 0640 "${CONF_DIR}/config.yaml"
fi

# Tunables: keep an existing value unless --fallback-timeout was given.
if [[ -n "$FALLBACK_TIMEOUT" || ! -s "$ENV_FILE" ]]; then
    write_env_file "${FALLBACK_TIMEOUT:-120}"
fi

# ─── 5. Secrets (root-only) ──────────────────────────────────────────────────
run install -d -m 0700 -o root -g root "$CRED_DIR"
for identity in operator kent gent metrics; do
    if [[ ! -s "${CRED_DIR}/${identity}_key" ]]; then
        log "generating ${identity} key"
        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "  [dry-run] write ${CRED_DIR}/${identity}_key (0600 root:root)"
        else
            (umask 077; printf 'sk-kent-%s-%s' "$identity" "$(openssl rand -hex 32)" > "${CRED_DIR}/${identity}_key")
        fi
    fi
done
if [[ ! -e "${CRED_DIR}/anthropic_api_key" ]]; then
    run install -m 0600 -o root -g root /dev/null "${CRED_DIR}/anthropic_api_key"
fi

# The operator's own key, in the operator's home (e.g. for a personal Hermes or scripts).
# Kent's key is not copied here: Kent runs as the kent account and reads its own copy
# (installed by the kent-core and hermes modules).
ensure_dir "$OPERATOR_KEY_DIR" 0700 "$OPERATOR" "$OPERATOR" "$OPERATOR"
dest="${OPERATOR_KEY_DIR}/litellm_operator_key"
claim_path file "$dest"
run install -m 0600 -o "$OPERATOR" -g "$OPERATOR" "${CRED_DIR}/operator_key" "$dest"
retire_file "${OPERATOR_KEY_DIR}/litellm_kent_key"

# ─── 6. Unit ─────────────────────────────────────────────────────────────────
claim_path unit "$UNIT_PATH"
manifest_add state "$STATE_DIR"
run install -m 0644 -o root -g root "$HERE/${UNIT}" "$UNIT_PATH"
run systemctl daemon-reload
if [[ "$START" -eq 1 ]]; then
    run systemctl enable "$UNIT"
    run systemctl restart "$UNIT"
fi

# ─── 7. Verify ───────────────────────────────────────────────────────────────
if [[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    log "waiting for gateway on 127.0.0.1:${PORT}"
    for _ in $(seq 1 180); do
        curl -sf -m 2 "http://127.0.0.1:${PORT}/health/liveliness" >/dev/null && break
        sleep 1
    done
    code() {  # code <key|""> <method> <path> [model]
        local args=(-s -o /dev/null -w '%{http_code}' -m 30 -X "$2" "http://127.0.0.1:${PORT}$3")
        [[ -n "$1" ]] && args+=(-H "Authorization: Bearer $1")
        [[ -n "${4:-}" ]] && args+=(-H 'Content-Type: application/json' \
            -d "{\"model\":\"$4\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}")
        curl "${args[@]}"
    }
    KENT_KEY="$(cat "${CRED_DIR}/kent_key")"; GENT_KEY="$(cat "${CRED_DIR}/gent_key")"
    NOAUTH=$(code "" GET /v1/models)
    KENT_MODELS=$(code "$KENT_KEY" GET /v1/models)
    GENT_SMART=$(code "$GENT_KEY" POST /v1/chat/completions smart)
    GENT_ADMIN=$(code "$GENT_KEY" POST /key/generate)
    # Regression (found by tests/live): LiteLLM also takes the model from the
    # query string; a restricted identity must be refused, not routed.
    GENT_QUERY=$(code "$GENT_KEY" POST "/v1/chat/completions?model=frontier" fast)
    unset KENT_KEY GENT_KEY
    [[ "$NOAUTH" == "401" ]]      || die "gateway served /v1/models without a key (HTTP $NOAUTH)"
    [[ "$KENT_MODELS" == "200" ]] || die "gateway rejected the kent key (HTTP $KENT_MODELS); see: journalctl -u ${UNIT}"
    [[ "$GENT_SMART" == "403" ]]  || die "SECURITY: gent key was not refused on tier 'smart' (HTTP $GENT_SMART)"
    [[ "$GENT_ADMIN" == "403" ]]  || die "SECURITY: gent key was not refused on an admin route (HTTP $GENT_ADMIN)"
    [[ "$GENT_QUERY" == "403" ]]  || die "SECURITY: gent key with ?model=frontier was not refused (HTTP $GENT_QUERY)"
    log "OK: ${UNIT} is up; no key 401, kent 200, gent refused on smart, admin routes and query-string model."

    # Hermes-style request (carries reasoning_effort) against the local tier.
    # Non-fatal: the local llama.cpp server may legitimately be stopped.
    if curl -sf -m 3 http://127.0.0.1:8080/health >/dev/null 2>&1; then
        KENT_KEY="$(cat "${CRED_DIR}/kent_key")"
        HERMES_STYLE=$(curl -s -o /dev/null -w '%{http_code}' -m 300 -X POST "http://127.0.0.1:${PORT}/v1/chat/completions" \
            -H "Authorization: Bearer ${KENT_KEY}" -H 'Content-Type: application/json' \
            -d '{"model":"fast","reasoning_effort":"medium","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}')
        unset KENT_KEY
        if [[ "$HERMES_STYLE" == "200" ]]; then
            log "OK: local tier accepts Hermes-style requests (reasoning_effort dropped for llama.cpp)."
        else
            warn "local tier rejected a Hermes-style request (HTTP $HERMES_STYLE); check drop_params on local deployments"
        fi
    else
        log "local llama.cpp server not answering on :8080; skipped the Hermes-style request check."
    fi
fi

warn_if_timeout_too_short
audit_event "install completed"
log "done."
