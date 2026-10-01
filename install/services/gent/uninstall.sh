#!/usr/bin/env bash
# Kent — gent uninstall (manifest-driven). Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
# First destroys every Gent spawned by kent-spawn-gent (archived under
# /var/lib/kent-gent/archive unless --purge-state), then removes the firewall rules,
# Docker network and images this module recorded, then the generic manifest steps.
set -euo pipefail
SERVICE="gent"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"

pre_uninstall() {
    local mf sid rule net img; mf="$(manifest_file)"
    if [[ -d /var/lib/kent-gent/registry && -x /opt/kent-gent/bin/kent-destroy-gent ]]; then
        for f in /var/lib/kent-gent/registry/*; do
            [[ -f "$f" ]] || continue
            sid="$(basename "$f")"
            if [[ "${PURGE_STATE:-0}" -eq 1 ]]; then
                run env SUDO_USER=kent /opt/kent-gent/bin/kent-destroy-gent "$sid"
            else
                run env SUDO_USER=kent /opt/kent-gent/bin/kent-destroy-gent "$sid" --archive
            fi
        done
    fi
    # Stop the proxy now so its shared-memory segments can be removed before the
    # account goes away (they would otherwise linger in /dev/shm owned by a dead uid).
    run systemctl stop kent-squid.service 2>/dev/null || true
    id kent-squid &>/dev/null && run find /dev/shm -maxdepth 1 -user kent-squid -type f -delete
    while read -r rule; do
        [[ -n "$rule" ]] && run ufw delete $rule || true
    done < <(awk '$1 == "ufwrule" { $1 = ""; sub(/^ /, ""); print }' "$mf")
    while read -r net; do
        [[ -n "$net" ]] || continue
        docker network inspect "$net" >/dev/null 2>&1 && run docker network rm "$net" || true
    done < <(awk '$1 == "dockernet" { print $2 }' "$mf")
    while read -r img; do
        [[ -n "$img" ]] || continue
        docker images --format '{{.Repository}}:{{.Tag}}' "$img" | while read -r t; do run docker rmi "$t" || true; done
    done < <(awk '$1 == "dockerimage" { print $2 }' "$mf")
    while read -r base; do
        [[ -n "$base" ]] && docker image inspect "$base" >/dev/null 2>&1 && { run docker rmi "$base" || true; }
    done < <(awk '$1 == "dockerbase" { print $2 }' "$mf")
}

uninstall_main "$@"
