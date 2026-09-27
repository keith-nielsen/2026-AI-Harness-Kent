#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/node_exporter/install.sh
# node_exporter (pinned in ../versions.env, SHA-256 verified):
#   account node_exporter:node_exporter   code /opt/kent-node-exporter
#   unit    kent-node-exporter.service    127.0.0.1:9100
# Usage: sudo ./install.sh [--no-start] [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="node_exporter"
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../lib-service.sh"
source "$HERE/../versions.env"

ACCOUNT=node_exporter; OPT=/opt/kent-node-exporter; UNIT=kent-node-exporter.service; PORT=9100; START=1
while [[ $# -gt 0 ]]; do
    case "$1" in --no-start) START=0; shift ;; --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac
done
export DRY_RUN; require_root
audit_event "install started ($*)"

log "preflight"
if ! port_free "$PORT" && ! systemctl is-active --quiet "$UNIT" 2>/dev/null; then die "port $PORT in use"; fi

ensure_service_account "$ACCOUNT" /nonexistent "Prometheus node_exporter (Kent)"

claim_path path "$OPT"
ensure_dir "$OPT" 0755 root root
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
fetch_verified "$NODE_EXPORTER_URL" "$NODE_EXPORTER_SHA256" "$WORK/ne.tgz"
if [[ "$DRY_RUN" -eq 0 ]]; then
    tar -xzf "$WORK/ne.tgz" -C "$WORK"
    install -m 0755 -o root -g root "$WORK/node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64/node_exporter" "$OPT/"
    echo "$NODE_EXPORTER_VERSION" > "$OPT/VERSION"
fi

place_file unit "$HERE/$UNIT" "/etc/systemd/system/$UNIT" 0644 root root
run systemctl daemon-reload
if [[ "$START" -eq 1 ]]; then run systemctl enable "$UNIT"; run systemctl restart "$UNIT"; fi

if [[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    for _ in $(seq 1 20); do curl -sf -m 2 "http://127.0.0.1:${PORT}/metrics" >/dev/null && break; sleep 1; done
    n=$(curl -sf -m 5 "http://127.0.0.1:${PORT}/metrics" | grep -c '^node_' || true)
    [[ "$n" -gt 100 ]] || die "node_exporter not serving metrics (got $n node_* series); see journalctl -u $UNIT"
    log "OK: $UNIT serving $n node_* series on 127.0.0.1:${PORT}"
fi
audit_event "install completed"
log "done."
