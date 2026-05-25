#!/usr/bin/env bash
# =============================================================================
# Kent — resetinstall.sh
# Removes all Kent-installed services, users, data, and config.
# Run this before a clean reinstall.
#
# Usage: sudo ./resetinstall.sh [OPTIONS]
#   --include-ollama   Also remove Ollama (binary, models, user)
#   --full-purge       Remove ALL packages installed by Kent (PostgreSQL,
#                      Grafana, Squid, AIDE, Prometheus, Loki, etc.)
# =============================================================================
set -uo pipefail

INCLUDE_OLLAMA=0
FULL_PURGE=0

for arg in "$@"; do
    case "$arg" in
        --include-ollama) INCLUDE_OLLAMA=1 ;;
        --full-purge)     FULL_PURGE=1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Must run as root (sudo)."
    exit 1
fi

# Detect package manager
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    case "$ID" in
        ubuntu|linuxmint|pop) PKG_MGR="apt" ;;
        fedora|rhel|centos|rocky|alma) PKG_MGR="dnf" ;;
        *) PKG_MGR="apt" ;;
    esac
fi

echo "━━━ Kent Reset ━━━"
echo ""
echo "This will destroy ALL Kent data, databases, secrets, and service configs."
echo "  Ollama:     $([ $INCLUDE_OLLAMA -eq 1 ] && echo 'WILL BE REMOVED' || echo 'preserved (use --include-ollama)')"
echo "  Full purge: $([ $FULL_PURGE -eq 1 ] && echo 'YES — all packages will be removed' || echo 'no (use --full-purge)')"
echo ""
read -p "Type YES to confirm: " CONFIRM
if [[ "$CONFIRM" != "YES" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""

# ─── Stop Services ───────────────────────────────────────────────────────────
echo "[reset] Stopping services..."
for svc in kent litellm gitea grafana-server prometheus loki promtail squid node-exporter; do
    systemctl stop "$svc" 2>/dev/null || true
    systemctl disable "$svc" 2>/dev/null || true
done

if [[ $INCLUDE_OLLAMA -eq 1 ]]; then
    systemctl stop ollama 2>/dev/null || true
    systemctl disable ollama 2>/dev/null || true
fi

# ─── Remove Systemd Units ────────────────────────────────────────────────────
echo "[reset] Removing systemd units..."
rm -f /etc/systemd/system/kent.service
rm -f /etc/systemd/system/litellm.service
rm -f /etc/systemd/system/gitea.service
rm -f /etc/systemd/system/node-exporter.service
rm -f /etc/systemd/system/prometheus.service
rm -f /etc/systemd/system/loki.service
rm -f /etc/systemd/system/promtail.service

if [[ $INCLUDE_OLLAMA -eq 1 ]]; then
    rm -f /etc/systemd/system/ollama.service
    rm -rf /etc/systemd/system/ollama.service.d
fi

systemctl daemon-reload

# ─── Remove Users ─────────────────────────────────────────────────────────────
echo "[reset] Removing users..."
for user in kent litellm gitea node_exporter promtail prometheus loki; do
    userdel -r "$user" 2>/dev/null || true
done

if [[ $INCLUDE_OLLAMA -eq 1 ]]; then
    userdel -r ollama 2>/dev/null || true
fi

# ─── Remove Data Directories ─────────────────────────────────────────────────
echo "[reset] Removing data directories..."
rm -rf /home/kent
rm -rf /home/litellm
rm -rf /home/gitea
rm -rf /var/lib/prometheus
rm -rf /var/lib/loki

if [[ $INCLUDE_OLLAMA -eq 1 ]]; then
    rm -rf /var/lib/ollama
fi

# ─── Remove Config Directories ───────────────────────────────────────────────
echo "[reset] Removing config directories..."
rm -rf /etc/kent
rm -rf /etc/litellm
rm -rf /etc/loki
rm -rf /etc/promtail
rm -rf /etc/prometheus
rm -f /etc/grafana/provisioning/datasources/kent.yaml
rm -f /etc/cron.d/kent
rm -f /etc/aide/aide.conf.d/kent.conf 2>/dev/null || true

# ─── Remove PostgreSQL Database ──────────────────────────────────────────────
echo "[reset] Removing PostgreSQL litellm database..."
if command -v psql &>/dev/null && systemctl is-active --quiet postgresql 2>/dev/null; then
    sudo -u postgres psql -c "DROP DATABASE IF EXISTS litellm;" 2>/dev/null || true
    sudo -u postgres psql -c "DROP USER IF EXISTS litellm;" 2>/dev/null || true
    echo "  PostgreSQL litellm database and user removed."
else
    echo "  PostgreSQL not running — skipping."
fi

# ─── Remove Docker Resources ─────────────────────────────────────────────────
echo "[reset] Removing Docker resources..."
docker network rm kent-gent-net 2>/dev/null || true
docker rmi kent/gent:latest 2>/dev/null || true

for c in $(docker ps -a --filter "name=gent-" --format "{{.Names}}" 2>/dev/null); do
    docker rm -f "$c" 2>/dev/null || true
done
for c in $(docker ps -a --filter "name=litestream-" --format "{{.Names}}" 2>/dev/null); do
    docker rm -f "$c" 2>/dev/null || true
done

# ─── Remove Installed Binaries ───────────────────────────────────────────────
echo "[reset] Removing installed binaries..."
rm -f /usr/local/bin/spawn-gent
rm -f /usr/local/bin/destroy-gent
rm -f /usr/local/bin/submit-task
rm -f /usr/local/bin/gitea
rm -f /usr/local/bin/node_exporter
rm -f /usr/local/bin/prometheus /usr/local/bin/promtool
rm -f /usr/local/bin/loki
rm -f /usr/local/bin/promtail

if [[ $INCLUDE_OLLAMA -eq 1 ]]; then
    rm -f /usr/local/bin/ollama
fi

# ─── Remove Install State ────────────────────────────────────────────────────
echo "[reset] Removing install state..."
OPERATOR="${SUDO_USER:-$(logname 2>/dev/null || echo '')}"
if [[ -n "$OPERATOR" ]] && [[ -d "/home/${OPERATOR}/.kent-install" ]]; then
    rm -rf "/home/${OPERATOR}/.kent-install"
    echo "  Removed /home/${OPERATOR}/.kent-install"
fi

# ─── Full Purge (packages) ───────────────────────────────────────────────────
if [[ $FULL_PURGE -eq 1 ]]; then
    echo ""
    echo "[reset] Full purge — removing installed packages..."

    # PostgreSQL
    if command -v psql &>/dev/null; then
        systemctl stop postgresql 2>/dev/null || true
        systemctl disable postgresql 2>/dev/null || true
        if [[ "$PKG_MGR" == "apt" ]]; then
            apt-get purge -y postgresql postgresql-client 2>/dev/null || true
            apt-get autoremove -y 2>/dev/null || true
        else
            dnf remove -y postgresql-server postgresql 2>/dev/null || true
        fi
        rm -rf /var/lib/postgresql /etc/postgresql
        echo "  PostgreSQL purged."
    fi

    # Grafana
    if command -v grafana-server &>/dev/null; then
        systemctl stop grafana-server 2>/dev/null || true
        systemctl disable grafana-server 2>/dev/null || true
        if [[ "$PKG_MGR" == "apt" ]]; then
            apt-get purge -y grafana 2>/dev/null || true
        else
            dnf remove -y grafana 2>/dev/null || true
        fi
        rm -rf /var/lib/grafana /etc/grafana
        echo "  Grafana purged."
    fi

    # Squid
    if command -v squid &>/dev/null; then
        systemctl stop squid 2>/dev/null || true
        systemctl disable squid 2>/dev/null || true
        if [[ "$PKG_MGR" == "apt" ]]; then
            apt-get purge -y squid 2>/dev/null || true
        else
            dnf remove -y squid 2>/dev/null || true
        fi
        rm -rf /var/spool/squid /etc/squid /var/log/squid
        echo "  Squid purged."
    fi

    # AIDE
    if command -v aide &>/dev/null; then
        if [[ "$PKG_MGR" == "apt" ]]; then
            apt-get purge -y aide 2>/dev/null || true
        else
            dnf remove -y aide 2>/dev/null || true
        fi
        rm -rf /var/lib/aide /etc/aide
        echo "  AIDE purged."
    fi

    echo "  Package purge complete."
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "━━━ Reset complete ━━━"
echo ""
if [[ $FULL_PURGE -eq 1 ]]; then
    echo "Full purge done. Only Docker engine, Python, and base OS packages remain."
else
    echo "Preserved (use --full-purge to remove):"
    echo "  - PostgreSQL server (only litellm database removed)"
    echo "  - Grafana package (only Kent datasources removed)"
    echo "  - Squid package"
    echo "  - AIDE package"
fi
if [[ $INCLUDE_OLLAMA -eq 0 ]]; then
    echo "  - Ollama binary, models, and user config (use --include-ollama)"
fi
echo "  - Docker engine"
echo ""
echo "Ready for clean reinstall: sudo ./install.sh [--dev]"
