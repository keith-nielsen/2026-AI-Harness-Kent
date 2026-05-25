#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 5: Observability
# Installs: Prometheus, Loki, Promtail, Grafana, node_exporter
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

detect_distro

# ─── 5.1 node_exporter ───────────────────────────────────────────────────────
log "Installing node_exporter ${NODE_EXPORTER_VERSION}..."
if ! command -v node_exporter &>/dev/null; then
    cd /tmp
    wget -q "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz"
    tar xzf "node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz"
    cp "node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64/node_exporter" /usr/local/bin/
    chmod +x /usr/local/bin/node_exporter
    rm -rf "node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64"*
fi

ensure_system_user "node_exporter" "/dev/null"

cat > /etc/systemd/system/node-exporter.service << EOF
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
Type=simple
User=node_exporter
ExecStart=/usr/local/bin/node_exporter --web.listen-address=127.0.0.1:${NODE_EXPORTER_PORT}
Restart=always

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

enable_and_start node-exporter
wait_for_port "$NODE_EXPORTER_PORT" "node_exporter"

# ─── 5.2 Prometheus ──────────────────────────────────────────────────────────
log "Installing Prometheus ${PROMETHEUS_VERSION}..."
if ! command -v prometheus &>/dev/null; then
    cd /tmp
    wget -q "https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}/prometheus-${PROMETHEUS_VERSION}.linux-amd64.tar.gz"
    tar xzf "prometheus-${PROMETHEUS_VERSION}.linux-amd64.tar.gz"
    cp "prometheus-${PROMETHEUS_VERSION}.linux-amd64/prometheus" /usr/local/bin/
    cp "prometheus-${PROMETHEUS_VERSION}.linux-amd64/promtool" /usr/local/bin/
    chmod +x /usr/local/bin/prometheus /usr/local/bin/promtool
    rm -rf "prometheus-${PROMETHEUS_VERSION}.linux-amd64"*
fi

ensure_system_user "prometheus" "/var/lib/prometheus"
mkdir -p /var/lib/prometheus /etc/prometheus
chown prometheus:prometheus /var/lib/prometheus

cp "${KENT_ROOT}/configs/prometheus.yml" /etc/prometheus/prometheus.yml
chown root:prometheus /etc/prometheus/prometheus.yml

cat > /etc/systemd/system/prometheus.service << EOF
[Unit]
Description=Prometheus Monitoring
After=network.target

[Service]
Type=simple
User=prometheus
ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus --storage.tsdb.retention.time=90d --web.listen-address=127.0.0.1:${PROMETHEUS_PORT}
Restart=always

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
ReadWritePaths=/var/lib/prometheus

[Install]
WantedBy=multi-user.target
EOF

enable_and_start prometheus
wait_for_port "$PROMETHEUS_PORT" "Prometheus"
wait_for_url "http://127.0.0.1:${PROMETHEUS_PORT}/-/healthy" "Prometheus API" 38

# ─── 5.3 Loki ────────────────────────────────────────────────────────────────
log "Installing Loki ${LOKI_VERSION}..."
if ! command -v loki &>/dev/null; then
    cd /tmp
    wget -q "https://github.com/grafana/loki/releases/download/v${LOKI_VERSION}/loki-linux-amd64.zip"
    unzip -o loki-linux-amd64.zip
    mv loki-linux-amd64 /usr/local/bin/loki
    chmod +x /usr/local/bin/loki
    rm -f loki-linux-amd64.zip
fi

ensure_system_user "loki" "/var/lib/loki"
mkdir -p /var/lib/loki /etc/loki
chown loki:loki /var/lib/loki

cp "${KENT_ROOT}/configs/loki.yaml" /etc/loki/loki.yaml

cat > /etc/systemd/system/loki.service << EOF
[Unit]
Description=Grafana Loki Log Aggregation
After=network.target

[Service]
Type=simple
User=loki
ExecStart=/usr/local/bin/loki --config.file=/etc/loki/loki.yaml
Restart=always

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
ReadWritePaths=/var/lib/loki

