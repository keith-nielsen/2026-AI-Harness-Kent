#!/usr/bin/env bash
# Kent — llama uninstall (manifest-driven). Stops the server and its mount, then puts every model
# file's original owner and mode back (recorded at install) before the kent-models group is removed.
# Files added after install (not recorded) go back to the operator, 0444. The models are never deleted.
# Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="llama"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
pre_uninstall() {
    local mf bak dir f kind mode owner group path op; mf="$(manifest_file)"
    bak="$INSTALL_STATE/backup/llama/models-perms"
    run systemctl stop kent-llama.service srv-kent-models.mount 2>/dev/null || true
    op="$(awk '$1 == "member" && $2 == "kent-models" { print $3; exit }' "$mf")"
    while read -r dir; do
        [[ -n "$dir" && -d "$dir" ]] || continue
        for f in "$dir"/*; do   # unrecorded (added later) files first; recorded ones are overwritten below
            [[ -f "$f" && ! -L "$f" ]] || continue
            grep -qF " $f" "$bak" 2>/dev/null && continue
            [[ -n "$op" ]] && run chown "$op:$op" "$f"; run chmod 0444 "$f"
        done
    done < <(awk '$1 == "modelsdir" { $1 = ""; sub(/^ /, ""); print }' "$mf")
    [[ -f "$bak" ]] || return 0
    while read -r kind mode owner group path; do
        [[ -e "$path" ]] || continue
        run chown "$owner:$group" "$path"; run chmod "$mode" "$path"
    done < "$bak"
    [[ "$DRY_RUN" -eq 1 ]] || rm -f "$bak"
    log "restored the original owner/mode of the model directory and files"
}
uninstall_main "$@"
