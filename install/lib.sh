#!/usr/bin/env bash
# =============================================================================
# Kent — install/lib.sh
# Shared functions for all install phase scripts.
# Sourced by install.sh and each phase_N script.
# =============================================================================

# --- Operator Detection ---
ENTERPRISE_USER="${KENT_OPERATOR:-$(logname 2>/dev/null || echo "${SUDO_USER:-}")}"
if [[ -z "$ENTERPRISE_USER" ]] || [[ "$ENTERPRISE_USER" == "root" ]]; then
    echo "ERROR: Cannot detect operator user. Do not run as root directly."
    echo "  Use: sudo ./install.sh"
    echo "  Or:  KENT_OPERATOR=myuser sudo ./install.sh"
    exit 1
fi
ENTERPRISE_HOME="$(eval echo ~"$ENTERPRISE_USER")"
export ENTERPRISE_USER ENTERPRISE_HOME

# --- State ---
KENT_ROOT="${KENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_DIR="${STATE_DIR:-${ENTERPRISE_HOME}/.kent-install}"
DEV_MODE="${DEV_MODE:-0}"
DRY_RUN="${DRY_RUN:-0}"
LOG_FILE="${STATE_DIR}/install.log"

# --- Derived Paths (single source of truth) ---
# Kent's home is the estate root. All shared infrastructure lives here.
KENT_HOME="/home/kent"
KENT_DB="${KENT_HOME}/kent.db"
SECRETS_DIR="${KENT_HOME}/secrets"
STACKS_DIR="${KENT_HOME}/stacks"
LOGS_DIR="${KENT_HOME}/logs"

# Other service home directories
LITELLM_HOME="/home/litellm"
GITEA_HOME="/home/gitea"
OLLAMA_HOME="/var/lib/ollama"

# PostgreSQL (required by LiteLLM only)
POSTGRES_PORT=5432
LITELLM_PG_DB="litellm"
LITELLM_PG_USER="litellm"

# Ollama
OLLAMA_PORT=11434
OLLAMA_URL="http://127.0.0.1:${OLLAMA_PORT}"

# Config directories
LITELLM_CONF="/etc/litellm"

# Ports
GATEWAY_PORT=4000
GITEA_PORT=3000
GRAFANA_PORT=3001
PROMETHEUS_PORT=9090
LOKI_PORT=3100
SQUID_PORT=3128
NODE_EXPORTER_PORT=9100
PROMTAIL_PORT=9080

# Docker networking
DOCKER_SUBNET="172.30.0.0/24"
DOCKER_GATEWAY="172.30.0.1"
DOCKER_NETWORK="kent-gent-net"

# Component versions
GITEA_VERSION="1.22.6"
NODE_EXPORTER_VERSION="1.8.2"
PROMETHEUS_VERSION="2.53.3"
LOKI_VERSION="3.3.2"

# Upstream repos
HERMES_REPO="https://github.com/NousResearch/hermes-agent.git"
HERMES_VERSION="v2026.5.16"

export KENT_ROOT KENT_HOME LITELLM_HOME SECRETS_DIR STACKS_DIR LOGS_DIR KENT_DB
export GITEA_HOME OLLAMA_HOME OLLAMA_PORT OLLAMA_URL LITELLM_CONF
export GATEWAY_PORT GITEA_PORT GRAFANA_PORT PROMETHEUS_PORT LOKI_PORT SQUID_PORT
export DOCKER_SUBNET DOCKER_GATEWAY DOCKER_NETWORK

# --- Logging ---
log() {
    local msg="[$(date '+%H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

warn() { log "WARN: $*"; }
err()  { log "ERROR: $*"; }
die()  { err "$*"; exit 1; }

# --- Error Trap ---
_on_error() {
    local rc=$?
    local lineno="$1"
    local cmd="$2"
    local script="${BASH_SOURCE[1]:-${BASH_SOURCE[0]:-unknown}}"
    err "━━━ FAILURE ━━━"
    err "  Script:  ${script}"
    err "  Line:    ${lineno}"
    err "  Command: ${cmd}"
    err "  Exit:    ${rc}"
    err "━━━━━━━━━━━━━━━"
}
trap '_on_error ${LINENO} "${BASH_COMMAND}"' ERR

# --- Distro Detection ---
detect_distro() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        case "$ID" in
            ubuntu|linuxmint|pop)
                PKG_MGR="apt"
                FW_MGR="ufw"
                DISTRO_FAMILY="debian"
                ;;
            fedora|rhel|centos|rocky|alma)
                PKG_MGR="dnf"
                FW_MGR="firewalld"
                DISTRO_FAMILY="rhel"
                ;;
            *)
                die "Unsupported distro: $ID"
                ;;
        esac
        export PKG_MGR FW_MGR DISTRO_FAMILY
        log "Detected: $PRETTY_NAME (family=$DISTRO_FAMILY, pkg=$PKG_MGR, fw=$FW_MGR)"
    else
        die "Cannot detect distro — /etc/os-release missing"
    fi
}

