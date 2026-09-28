#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/loki/install.sh
# Loki (pinned in ../versions.env, SHA-256 verified), single binary:
#   account loki:loki        code  /opt/kent-loki      config /etc/kent/loki/
#   state   /var/lib/loki    unit  kent-loki.service   127.0.0.1:3100 (+gRPC 9095)
# Usage: sudo ./install.sh [--no-start] [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="loki"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"
source "$HERE/../versions.env"

ACCOUNT=loki; OPT=/opt/kent-loki; CONF=/etc/kent/loki; UNIT=kent-loki.service; PORT=3100; START=1
while [[ $# -gt 0 ]]; do
    case "$1" in --no-start) START=0; shift ;; --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac
done
export DRY_RUN; require_root
audit_event "install started (${INVOCATION_ARGS})"

log "preflight"
command -v unzip >/dev/null || die "unzip is required"
for p in "$PORT" 9095; do
    if ! port_free "$p" && ! systemctl is-active --quiet "$UNIT" 2>/dev/null; then die "port $p in use"; fi
done

ensure_service_account "$ACCOUNT" /var/lib/loki "Loki (Kent)"

claim_path path "$OPT"
ensure_dir "$OPT" 0755 root root
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
fetch_verified "$LOKI_URL" "$LOKI_SHA256" "$WORK/loki.zip"
if [[ "$DRY_RUN" -eq 0 ]]; then
    unzip -q "$WORK/loki.zip" -d "$WORK"
    install -m 0755 -o root -g root "$WORK/loki-linux-amd64" "$OPT/loki"
    echo "$LOKI_VERSION" > "$OPT/VERSION"
fi

claim_path path "$CONF"
ensure_dir "$CONF" 0750 root "$ACCOUNT"
place_file file "$KENT_ROOT/configs/loki.yaml" "$CONF/loki.yaml" 0640 root "$ACCOUNT"
[[ "$DRY_RUN" -eq 1 ]] || "$OPT/loki" -config.file="$CONF/loki.yaml" -verify-config >/dev/null 2>&1 \
    || die "loki rejected $CONF/loki.yaml"

manifest_add state /var/lib/loki
place_file unit "$HERE/$UNIT" "/etc/systemd/system/$UNIT" 0644 root root
run systemctl daemon-reload
if [[ "$START" -eq 1 ]]; then run systemctl enable "$UNIT"; run systemctl restart "$UNIT"; fi

if [[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    for _ in $(seq 1 60); do [[ "$(curl -s -m 2 "http://127.0.0.1:${PORT}/ready")" == "ready" ]] && break; sleep 2; done
    [[ "$(curl -s -m 2 "http://127.0.0.1:${PORT}/ready")" == "ready" ]] || die "loki not ready; see journalctl -u $UNIT"
    # Round-trip: push one line, query it back.
    now=$(date +%s%N)
    curl -sf -m 5 -H 'Content-Type: application/json' -X POST "http://127.0.0.1:${PORT}/loki/api/v1/push" \
        -d "{\"streams\":[{\"stream\":{\"job\":\"kent-install-check\"},\"values\":[[\"$now\",\"kent loki install check\"]]}]}" \
        || die "loki push failed"
    sleep 2
    got=$(curl -sf -m 5 -G "http://127.0.0.1:${PORT}/loki/api/v1/query_range" \
        --data-urlencode 'query={job="kent-install-check"}' --data-urlencode "start=$((now - 60000000000))" \
        | jq -r '[.data.result[].values[][1]] | length')
    [[ "${got:-0}" -ge 1 ]] || die "loki push/query round-trip failed"
    log "OK: $UNIT ready on 127.0.0.1:${PORT}; push/query round-trip works (v${LOKI_VERSION})"
fi
audit_event "install completed"
log "done."
