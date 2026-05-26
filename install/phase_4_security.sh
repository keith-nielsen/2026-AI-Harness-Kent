#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 4: Security
# Installs: Squid egress proxy, Docker bridge network, AIDE, firewall rules
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

detect_distro

# ─── 4.1 Squid Egress Proxy ──────────────────────────────────────────────────
log "Installing Squid..."
pkg_install squid

backup_file /etc/squid/squid.conf
cp "${KENT_ROOT}/configs/squid.conf" /etc/squid/squid.conf
# Substitute Docker subnet in squid config
sed -i "s|172.30.0.0/24|${DOCKER_SUBNET}|g" /etc/squid/squid.conf
chown root:root /etc/squid/squid.conf
chmod 0644 /etc/squid/squid.conf

# Log directory
mkdir -p /var/log/squid
chown proxy:agentic-logs /var/log/squid
chmod 0750 /var/log/squid

enable_and_start squid
wait_for_port "$SQUID_PORT" "Squid"

# ─── 4.2 Docker Network for Gent Containers ──────────────────────────────────
log "Creating Docker bridge network..."
if ! docker network inspect "$DOCKER_NETWORK" &>/dev/null; then
    docker network create \
        --driver bridge \
        --subnet "$DOCKER_SUBNET" \
        --gateway "$DOCKER_GATEWAY" \
        "$DOCKER_NETWORK"
    log "  Created network: ${DOCKER_NETWORK} (${DOCKER_SUBNET})"
else
    log "  Network ${DOCKER_NETWORK} already exists."
fi

# ─── 4.3 AIDE (File Integrity) ───────────────────────────────────────────────
log "Installing AIDE..."
pkg_install aide

backup_file /etc/aide/aide.conf 2>/dev/null || true

cat > /etc/aide/aide.conf.d/kent.conf << EOF
# Kent-specific AIDE rules
${SECRETS_DIR}        CONTENT_EX
${KENT_DB}            CONTENT_EX
/etc/systemd/system   CONTENT_EX
${LITELLM_CONF}       CONTENT_EX
/etc/squid            CONTENT_EX
/usr/local/bin/ollama CONTENT_EX
/usr/local/bin/gitea  CONTENT_EX
EOF

log "  AIDE baseline will be initialised at end of Phase 8 (after all changes)."

# ─── 4.4 HMAC Audit Chain ────────────────────────────────────────────────────
log "Initialising HMAC audit chain..."
HMAC_LOG="${LOGS_DIR}/hmac_chain.log"
if [[ ! -f "$HMAC_LOG" ]]; then
    touch "$HMAC_LOG"
    chown root:agentic-logs "$HMAC_LOG"
    chmod 0640 "$HMAC_LOG"
    chattr +a "$HMAC_LOG" 2>/dev/null || warn "chattr +a not supported on this filesystem"
    log "  Created: $HMAC_LOG (append-only)"
fi

# ─── 4.5 Firewall ────────────────────────────────────────────────────────────
log "Configuring firewall..."

if [[ "$FW_MGR" == "ufw" ]]; then
    pkg_install ufw
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow ssh
    # All services localhost-only
    for port in $GITEA_PORT $GRAFANA_PORT $GATEWAY_PORT $PROMETHEUS_PORT $LOKI_PORT $SQUID_PORT; do
        ufw allow from 127.0.0.1 to any port "$port"
    done
    ufw --force enable
    log "  UFW configured and enabled."

elif [[ "$FW_MGR" == "firewalld" ]]; then
    pkg_install firewalld
    systemctl enable --now firewalld
    firewall-cmd --set-default-zone=drop
    firewall-cmd --permanent --add-service=ssh
    for port in $GITEA_PORT $GRAFANA_PORT $GATEWAY_PORT $PROMETHEUS_PORT $LOKI_PORT $SQUID_PORT; do
        firewall-cmd --permanent --add-rich-rule="rule family=\"ipv4\" source address=\"127.0.0.1\" port port=\"${port}\" protocol=\"tcp\" accept"
    done
    firewall-cmd --reload
    log "  firewalld configured."
fi

# ─── 4.6 systemd-oomd (Memory Pressure Guard) ────────────────────────────────
log "Installing systemd-oomd..."
pkg_install systemd-oomd

# Create drop-in configs for each Kent service to opt into OOMD management
cat > /etc/systemd/system/ollama.service.d/oomd.conf << 'EOF'
[Service]
ManagedOOMSwap=kill
ManagedOOMMemoryPressure=kill
EOF

cat > /etc/systemd/system/litellm.service.d/oomd.conf << 'EOF'
[Service]
ManagedOOMSwap=kill
ManagedOOMMemoryPressure=kill
EOF

cat > /etc/systemd/system/kent.service.d/oomd.conf << 'EOF'
[Service]
ManagedOOMSwap=kill
ManagedOOMMemoryPressure=kill
ManagedOOMMemoryPressureLimit=70%
EOF

cat > /etc/systemd/system/squid.service.d/oomd.conf << 'EOF'
[Service]
ManagedOOMSwap=kill
ManagedOOMMemoryPressure=kill
EOF

# Gent containers are managed via Docker mem_limit, not systemd-oomd.

enable_and_start systemd-oomd
log "  systemd-oomd enabled with ManagedOOM=kill on ollama, litellm, kent, squid."

# ─── Tests ────────────────────────────────────────────────────────────────────
test_gate "Squid listening" "ss -tlnp | grep -q ':${SQUID_PORT}'"
test_gate "Docker network exists" "docker network inspect ${DOCKER_NETWORK} >/dev/null 2>&1"
test_gate "HMAC log exists" "test -f '$HMAC_LOG'"

# Test egress proxy: verify Squid is proxying (GET from localhost is allowed)
PROXY_GET=$(curl -s -o /dev/null -w "%{http_code}" -x "http://127.0.0.1:${SQUID_PORT}" \
    https://httpbin.org/get 2>/dev/null || echo "000")
test_gate "Squid proxies GET requests" "[[ '$PROXY_GET' == '200' ]]"

# POST blocking from Docker bridge is tested in Phase 8 (E2E) inside a container.
# Host-originated requests match the 'localhost' ACL which is intentionally unrestricted.
log "  NOTE: POST blocking test deferred to Phase 8 (requires container on Docker bridge)."

test_gate "systemd-oomd running" "systemctl is-active --quiet systemd-oomd"
test_gate "OOMD swap monitoring active" "busctl call org.freedesktop.oom1 /org/freedesktop/oom1 org.freedesktop.oom1.Manager GetSwapUsedLimit 2>/dev/null | head -1 | grep -q '90'"
test_gate "Ollama OOMD drop-in exists" "test -f /etc/systemd/system/ollama.service.d/oomd.conf"

log "Phase 4 complete."