[Install]
WantedBy=multi-user.target
EOF

enable_and_start loki
wait_for_port "$LOKI_PORT" "Loki"
wait_for_url "http://127.0.0.1:${LOKI_PORT}/ready" "Loki API" 38

# ─── 5.4 Promtail ────────────────────────────────────────────────────────────
log "Installing Promtail..."
if ! command -v promtail &>/dev/null; then
    cd /tmp
    wget -q "https://github.com/grafana/loki/releases/download/v${LOKI_VERSION}/promtail-linux-amd64.zip"
    unzip -o promtail-linux-amd64.zip
    mv promtail-linux-amd64 /usr/local/bin/promtail
    chmod +x /usr/local/bin/promtail
    rm -f promtail-linux-amd64.zip
fi

ensure_system_user "promtail" "/dev/null"
add_to_group "promtail" "agentic-logs"
mkdir -p /etc/promtail

cp "${KENT_ROOT}/configs/promtail.yaml" /etc/promtail/promtail.yaml
sed -i "s|__LOGS_DIR__|${LOGS_DIR}|g" /etc/promtail/promtail.yaml

cat > /etc/systemd/system/promtail.service << EOF
[Unit]
Description=Promtail Log Shipper
After=loki.service
Requires=loki.service

[Service]
Type=simple
User=promtail
ExecStart=/usr/local/bin/promtail --config.file=/etc/promtail/promtail.yaml
Restart=always

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

enable_and_start promtail

# ─── 5.5 Grafana ─────────────────────────────────────────────────────────────
log "Installing Grafana..."
if ! command -v grafana-server &>/dev/null; then
    if [[ "$DISTRO_FAMILY" == "debian" ]]; then
        wget -q -O - https://apt.grafana.com/gpg.key | \
            gpg --dearmor --yes -o /etc/apt/keyrings/grafana.gpg
        echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
            > /etc/apt/sources.list.d/grafana.list
        pkg_update
        pkg_install grafana
    else
        cat > /etc/yum.repos.d/grafana.repo << 'REPO'
[grafana]
name=grafana
baseurl=https://rpm.grafana.com
repo_gpgcheck=1
enabled=1
gpgcheck=1
gpgkey=https://rpm.grafana.com/gpg.key
REPO
        pkg_install grafana
    fi
fi

# Grafana on configured port (default 3001, since 3000 is Gitea)
backup_file /etc/grafana/grafana.ini
sed -i "s/;http_port = 3000/http_port = ${GRAFANA_PORT}/" /etc/grafana/grafana.ini
sed -i 's/;http_addr =/http_addr = 127.0.0.1/' /etc/grafana/grafana.ini

enable_and_start grafana-server
wait_for_port "$GRAFANA_PORT" "Grafana"
wait_for_url "http://127.0.0.1:${GRAFANA_PORT}/api/health" "Grafana API" 38

# ─── 5.6 Grafana Datasources ─────────────────────────────────────────────────
log "Provisioning Grafana datasources..."
mkdir -p /etc/grafana/provisioning/datasources

cat > /etc/grafana/provisioning/datasources/kent.yaml << EOF
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:${PROMETHEUS_PORT}
    isDefault: true
  - name: Loki
    type: loki
    access: proxy
    url: http://localhost:${LOKI_PORT}
EOF

systemctl restart grafana-server
wait_for_url "http://127.0.0.1:${GRAFANA_PORT}/api/health" "Grafana API (post-provisioning)" 38

# ─── Tests ────────────────────────────────────────────────────────────────────
# Prometheus, Loki, and Grafana were verified by wait_for_url above.
# Only test node_exporter separately (no wait_for_url for it).
METRICS_TMP=$(mktemp)
curl -sf "http://127.0.0.1:${NODE_EXPORTER_PORT}/metrics" > "$METRICS_TMP" 2>/dev/null || true
test_gate "node_exporter metrics" "grep -q node_cpu '$METRICS_TMP'"
rm -f "$METRICS_TMP"

log "Phase 5 complete."
