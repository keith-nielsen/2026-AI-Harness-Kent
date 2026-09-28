#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/grafana/install.sh
# Configures the vendor grafana package (Grafana apt repo; account grafana)
# without editing grafana.ini:
#   drop-in   /etc/systemd/system/grafana-server.service.d/kent.conf (127.0.0.1:3001)
#   provision /etc/grafana/provisioning/{datasources,dashboards}/kent.yaml
#   kent      /etc/kent/grafana/ (dashboards, root-only admin password)
# Pre-existing /var/lib/grafana is moved aside and restored on uninstall; the
# unit's prior enabled/active state is restored too.
# Usage: sudo ./install.sh [--no-start] [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="grafana"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"

UNIT=grafana-server.service; CONF=/etc/kent/grafana; PROV=/etc/grafana/provisioning
DROPIN_DIR=/etc/systemd/system/grafana-server.service.d; PORT=3001; START=1
while [[ $# -gt 0 ]]; do
    case "$1" in --no-start) START=0; shift ;; --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac
done
export DRY_RUN; require_root
audit_event "install started (${INVOCATION_ARGS})"
OPERATOR="$(operator_user)"; OPERATOR_HOME="$(getent passwd "$OPERATOR" | cut -d: -f6)"

log "preflight"
pkg_installed grafana || die "grafana package not installed (expected from the Grafana apt repo)"
if ! port_free "$PORT" && ! systemctl is-active --quiet "$UNIT" 2>/dev/null; then die "port $PORT in use"; fi
[[ -d "$PROV/datasources" && -d "$PROV/dashboards" ]] || die "vendor provisioning dirs missing under $PROV"

record_unit_state "$UNIT"
run systemctl stop "$UNIT" || true

# Fresh data directory; the pre-existing one is kept aside for uninstall.
if ! grep -q "^moved /var/lib/grafana " "$(manifest_file)" 2>/dev/null; then
    move_aside /var/lib/grafana
    run install -d -m 0755 -o grafana -g grafana /var/lib/grafana
fi

claim_path path "$CONF"
ensure_dir "$CONF" 0750 root grafana
run install -d -m 0700 -o root -g root "$CONF/credentials"
if [[ ! -s "$CONF/credentials/admin_password" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then echo "  [dry-run] generate $CONF/credentials/admin_password (0600 root)"
    else (umask 077; openssl rand -base64 24 | tr -d '\n=/+' > "$CONF/credentials/admin_password"); fi
fi
run install -d -m 0750 -o root -g grafana "$CONF/dashboards"
for dash in "$KENT_ROOT"/configs/grafana/kent-*.json; do
    run install -m 0640 -o root -g grafana "$dash" "$CONF/dashboards/$(basename "$dash")"
done

place_file file "$KENT_ROOT/configs/grafana/datasources.yaml" "$PROV/datasources/kent.yaml" 0640 root grafana
place_file file "$KENT_ROOT/configs/grafana/dashboards.yaml" "$PROV/dashboards/kent.yaml" 0640 root grafana

OPKEY="$OPERATOR_HOME/.config/kent/grafana_admin_password"
ensure_dir "$(dirname "$OPKEY")" 0700 "$OPERATOR" "$OPERATOR" "$OPERATOR"
claim_path file "$OPKEY"
run install -m 0600 -o "$OPERATOR" -g "$OPERATOR" "$CONF/credentials/admin_password" "$OPKEY"

ensure_dir "$DROPIN_DIR" 0755 root root
place_file dropin "$HERE/kent.conf" "$DROPIN_DIR/kent.conf" 0644 root root
run systemctl daemon-reload
if [[ "$START" -eq 1 ]]; then run systemctl enable "$UNIT"; run systemctl restart "$UNIT"; fi

if [[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    for _ in $(seq 1 60); do curl -sf -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null && break; sleep 2; done
    curl -sf -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null || die "grafana not healthy; see journalctl -u $UNIT"
    # Set the admin password explicitly (works for fresh and existing DBs; the
    # GF_SECURITY_ADMIN_PASSWORD__FILE value only applies when the admin is first
    # created). Then log in exactly once: Grafana locks the account after repeated
    # failures, so never retry-loop on credentials.
    runuser -u grafana -- /usr/share/grafana/bin/grafana cli --homepath /usr/share/grafana \
        --config /etc/grafana/grafana.ini admin reset-admin-password --password-from-stdin \
        cfg:default.paths.data=/var/lib/grafana cfg:default.paths.logs=/var/log/grafana \
        cfg:default.paths.plugins=/var/lib/grafana/plugins \
        < "$CONF/credentials/admin_password" >/dev/null 2>&1 \
        || die "grafana cli could not set the admin password"
    PW="$(cat "$CONF/credentials/admin_password")"
    api() { curl -s -m 10 -u "admin:${PW}" "http://127.0.0.1:${PORT}$1"; }
    [[ "$(api /api/user | jq -r .login)" == "admin" ]] || die "admin login with the Kent password failed (single attempt; see journalctl -u $UNIT)"
    for ds in kent-prometheus kent-loki; do
        st=$(api "/api/datasources/uid/$ds/health" | jq -r .status)
        [[ "$st" == "OK" ]] || die "datasource $ds health: ${st:-no response}"
    done
    [[ "$(api '/api/search?query=Kent%20overview' | jq length)" -ge 1 ]] || die "Kent overview dashboard not provisioned"
    anon=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/datasources")
    [[ "$anon" == "401" ]] || die "anonymous API access not refused (HTTP $anon)"
    unset PW
    log "OK: grafana $(dpkg-query -W -f='${Version}' grafana) on 127.0.0.1:${PORT}; datasources healthy; dashboard provisioned; anonymous refused"
fi
audit_event "install completed"
log "done."