# --- Package Management ---
pkg_install() {
    if [[ "$PKG_MGR" == "apt" ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
    else
        dnf install -y "$@"
    fi
}

pkg_update() {
    if [[ "$PKG_MGR" == "apt" ]]; then
        apt-get update -qq
    else
        dnf check-update || true
    fi
}

# --- Pre-modification Backup ---
backup_file() {
    local f="$1"
    if [[ -f "$f" ]] && [[ ! -f "${f}.prekent" ]]; then
        cp -a "$f" "${f}.prekent"
        log "Backed up: ${f} → ${f}.prekent"
    fi
}

# --- User/Group Management ---
ensure_system_user() {
    local user="$1"
    local home="${2:-/dev/null}"
    local shell="${3:-/usr/sbin/nologin}"
    if ! id "$user" &>/dev/null; then
        local create_args=(--system --home-dir "$home" --shell "$shell")
        if getent group "$user" &>/dev/null; then
            create_args+=(-g "$user")
        fi
        useradd "${create_args[@]}" --create-home "$user" 2>/dev/null || \
        useradd "${create_args[@]}" "$user"
        log "Created system user: $user (uid=$(id -u "$user"))"
    else
        local current_home
        current_home=$(getent passwd "$user" | cut -d: -f6)
        if [[ "$home" != "/dev/null" ]] && [[ "$current_home" != "$home" ]]; then
            warn "User $user exists but home is $current_home (expected $home)"
        fi
        if [[ "$home" != "/dev/null" ]] && [[ ! -d "$home" ]]; then
            mkdir -p "$home"
            chown "$user:$(id -gn "$user")" "$home"
            log "Created missing home dir for $user: $home"
        fi
        log "User exists: $user (uid=$(id -u "$user"), home=$current_home)"
    fi
}

ensure_group() {
    local group="$1"
    if ! getent group "$group" &>/dev/null; then
        groupadd --system "$group"
        log "Created group: $group"
    else
        log "Group exists: $group (gid=$(getent group "$group" | cut -d: -f3))"
    fi
}

add_to_group() {
    local user="$1"
    local group="$2"
    if ! id "$user" &>/dev/null; then
        warn "Cannot add $user to $group — user does not exist"
        return 0
    fi
    if ! getent group "$group" &>/dev/null; then
        warn "Cannot add $user to $group — group does not exist"
        return 0
    fi
    if id -nG "$user" 2>/dev/null | grep -qw "$group" 2>/dev/null; then
        log "User $user already in group $group"
    else
        usermod -aG "$group" "$user"
        log "Added $user to group $group"
    fi
}

# --- Service Management ---
enable_and_start() {
    local svc="$1"
    systemctl daemon-reload
    systemctl enable "$svc"
    systemctl start "$svc"
    log "Enabled and started: $svc"
}

# Idempotent service start — only restarts if not currently running.
# Use this for re-runnable install phases to avoid clobbering working services.
ensure_running() {
    local svc="$1"
    systemctl daemon-reload
    systemctl enable "$svc" 2>/dev/null || true
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        log "Service ${svc} already running — not restarting."
    else
        log "Starting ${svc}..."
        systemctl stop "$svc" 2>/dev/null || true
        systemctl reset-failed "$svc" 2>/dev/null || true
        systemctl start "$svc"
        log "Started: $svc"
    fi
}

# --- Test Gate ---
test_gate() {
    local desc="$1"
    local cmd="$2"
    log "  TEST: ${desc}"
    if ( set +o pipefail; eval "$cmd" ); then
        log "  ✓ PASS: ${desc}"
    else
        local rc=$?
        log "  ✗ FAIL: ${desc}"
        log "  Expression: ${cmd}"
        die "Test gate failed (exit $rc): ${desc}"
    fi
}

# --- GPU Detection ---
detect_gpu() {
    if [[ -d /sys/class/drm ]] && ls /sys/class/drm/card*/device/vendor 2>/dev/null | \
       xargs grep -l '0x1002' &>/dev/null; then
        GPU_VENDOR="amd"
    elif command -v nvidia-smi &>/dev/null; then
        GPU_VENDOR="nvidia"
    else
        GPU_VENDOR="none"
    fi
    export GPU_VENDOR
    log "GPU vendor: $GPU_VENDOR"
}

# --- Wait Helpers ---
# All timeouts include +25% margin for slower machines.

wait_for_port() {
    local port="$1"
    local svc="${2:-service}"
    local timeout="${3:-38}"
    local elapsed=0
    log "  Waiting for ${svc} on port ${port}..."
    while ! ss -tlnp | grep -q ":${port} "; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ $elapsed -ge $timeout ]]; then
            die "Timeout waiting for ${svc} on port ${port} (${timeout}s)"
        fi
    done
    log "  ${svc} ready on port ${port} (${elapsed}s)"
}

wait_for_socket() {
    local sock="$1"
    local svc="${2:-service}"
    local timeout="${3:-38}"
    local elapsed=0
    log "  Waiting for ${svc} socket ${sock}..."
    while [[ ! -S "$sock" ]]; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ $elapsed -ge $timeout ]]; then
            die "Timeout waiting for ${svc} socket ${sock} (${timeout}s)"
        fi
    done
    log "  ${svc} socket ready (${elapsed}s)"
}

wait_for_url() {
    local url="$1"
    local svc="${2:-service}"
    local timeout="${3:-75}"
    local interval="${4:-2}"
    local elapsed=0
    log "  Waiting for ${svc} at ${url}..."
    while true; do
        local status="000"
        status=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null) || true
        if [[ "$status" != "000" ]] && [[ -n "$status" ]]; then
            log "  ${svc} ready at ${url} (${elapsed}s, HTTP ${status})"
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
        if [[ $elapsed -ge $timeout ]]; then
            die "Timeout waiting for ${svc} at ${url} (${timeout}s, no HTTP response)"
        fi
    done
}
