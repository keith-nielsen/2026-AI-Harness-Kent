#!/usr/bin/env bash
# Kent — llama uninstall (manifest-driven). Stops the server and its mount. Hardened profile: puts
# every model file's original owner and mode back (recorded at install) before the kent-models group
# is removed; files added after install (not recorded) go back to the operator, 0444. Lab profile:
# the model files were never re-owned and are left as they are. The models are never deleted.
# Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="llama"
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../lib-service.sh"
source "$HERE/models-perms.sh"
pre_uninstall() {
    local mf op dir msg; mf="$(manifest_file)"
    # Guard, before any change: the model files must be reachable to be handed back. Otherwise the
    # kent-models group would be deleted below and leave them owned by a group that no longer exists.
    while read -r dir; do
        [[ -z "$dir" || -d "$dir" ]] && continue
        msg="models directory $dir is not available (drive not mounted?); mount it and re-run"
        [[ "$DRY_RUN" -eq 1 ]] && { warn "$msg (a real run would stop here)"; continue; }
        die "$msg. Refusing to continue: removing the kent-models group now would leave the model files owned by a group that no longer exists"
    done < <(awk '$1 == "modelsdir" { $1 = ""; sub(/^ /, ""); print }' "$mf")
    run systemctl stop kent-llama.service srv-kent-models.mount 2>/dev/null || true
    op="$(awk '$1 == "member" && $2 == "kent-models" { print $3; exit }' "$mf")"
    # hardened: put back what install recorded; lab: the files were never re-owned, leave them.
    if [[ "$(models_mode_recorded)" == hardened ]]; then
        restore_models_hardened "$op"
    else
        log "lab profile: model files left as they are (owner never changed)"
    fi
}
uninstall_main "$@"
