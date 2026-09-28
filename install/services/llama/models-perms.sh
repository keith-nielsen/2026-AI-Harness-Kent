# shellcheck shell=bash
# =============================================================================
# Kent — install/services/llama/models-perms.sh (sourced by the llama install.sh / uninstall.sh)
# How the model directory is treated, by profile ("models mode", recorded in the manifest):
#   lab       the operator keeps their files: no change of owner, the directory is left alone,
#             only write bits are removed from the files (a-w). kent-llama reads them as "other",
#             so they must be world-readable (warned about, never changed silently).
#   hardened  root:kent-models, directory 0750, files 0440; every original owner/mode is recorded
#             and restored on uninstall (or when switching back to lab).
# Both: SHA-256 record checked before every load, read-only bind mount at /srv/kent/models.
# Needs lib-service.sh (SERVICE=llama) loaded, and MODELS_DIR set where a directory is worked on.
# =============================================================================
PERMS_BACKUP="${KENT_PERMS_BACKUP:-$INSTALL_STATE/backup/llama/models-perms}"   # override only for tests
PROFILE_FILE="${KENT_PROFILE_FILE:-/etc/kent/profile}"                          # override only for tests

# The recorded mode. Installs from before profiles (v0.1.0-rc.4) always locked the directory down:
# a recorded models directory without a mode line means hardened.
models_mode_recorded() {
    local mf m=""; mf="$(manifest_file)"
    if [[ -f "$mf" ]]; then
        m="$(awk '$1 == "modelsmode" { m = $2 } END { print m }' "$mf")"
        if [[ -z "$m" ]] && grep -q '^modelsdir ' "$mf"; then m=hardened; fi
    fi
    echo "$m"
}

# Mode to apply: explicit --profile, else what was recorded, else Kent's profile, else lab.
resolve_models_mode() {  # resolve_models_mode [explicit]
    local m="${1:-}"
    [[ -n "$m" ]] || m="$(models_mode_recorded)"
    if [[ -z "$m" && -s "$PROFILE_FILE" ]]; then m="$(tr -d '[:space:]' < "$PROFILE_FILE")"; fi
    [[ -n "$m" ]] || m=lab
    [[ "$m" == lab || "$m" == hardened ]] || die "profile must be lab or hardened, got '$m'"
    echo "$m"
}

record_models_mode() {  # record_models_mode <mode> — one modelsmode line in the manifest
    local mode="$1" mf; mf="$(manifest_file)"
    if [[ -f "$mf" ]] && grep -qx "modelsmode $mode" "$mf"; then return 0; fi
    if [[ "$DRY_RUN" -eq 0 && -f "$mf" ]]; then
        grep -v '^modelsmode ' "$mf" > "$mf.tmp" || true; mv "$mf.tmp" "$mf"
    fi
    manifest_add modelsmode "$mode"
}

model_files() {  # regular files directly in the directory (no symlinks, no subdirectories)
    local f
    for f in "$1"/*; do [[ -f "$f" && ! -L "$f" ]] && printf '%s\n' "$f"; done
    return 0
}

# lab: keep the operator's ownership; only take write permission away from the files.
secure_models_lab() {
    local f m dm bad=0
    while IFS= read -r f; do
        m="$(stat -c %a "$f")"
        if (( 8#$m & 8#222 )); then run chmod a-w "$f"; fi
        if ! (( 8#$m & 8#004 )); then
            warn "$f is not world-readable (mode $m): kent-llama cannot load it in the lab profile." \
                 "Make it readable (chmod a+r) or use --profile hardened."
            bad=$((bad + 1))
        fi
    done < <(model_files "$MODELS_DIR")
    dm="$(stat -c %a "$MODELS_DIR")"
    if ! (( 8#$dm & 8#001 )); then
        warn "$MODELS_DIR is not searchable by others (mode $dm): kent-llama cannot reach the models in the lab profile."
        bad=$((bad + 1))
    fi
    if (( 8#$dm & 8#002 )); then warn "$MODELS_DIR is world-writable (mode $dm): anyone can replace a model (the hash check refuses changed files)."; fi
    log "lab profile: model files keep their owner; write bits removed$( ((bad)) && echo " ($bad warning(s))")"
}

# hardened: record each file's original owner/mode once (restored on uninstall), then lock down.
secure_models_hardened() {
    local f
    if [[ "$DRY_RUN" -eq 0 ]]; then
        install -d -m 0700 "$(dirname "$PERMS_BACKUP")"
        touch "$PERMS_BACKUP"; chmod 0600 "$PERMS_BACKUP"
        grep -q "^dir " "$PERMS_BACKUP" || stat -c 'dir %a %U %G %n' "$MODELS_DIR" >> "$PERMS_BACKUP"
        while IFS= read -r f; do
            grep -qF " $f" "$PERMS_BACKUP" || stat -c 'file %a %U %G %n' "$f" >> "$PERMS_BACKUP"
        done < <(model_files "$MODELS_DIR")
    fi
    run chown root:kent-models "$MODELS_DIR"; run chmod 0750 "$MODELS_DIR"
    while IFS= read -r f; do
        run chown root:kent-models "$f"; run chmod 0440 "$f"
    done < <(model_files "$MODELS_DIR")
    log "hardened profile: models root:kent-models, directory 0750, files 0440"
}

# Undo the hardened lockdown for every recorded models directory: recorded files get their
# original owner/mode back; files added since (not recorded) go to the operator, 0444.
restore_models_hardened() {  # restore_models_hardened [operator]
    local mf dir f kind mode owner group path op="${1:-}"; mf="$(manifest_file)"
    [[ -n "$op" ]] || op="$(awk '$1 == "member" && $2 == "kent-models" { print $3; exit }' "$mf" 2>/dev/null || true)"
    while read -r dir; do
        [[ -n "$dir" && -d "$dir" ]] || continue
        while IFS= read -r f; do
            grep -qF " $f" "$PERMS_BACKUP" 2>/dev/null && continue
            [[ -n "$op" ]] && run chown "$op:$op" "$f"
            run chmod 0444 "$f"
        done < <(model_files "$dir")
    done < <(awk '$1 == "modelsdir" { $1 = ""; sub(/^ /, ""); print }' "$mf" 2>/dev/null)
    [[ -f "$PERMS_BACKUP" ]] || return 0
    while read -r kind mode owner group path; do
        [[ -n "$kind" && -e "$path" ]] || continue
        run chown "$owner:$group" "$path"; run chmod "$mode" "$path"
    done < "$PERMS_BACKUP"
    [[ "$DRY_RUN" -eq 1 ]] || rm -f "$PERMS_BACKUP"
    log "restored the original owner/mode of the model directory and files"
}

# Apply <mode> to MODELS_DIR, switching cleanly from <previous> (hardened -> lab restores first).
apply_models_mode() {  # apply_models_mode <mode> [previous]
    local mode="$1" prev="${2:-}"
    [[ -d "$MODELS_DIR" ]] || die "models directory $MODELS_DIR not found (is the drive mounted?)"
    manifest_add modelsdir "$MODELS_DIR"
    if [[ -n "$prev" && "$prev" != "$mode" ]]; then
        log "models: switching from the $prev to the $mode profile"
        audit_event "models profile $prev -> $mode"
    fi
    case "$mode" in
        lab)
            [[ "$prev" == hardened ]] && restore_models_hardened
            secure_models_lab ;;
        hardened) secure_models_hardened ;;
        *) die "unknown models mode '$mode'" ;;
    esac
    record_models_mode "$mode"
}
