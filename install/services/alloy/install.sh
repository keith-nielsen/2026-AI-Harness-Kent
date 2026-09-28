#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/alloy/install.sh
# Grafana Alloy from the Grafana apt repository (vendor package; its postinst
# creates the alloy account and adds it to systemd-journal/adm). Kent adds:
#   config  /etc/kent/alloy/config.alloy (journald -> local Loki)
#   drop-in /etc/systemd/system/alloy.service.d/kent.conf (ExecStart + hardening)
# Vendor files (/etc/alloy, /etc/default/alloy) are never edited.
# If Kent installed the package, uninstall purges it and removes the account
# and /var/lib/alloy the package leaves behind.
# Usage: sudo ./install.sh [--live-debugging] [--no-start] [--dry-run]
#   --live-debugging  enable the Alloy UI's live debugging (streams log lines through each
#                     component at http://127.0.0.1:12345). Off unless given: every re-run
#                     without it turns it off again. The UI has no login; any local account
#                     that can reach 127.0.0.1:12345 sees the stream.
# =============================================================================
set -euo pipefail
SERVICE="alloy"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"

CONF=/etc/kent/alloy; UNIT=alloy.service; DROPIN_DIR=/etc/systemd/system/alloy.service.d; START=1; LIVE_DEBUG=0
while [[ $# -gt 0 ]]; do
    case "$1" in --live-debugging) LIVE_DEBUG=1; shift ;; --no-start) START=0; shift ;; --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac
done
export DRY_RUN; require_root
audit_event "install started (${INVOCATION_ARGS})"

log "preflight"
grep -rqs "apt.grafana.com" /etc/apt/sources.list.d/ || die "Grafana apt repository not configured (/etc/apt/sources.list.d/grafana.list)"
if ! port_free 12345 && ! systemctl is-active --quiet "$UNIT" 2>/dev/null; then die "port 12345 in use"; fi
curl -sf -m 3 http://127.0.0.1:3100/ready >/dev/null || warn "Loki not answering on 127.0.0.1:3100; logs will queue until it is"

if pkg_installed alloy && ! manifest_has package alloy; then
    record_unit_state "$UNIT"          # pre-existing package: restore its state on uninstall
else
    run apt-get update -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/grafana.list \
        -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0 -qq
    ensure_package alloy
    if manifest_has package alloy; then
        # Created by the package because Kent installed it: Kent removes them too.
        manifest_add user alloy
        manifest_add group alloy
        manifest_add state /var/lib/alloy
    fi
fi

claim_path path "$CONF"
ensure_dir "$CONF" 0750 root alloy
SRC_CONFIG="$KENT_ROOT/configs/alloy.alloy"
if [[ "$LIVE_DEBUG" -eq 1 ]]; then
    SRC_CONFIG="$(mktemp)"; trap 'rm -f "$SRC_CONFIG"' EXIT
    { printf '// live debugging enabled by install.sh --live-debugging\nlivedebugging {\n  enabled = true\n}\n\n'
      cat "$KENT_ROOT/configs/alloy.alloy"; } > "$SRC_CONFIG"
    chmod 0644 "$SRC_CONFIG"
    log "live debugging ON (re-run without --live-debugging to turn it off)"
fi
place_file file "$SRC_CONFIG" "$CONF/config.alloy" 0640 root alloy
[[ "$DRY_RUN" -eq 1 ]] || /usr/bin/alloy validate "$CONF/config.alloy" >/dev/null || die "alloy rejected $CONF/config.alloy"

ensure_dir "$DROPIN_DIR" 0755 root root
place_file dropin "$HERE/kent.conf" "$DROPIN_DIR/kent.conf" 0644 root root
run systemctl daemon-reload
if [[ "$START" -eq 1 ]]; then
    run systemctl enable "$UNIT"; run systemctl restart "$UNIT"
    # Package installed by Kent → Kent's enablement is undone on uninstall (a pre-existing
    # package's state is restored from its recorded unitstate instead).
    manifest_has package alloy && manifest_add enabled "$UNIT"
fi

if [[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    for _ in $(seq 1 30); do curl -sf -m 2 http://127.0.0.1:12345/-/ready >/dev/null && break; sleep 2; done
    curl -sf -m 2 http://127.0.0.1:12345/-/ready >/dev/null || die "alloy not ready; see journalctl -u $UNIT"
    # End-to-end: the journal must reach Loki (Loki's own unit logs are a safe probe).
    for _ in $(seq 1 30); do
        n=$(curl -s -m 5 -G http://127.0.0.1:3100/loki/api/v1/query_range \
             --data-urlencode 'query={job="systemd-journal"}' --data-urlencode 'limit=5' \
             | jq -r '[.data.result[].values[]] | length' 2>/dev/null || echo 0)
        [[ "${n:-0}" -gt 0 ]] && break; sleep 2
    done
    [[ "${n:-0}" -gt 0 ]] || die "no journal lines reached Loki; see journalctl -u $UNIT"
    log "OK: alloy $(dpkg-query -W -f='${Version}' alloy) ready; journal lines arriving in Loki"
fi
audit_event "install completed"
log "done."
