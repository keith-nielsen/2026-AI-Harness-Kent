#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/searxng/install.sh
# SearXNG metasearch for Kent (Hermes web backend) and, through the Gent bridge, for Gents:
#   image    searxng/searxng pinned by digest (../versions.env)
#   account  kent-searxng:kent-searxng (system, nologin): the container runs as this uid:gid, never
#            as root, and the id is registered on the host (the image's own 977 is not used)
#   unit     kent-searxng.service (systemd-managed container): read-only rootfs, no capabilities,
#            no-new-privileges, memory/pids limits, published on 127.0.0.1:8888 only, logs to
#            journald (tag kent-searxng)
#   config   /etc/kent/searxng/settings.yml (configs/searxng-settings.yml); secret key in
#            /etc/kent/searxng/credentials/env (root 0600), passed as SEARXNG_SECRET
# The operator's own SearXNG (if any) is not touched.
# Usage: sudo ./install.sh [--no-start] [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="searxng"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"
source "$HERE/../versions.env"

CONF=/etc/kent/searxng; UNIT=kent-searxng.service; PORT=8888; START=1
while [[ $# -gt 0 ]]; do
    case "$1" in --no-start) START=0; shift ;; --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac
done
export DRY_RUN; require_root
audit_event "install started ($*)"

log "preflight"
systemctl is-active --quiet docker || die "docker is not running"
if ! port_free "$PORT" && ! systemctl is-active --quiet "$UNIT" 2>/dev/null; then die "port $PORT in use"; fi

if ! docker image inspect "$SEARXNG_IMAGE" >/dev/null 2>&1; then
    run docker pull -q "$SEARXNG_IMAGE" >/dev/null
    manifest_add dockerbase "$SEARXNG_IMAGE"          # pulled by Kent → removed on uninstall
fi

ensure_service_account kent-searxng /nonexistent "Kent SearXNG (container user)"
SX_UID="$(id -u kent-searxng 2>/dev/null || echo 0)"; SX_GID="$(id -g kent-searxng 2>/dev/null || echo 0)"

claim_path path "$CONF"
ensure_dir "$CONF" 0755 root root
ensure_dir "$CONF/credentials" 0700 root root
place_file file "$KENT_ROOT/configs/searxng-settings.yml" "$CONF/settings.yml" 0644 root root
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
if [[ ! -s "$CONF/credentials/env" ]]; then
    [[ "$DRY_RUN" -eq 1 ]] || (umask 077; printf 'SEARXNG_SECRET=%s\n' "$(openssl rand -hex 32)" > "$WORK/env")
    place_file file "$WORK/env" "$CONF/credentials/env" 0600 root root
fi
[[ "$DRY_RUN" -eq 1 ]] || printf 'SEARXNG_IMAGE=%s\nSEARXNG_UID=%s\nSEARXNG_GID=%s\n' \
    "$SEARXNG_IMAGE" "$SX_UID" "$SX_GID" > "$WORK/image.env"
place_file file "$WORK/image.env" "$CONF/image.env" 0644 root root

place_file unit "$HERE/$UNIT" "/etc/systemd/system/$UNIT" 0644 root root
run systemctl daemon-reload
if [[ "$START" -eq 1 ]]; then run systemctl enable "$UNIT"; run systemctl restart "$UNIT"; fi

if [[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    n=0
    for _ in $(seq 1 60); do
        n="$(curl -s -m 15 "http://127.0.0.1:${PORT}/search?q=linux+kernel&format=json" 2>/dev/null \
             | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("results", [])))' 2>/dev/null || echo 0)"
        [[ "$n" -gt 0 ]] && break; sleep 2
    done
    [[ "$n" -gt 0 ]] || die "SearXNG returned no JSON results; see journalctl -u $UNIT"
    # 127.0.0.1 only; 172.30.0.1 is the gent module's bridge socket for Gents (internal network).
    extra="$(ss -ltnH "( sport = :$PORT )" | awk '{print $4}' | sort -u | grep -vxE "127\.0\.0\.1:$PORT|172\.30\.0\.1:$PORT" || true)"
    [[ -z "$extra" ]] || die "SearXNG is reachable beyond loopback and the Gent bridge: $extra"
    uids="$(docker top kent-searxng -o pid,uid,gid | awk 'NR > 1 { print $2 ":" $3 }' | sort -u | paste -sd,)"
    [[ "$uids" == "$SX_UID:$SX_GID" ]] || die "SearXNG processes run as $uids, expected kent-searxng ($SX_UID:$SX_GID)"
    log "OK: $UNIT ($SEARXNG_VERSION) on 127.0.0.1:${PORT}; JSON search returns $n results; runs as kent-searxng ($SX_UID)"
fi
audit_event "install completed"
log "done."
