#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 0: OS Base
# Installs: GPU drivers (ROCm or CUDA), Python 3.12, Docker, system users/groups
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

detect_distro
detect_gpu

# ─── 0.1 Validate Operator ──────────────────────────────────────────────────
log "Validating operator: $ENTERPRISE_USER"
if ! id "$ENTERPRISE_USER" &>/dev/null; then
    die "Operator user '$ENTERPRISE_USER' does not exist."
fi
if [[ ! -d "$ENTERPRISE_HOME" ]]; then
    die "Operator home '$ENTERPRISE_HOME' does not exist."
fi
log "Operator validated: $ENTERPRISE_USER (uid=$(id -u "$ENTERPRISE_USER"), home=$ENTERPRISE_HOME)"

# ─── 0.2 System Packages ────────────────────────────────────────────────────
log "Installing base packages..."
pkg_update
pkg_install curl wget git jq sqlite3 build-essential lm-sensors \
            ca-certificates gnupg software-properties-common \
            logrotate rsync e2fsprogs attr

# ─── 0.3 Python 3.12 ────────────────────────────────────────────────────────
log "Installing Python 3.12..."
if [[ "$DISTRO_FAMILY" == "debian" ]]; then
    add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
    pkg_update
    pkg_install python3.12 python3.12-venv python3.12-dev python3-pip
else
    pkg_install python3.12 python3.12-devel python3-pip
fi
test_gate "Python 3.12 available" "python3.12 --version"

# ─── 0.4 GPU Drivers ────────────────────────────────────────────────────────
if [[ "$DEV_MODE" -eq 1 ]] && [[ "$GPU_VENDOR" == "nvidia" ]]; then
    log "Dev mode: Skipping ROCm, using existing NVIDIA/CUDA drivers."
    test_gate "nvidia-smi responds" "nvidia-smi"
elif [[ "$GPU_VENDOR" == "amd" ]]; then
    log "Installing ROCm for AMD GPU..."
    if [[ "$DISTRO_FAMILY" == "debian" ]]; then
        mkdir -p /etc/apt/keyrings
        wget -q -O - https://repo.radeon.com/rocm/rocm.gpg.key | \
            gpg --dearmor --yes -o /etc/apt/keyrings/rocm.gpg
        echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] \
              https://repo.radeon.com/rocm/apt/latest noble main" \
              > /etc/apt/sources.list.d/rocm.list
        pkg_update
        pkg_install rocm-hip-runtime rocm-smi-lib
    else
        pkg_install rocm-hip-runtime rocm-smi
    fi
    test_gate "rocm-smi responds" "rocm-smi --showid"
elif [[ "$GPU_VENDOR" == "nvidia" ]]; then
    log "NVIDIA GPU detected (production). Ensure CUDA drivers are installed."
    test_gate "nvidia-smi responds" "nvidia-smi"
else
    warn "No GPU detected. Inference will be CPU-only (very slow)."
fi

# ─── 0.5 Docker ─────────────────────────────────────────────────────────────
log "Installing Docker..."
if ! command -v docker &>/dev/null; then
    if [[ "$DISTRO_FAMILY" == "debian" ]]; then
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
            gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
        # Linux Mint: UBUNTU_CODENAME gives the upstream Ubuntu codename.
        # Fall back to VERSION_CODENAME if UBUNTU_CODENAME is unset.
        local_codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
        if [[ -z "$local_codename" ]]; then
            die "Cannot determine Ubuntu codename for Docker repo."
        fi
        echo "deb [arch=$(dpkg --print-architecture) \
              signed-by=/etc/apt/keyrings/docker.gpg] \
              https://download.docker.com/linux/ubuntu \
              ${local_codename} stable" \
              > /etc/apt/sources.list.d/docker.list
        pkg_update
        pkg_install docker-ce docker-ce-cli containerd.io docker-compose-plugin
    else
        pkg_install docker-ce docker-ce-cli containerd.io docker-compose-plugin
    fi
fi
enable_and_start docker
test_gate "Docker daemon running" "docker info --format '{{.ServerVersion}}'" 

# ─── 0.6 System Users & Groups ──────────────────────────────────────────────
log "Creating system users and groups..."

