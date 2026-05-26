#!/usr/bin/env bash
# =============================================================================
# Kent — install.sh
# Master installer. Runs phases sequentially, each with test gates.
# Usage: sudo ./install.sh [--phase N] [--dev] [--cloud] [--dry-run]
# =============================================================================
set -euo pipefail

KENT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${KENT_ROOT}/install"

# --- Defaults ---
START_PHASE=-1  # sentinel: no explicit phase given
DEV_MODE=0
CLOUD_MODE=0
DRY_RUN=0

usage() {
    cat <<EOF
Usage: sudo $0 [OPTIONS]

Options:
  --phase N     Start from phase N (0-8). Overrides auto-resume.
  --dev         Use dev-mode models (small VRAM footprint, NVIDIA CUDA).
  --cloud       All tiers via cloud API (DeepSeek V4 Flash). For flow testing
                on hardware too limited for local models. Requires DEEPSEEK_API_KEY.
  --dry-run     Show what would be done without executing.
  -h, --help    Show this help.

Environment:
  KENT_OPERATOR   Override auto-detected operator username.
                  Default: the user who invoked sudo.

Phases:
  0  OS Base        — ROCm/CUDA, Python 3.12, Docker, users/groups
  1  Storage & Git  — SQLite, Gitea, schema init
  2  Inference      — Ollama, model pulls, Unix socket
  3  Gateway        — LiteLLM, scoped API keys, auth tests
  4  Security       — Squid proxy, Docker network, AIDE, firewall, systemd-oomd
  5  Observability  — Prometheus, Loki, Grafana, exporters
  6  Kent Agent     — Hermes install, kent.db, Gitea PAT, crons
  7  Gent Template  — Docker image, compose template, spawn/destroy
  8  E2E Validation — Full lifecycle test
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase)   START_PHASE="$2"; shift 2 ;;
        --dev)     DEV_MODE=1; shift ;;
        --cloud)   CLOUD_MODE=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *)         echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

# Must run as root
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This installer must be run as root (sudo)."
    exit 1
fi

# Source shared library (detects operator, sets ENTERPRISE_HOME, STATE_DIR)
source "${INSTALL_DIR}/lib.sh"

# Create state directory (now that we know ENTERPRISE_HOME)
mkdir -p "$STATE_DIR"

log "============================================="
log "Kent Installer — $(date -Iseconds)"
log "Operator: ${ENTERPRISE_USER} (home=${ENTERPRISE_HOME})"
log "Dev mode: ${DEV_MODE}"
log "Cloud mode: ${CLOUD_MODE}"
log "============================================="

# Determine start phase
if [[ "$START_PHASE" -eq -1 ]]; then
    # No explicit --phase: auto-resume from last completed
    if [[ -f "${STATE_DIR}/last_completed_phase" ]]; then
        LAST=$(cat "${STATE_DIR}/last_completed_phase")
        START_PHASE=$((LAST + 1))
        if [[ $START_PHASE -gt 8 ]]; then
            log "All phases already completed. Use --phase N to re-run a specific phase."
            exit 0
        fi
        log "Resuming from phase ${START_PHASE} (last completed: ${LAST})"
    else
        START_PHASE=0
    fi
else
    # Explicit --phase: reset state so this phase runs unconditionally
    log "Explicit --phase ${START_PHASE} requested."
    if [[ $START_PHASE -gt 0 ]]; then
        echo $((START_PHASE - 1)) > "${STATE_DIR}/last_completed_phase"
    else
        rm -f "${STATE_DIR}/last_completed_phase"
    fi
fi

log "Start phase: ${START_PHASE}"

# Export for child scripts
export KENT_ROOT DEV_MODE CLOUD_MODE DRY_RUN STATE_DIR ENTERPRISE_USER ENTERPRISE_HOME

# Run phases
for phase in $(seq "$START_PHASE" 8); do
    SCRIPT="${INSTALL_DIR}/phase_${phase}_*.sh"
    SCRIPT_PATH=$(ls $SCRIPT 2>/dev/null | head -1)

    if [[ -z "$SCRIPT_PATH" ]]; then
        log "ERROR: No script found for phase ${phase}"
        exit 1
    fi

    PHASE_NAME=$(basename "$SCRIPT_PATH" .sh | sed 's/phase_[0-9]_//')
    log ""
    log "━━━ Phase ${phase}: ${PHASE_NAME} ━━━"

    if [[ $DRY_RUN -eq 1 ]]; then
        log "[DRY RUN] Would execute: ${SCRIPT_PATH}"
        continue
    fi

    if bash "$SCRIPT_PATH"; then
        echo "$phase" > "${STATE_DIR}/last_completed_phase"
        log "✓ Phase ${phase} complete"
    else
        log "✗ Phase ${phase} FAILED — stopping."
        log "  Fix the issue, then re-run: sudo ./install.sh --phase ${phase}"
        exit 1
    fi
done

log ""
log "============================================="
log "All phases complete. Run the post-install checklist:"
log "  see docs/architecture.md § 25"
log "============================================="
