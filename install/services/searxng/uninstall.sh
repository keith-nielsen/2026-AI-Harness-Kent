#!/usr/bin/env bash
# Kent — searxng uninstall (manifest-driven; also removes the image if Kent pulled it).
# Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="searxng"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
pre_uninstall() {
    local mf img; mf="$(manifest_file)"
    run systemctl stop kent-searxng.service 2>/dev/null || true
    while read -r img; do
        [[ -n "$img" ]] && docker image inspect "$img" >/dev/null 2>&1 && { run docker rmi "$img" || true; }
    done < <(awk '$1 == "dockerbase" { print $2 }' "$mf")
}
uninstall_main "$@"
