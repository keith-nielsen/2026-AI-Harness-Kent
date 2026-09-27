#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/prometheus/install.sh
# Prometheus (pinned in ../versions.env, SHA-256 verified) as a hardened service:
#   account  prometheus:prometheus     code  /opt/kent-prometheus (root-owned)
#   config   /etc/kent/prometheus/     state /var/lib/prometheus (30d / 5GB)
#   unit     kent-prometheus.service   127.0.0.1:9090
# Usage: sudo ./install.sh [--no-start] [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="prometheus"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"
source "$HERE/../versions.env"

ACCOUNT=prometheus; OPT=/opt/kent-prometheus; CONF=/etc/kent/prometheus
UNIT=kent-prometheus.service; PORT=9090; START=1
while [[ $# -gt 0 ]]; do
    case "$1" in --no-start) START=0; shift ;; --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac
done
export DRY_RUN; require_root
audit_event "install started ($*)"

log "preflight"
if ! port_free "$PORT" && ! systemctl is-active --quiet "$UNIT" 2>/dev/null; then die "port $PORT in use"; fi

ensure_service_account "$ACCOUNT" /var/lib/prometheus "Prometheus (Kent)"

claim_path path "$OPT"
ensure_dir "$OPT" 0755 root root
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
fetch_verified "$PROMETHEUS_URL" "$PROMETHEUS_SHA256" "$WORK/prom.tgz"
if [[ "$DRY_RUN" -eq 0 ]]; then
    tar -xzf "$WORK/prom.tgz" -C "$WORK"
    SRC="$WORK/prometheus-${PROMETHEUS_VERSION}.linux-amd64"
    install -m 0755 -o root -g root "$SRC/prometheus" "$SRC/promtool" "$OPT/"
    echo "$PROMETHEUS_VERSION" > "$OPT/VERSION"
fi

claim_path path "$CONF"
ensure_dir "$CONF" 0750 root "$ACCOUNT"
place_file file "$KENT_ROOT/configs/prometheus.yml" "$CONF/prometheus.yml" 0640 root "$ACCOUNT"
# Gateway scrape key (the LiteLLM "metrics" identity). Re-run this installer after
# the LiteLLM module (re)generates it. Missing key = litellm target shows 401.
run install -d -m 0750 -o root -g "$ACCOUNT" "$CONF/credentials"
if [[ -s /etc/kent/litellm/credentials/metrics_key ]]; then
    place_file file /etc/kent/litellm/credentials/metrics_key "$CONF/credentials/litellm_metrics_key" 0640 root "$ACCOUNT"
else
    warn "LiteLLM metrics key not found; install the litellm module first for the gateway scrape"
fi
[[ "$DRY_RUN" -eq 1 ]] || "$OPT/promtool" check config "$CONF/prometheus.yml" >/dev/null \
    || die "promtool rejected $CONF/prometheus.yml"

manifest_add state /var/lib/prometheus
place_file unit "$HERE/$UNIT" "/etc/systemd/system/$UNIT" 0644 root root
run systemctl daemon-reload
if [[ "$START" -eq 1 ]]; then run systemctl enable "$UNIT"; run systemctl restart "$UNIT"; fi

if [[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    for _ in $(seq 1 30); do curl -sf -m 2 "http://127.0.0.1:${PORT}/-/ready" >/dev/null && break; sleep 1; done
    curl -sf -m 2 "http://127.0.0.1:${PORT}/-/ready" >/dev/null || die "prometheus not ready; see journalctl -u $UNIT"
    log "OK: $UNIT ready on 127.0.0.1:${PORT} (v$("$OPT/prometheus" --version 2>&1 | awk 'NR==1{print $3}'))"
fi
audit_event "install completed"
log "done."