# Functional groups
ensure_group "agentic-logs"
ensure_group "ollama"

# System users
ensure_system_user "kent"     "$KENT_HOME"
ensure_system_user "ollama"   "$OLLAMA_HOME"
ensure_system_user "litellm"  "$LITELLM_HOME"
ensure_system_user "gitea"    "$GITEA_HOME"

# Group memberships — system users
add_to_group "kent"     "ollama"
add_to_group "litellm"  "ollama"

# Operator group memberships
add_to_group "$ENTERPRISE_USER" "docker"
add_to_group "$ENTERPRISE_USER" "agentic-logs"

# promtail is created in Phase 5; skip gracefully if it doesn't exist yet
if id "promtail" &>/dev/null; then
    add_to_group "promtail" "agentic-logs"
fi

# ─── 0.7 Directory Structure ────────────────────────────────────────────────
log "Creating directory structure..."

# Kent's home is the estate root (created by ensure_system_user above)
# Subdirectories with specific ownership
mkdir -p "$STACKS_DIR"
mkdir -p "$SECRETS_DIR"
mkdir -p "$LOGS_DIR"/{gateway,kent,squid}

# Stacks dir: root-owned, Gent subdirs owned by their gent-<id> users
chown root:root            "$STACKS_DIR"
chmod 0755                 "$STACKS_DIR"

# Secrets: root-only, distributed by systemd LoadCredential / Docker secrets
chown root:root            "$SECRETS_DIR"
chmod 0700                 "$SECRETS_DIR"

# Kent owns the logs parent directory; subdirs have specific ownership
chown kent:kent            "$LOGS_DIR"

# Log directories: service-owned, group-readable by agentic-logs
chown litellm:agentic-logs "$LOGS_DIR/gateway"
chown kent:agentic-logs    "$LOGS_DIR/kent"
chmod 0750                 "$LOGS_DIR/gateway"
chmod 0750                 "$LOGS_DIR/kent"

# ─── 0.8 Runtime Config ──────────────────────────────────────────────────────
# Written once at install. Read by standalone scripts (crons, spawn/destroy)
# that don't run under sudo and can't auto-detect the operator.
KENT_CONF="/etc/kent/kent.conf"
mkdir -p /etc/kent
cat > "$KENT_CONF" << EOF
# Kent runtime config — written by install phase 0
ENTERPRISE_USER=${ENTERPRISE_USER}
ENTERPRISE_HOME=${ENTERPRISE_HOME}
KENT_ROOT=${KENT_ROOT}
KENT_HOME=${KENT_HOME}
LITELLM_HOME=${LITELLM_HOME}
SECRETS_DIR=${SECRETS_DIR}
STACKS_DIR=${STACKS_DIR}
LOGS_DIR=${LOGS_DIR}
KENT_DB=${KENT_DB}
GITEA_HOME=${GITEA_HOME}
OLLAMA_PORT=${OLLAMA_PORT}
OLLAMA_URL=${OLLAMA_URL}
GATEWAY_PORT=${GATEWAY_PORT}
GITEA_PORT=${GITEA_PORT}
GRAFANA_PORT=${GRAFANA_PORT}
SQUID_PORT=${SQUID_PORT}
DOCKER_SUBNET=${DOCKER_SUBNET}
DOCKER_GATEWAY=${DOCKER_GATEWAY}
DOCKER_NETWORK=${DOCKER_NETWORK}
EOF
chmod 0644 "$KENT_CONF"
log "Runtime config written: $KENT_CONF"

# ─── Tests ───────────────────────────────────────────────────────────────────
test_gate "Python 3.12" "python3.12 -c \"import sys; assert sys.version_info[:2] == (3,12)\""
test_gate "Docker running" "systemctl is-active --quiet docker"
test_gate "kent user exists" "id kent"
test_gate "ollama group" "getent group ollama"
test_gate "agentic-logs group" "getent group agentic-logs"
test_gate "Operator in docker" "id -nG '${ENTERPRISE_USER}' | grep -qw docker"
test_gate "Secrets dir 0700" "[[ \$(stat -c %a '${SECRETS_DIR}') == '700' ]]"

log "Phase 0 complete."
