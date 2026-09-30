#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/llama/install.sh
# The local model server (llama.cpp llama-server) as a managed, on-demand service:
#   account  kent-llama:kent-llama (system, nologin, no sudo); group kent-models (model readers in
#            the hardened profile: the service through SupplementaryGroups, plus the installing operator)
#   code     /opt/kent-llama/{bin,lib} — a root-owned copy of an existing llama.cpp build (llama-server
#            and the libraries it links from its build directory; hashes in BUILD-INFO)
#   models   MODELS_DIR (default /media/administrator/DATA/models), by profile (models-perms.sh):
#            lab: files keep their owner, only write bits are removed; hardened: root:kent-models,
#            directory 0750, files 0440, originals recorded and restored on uninstall. Both: SHA-256
#            of the model files MODEL_SET selects in /etc/kent/llama/models.sha256 (another set is hashed
#            when selected with --set); the service refuses to load a file
#            whose hash does not match.
#   mount    srv-kent-models.mount: MODELS_DIR bound read-only at /srv/kent/models (ro,nodev,nosuid,noexec)
#   units    kent-llama.service (127.0.0.1:8080, hardened, not enabled at boot) and
#            kent-llama-tuning.service (root oneshot: SMT/boost off while the server runs, restored after)
#   polkit   60-kent-llama.rules: kent-operators may start/stop/restart kent-llama.service, nothing else
#   config   /etc/kent/llama/llama.env (settings; see kent_llama_launch.py)
#
# Usage: sudo ./install.sh [--from BUILD_BIN_DIR] [--models-dir DIR] [--profile lab|hardened]
#                          [--no-start] [--dry-run]
#        sudo ./install.sh --set KEY=VALUE      change one setting in llama.env
#        sudo ./install.sh --rehash [--profile lab|hardened]
#                                               re-record model hashes and re-apply the models mode
#                                               (after adding or replacing a model file; with
#                                               --profile, switch the models mode)
# --profile defaults to the recorded models mode, else /etc/kent/profile, else lab.
# =============================================================================
set -euo pipefail
SERVICE="llama"
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../lib-service.sh"
source "$HERE/models-perms.sh"

OPT=/opt/kent-llama; CONF="${KENT_LLAMA_CONF:-/etc/kent/llama}"; SUMS=$CONF/models.sha256; UNIT=kent-llama.service   # CONF: override only for tests
MOUNT_UNIT=srv-kent-models.mount; MNT=/srv/kent/models
RULE=/etc/polkit-1/rules.d/60-kent-llama.rules
FROM="${KENT_LLAMA_BUILD:-}"; MODELS_DIR=""; START=1; SET=""; REHASH_ONLY=0; PROFILE_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)       FROM="$2"; shift 2 ;;
        --models-dir) MODELS_DIR="$2"; shift 2 ;;
        --profile)    PROFILE_ARG="$2"; shift 2 ;;
        --no-start)   START=0; shift ;;
        --set)        SET="$2"; shift 2 ;;
        --rehash)     REHASH_ONLY=1; shift ;;
        --dry-run)    DRY_RUN=1; shift ;;
        *) die "unknown option: $1" ;;
    esac
done
export DRY_RUN; require_root

KEYS="MODEL_SET CTX UB NCMOE THINK ALIAS PIN CORES FORCE_256K TUNE"
set_value() {  # set_value KEY=VALUE — validated against the launcher before it is written
    local key="${1%%=*}" val="${1#*=}"
    [[ "$1" == *=* && " $KEYS " == *" $key "* ]] || die "--set takes KEY=VALUE with KEY one of: $KEYS"
    [[ "$val" =~ ^[A-Za-z0-9_.,-]*$ ]] || die "value for $key may contain only letters, digits and _.,-"
    [[ -f "$CONF/llama.env" ]] || die "not installed ($CONF/llama.env missing)"
    local tmp; tmp="$(mktemp)"
    awk -v k="$key" -v v="$val" -F= 'BEGIN { OFS = "=" } $1 == k { $0 = k "=" v; seen = 1 } { print } END { if (!seen) print k "=" v }' \
        "$CONF/llama.env" > "$tmp"
    # shellcheck disable=SC1090  # a candidate llama.env, validated by the launcher
    if ! (set -a; source "$tmp"; set +a; MODELDIR="$MNT" "$HERE/bin/kent_llama_launch.py" --print >/dev/null); then
        rm -f "$tmp"; die "rejected: $key=$val is not a valid setting (llama.env unchanged)"
    fi
    # A new model set is hashed before it is selected: a missing file leaves llama.env unchanged.
    if [[ "$key" == MODEL_SET ]]; then record_hashes "$tmp"; fi
    run install -m 0640 -o root -g kent-llama "$tmp" "$CONF/llama.env"; rm -f "$tmp"
    audit_event "setting $key=$val"
    log "set $key=$val. Takes effect at the next start: kent llama restart"
}
current_models_dir() { sed -n "s/^What=//p" "/etc/systemd/system/$MOUNT_UNIT" 2>/dev/null || true; }
[[ -n "$MODELS_DIR" ]] || MODELS_DIR="$(current_models_dir)"
[[ -n "$MODELS_DIR" ]] || MODELS_DIR=/media/administrator/DATA/models
MODELS_DIR="$(realpath -m "$MODELS_DIR")"
# Models mode (profile): lab leaves the operator's files as they are, hardened locks them down.
PREV_MODE="$(models_mode_recorded)"
MODE="$(resolve_models_mode "$PROFILE_ARG")"

# The model files the settings select (MODEL_SET), by name: the launcher decides, so the record
# and the start-time check (kent-llama-verify) always agree on which files matter.
selected_models() {  # selected_models [ENV_FILE] (default: the installed llama.env, else the shipped one)
    local env="${1:-$CONF/llama.env}"
    if [[ ! -e "$env" ]]; then env="$HERE/llama.env"     # first install: the shipped settings
    elif [[ ! -r "$env" ]]; then
        # A dry run without root (unshare -r) can reach the installed llama.env (directory 0751) but
        # not read it (0640): say so and use the shipped settings, rather than select nothing.
        [[ "$DRY_RUN" -eq 1 ]] || die "cannot read $env"
        warn "dry run: cannot read $env (not root); assuming the shipped settings (MODEL_SET=$(sed -n 's/^MODEL_SET=//p' "$HERE/llama.env"))"
        env="$HERE/llama.env"
    fi
    # shellcheck disable=SC1090  # llama.env: root-owned, or a candidate the launcher just validated
    (set -a; source "$env"; set +a; MODELDIR=/ "$HERE/bin/kent_llama_launch.py" --files) | xargs -r -n 1 basename
}

# SHA-256 of the selected model files only (not every GGUF in the directory: Kent loads one set),
# recorded root-owned. Another set is hashed when it is selected (--set MODEL_SET=...).
record_hashes() {  # record_hashes [ENV_FILE]
    local tmp n f name t0 gb; tmp="$(mktemp)"
    local -a names; mapfile -t names < <(selected_models "${1:-}")
    [[ ${#names[@]} -gt 0 ]] || die "the settings select no model files (check MODEL_SET)"
    for name in "${names[@]}"; do
        f="$MODELS_DIR/$name"
        [[ -f "$f" ]] || die "model file $name (selected by MODEL_SET) not found in $MODELS_DIR"
        gb="$(du -BG --apparent-size "$f" | cut -f1)"
        if [[ "$DRY_RUN" -eq 1 ]]; then log "dry run: would hash $name ($gb)"; continue; fi
        long_step "hashing $name ($gb) — reads the whole file once, up to a minute or two"
        t0=$SECONDS
        sha256sum "$f" | awk -v n="$name" '{ print $1 "  " n }' >> "$tmp"
        log "  hashed $name in $((SECONDS - t0)) s"
    done
    sort -k2 -o "$tmp" "$tmp"
    n="$(wc -l < "$tmp")"
    # Audit trail: what this rehash changed, per file, and who ran it. The previous record is kept
    # beside the new one (inside /etc/kent/llama, which the module claims, so uninstall removes it).
    claim_path file "$SUMS"   # before touching anything: refuses a record Kent did not create
    local line who="human:${SUDO_USER:-root}"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "dry run: no hashes computed, so no comparison with $SUMS"
    else
        while IFS= read -r line; do
            [[ -n "$line" ]] || continue
            audit_event "rehash by $who: $line"
            log "rehash by $who: $line"
        done < <(python3 "$HERE/bin/kent_llama_hashes.py" "$SUMS" "$tmp")
        if [[ -f "$SUMS" ]] && ! cmp -s "$SUMS" "$tmp"; then
            install -m 0644 -o root -g root "$SUMS" "$SUMS.$(date -u +%Y%m%dT%H%M%SZ)"
        fi
    fi
    run install -m 0644 -o root -g root "$tmp" "$SUMS"; rm -f "$tmp"
    audit_event "recorded SHA-256 of $n model files"
    if [[ "$DRY_RUN" -eq 1 ]]; then log "dry run: would record SHA-256 of the selected model files in $SUMS"
    else log "recorded SHA-256 of $n selected model files in $SUMS"; fi
}

# Where the models live, by filesystem (UUID + path inside it), so a failed start can name a moved
# or missing drive (kent-llama-source diagnose, run by `kent llama start`). A desktop automount
# (udisks, not in fstab) appears only after the operator logs in and can change name after an
# unclean shutdown (DATA -> DATA1): lab warns, hardened refuses it.
record_source() {
    local tmp; tmp="$(mktemp)"
    python3 "$HERE/bin/kent_llama_source.py" record "$MODELS_DIR" > "$tmp" || { rm -f "$tmp"; die "cannot tell which filesystem holds $MODELS_DIR"; }
    if grep -qx 'automount 1' "$tmp"; then
        local mp; mp="$(sed -n 's/^mountpoint //p' "$tmp")"
        [[ "$MODE" != hardened ]] || { rm -f "$tmp"; die "$MODELS_DIR is on a desktop automount ($mp): the hardened profile needs a system mount (an /etc/fstab entry)"; }
        warn "$MODELS_DIR is on a desktop automount ($mp): the model server can start only after you log in, and the mount point can change name after an unclean shutdown. An /etc/fstab entry for the drive avoids both."
    fi
    claim_path file "$CONF/models.source"
    run install -m 0644 -o root -g root "$tmp" "$CONF/models.source"; rm -f "$tmp"
}

# The operator reads the locked-down models through kent-models (hardened only; in lab the files
# stay theirs). The group itself always exists: the unit's SupplementaryGroups names it.
operator_reads_models() {
    local op; op="$(operator_user)"
    if [[ "$MODE" == hardened ]] && ! id -nG "$op" 2>/dev/null | tr ' ' '\n' | grep -qx kent-models; then
        run gpasswd -a "$op" kent-models >/dev/null
        manifest_add member "kent-models $op"
    fi
}

[[ -n "$SET" ]] && { set_value "$SET"; exit 0; }

if [[ "$REHASH_ONLY" -eq 1 ]]; then
    manifest_has modelsdir "$MODELS_DIR" || die "not installed (models directory not recorded)"
    record_source           # first: hardened refuses an automount before anything changes
    operator_reads_models   # a switch from lab to hardened (kent-admin profile hardened)
    apply_models_mode "$MODE" "$PREV_MODE"; record_hashes; exit 0
fi

audit_event "install started (${INVOCATION_ARGS})"
log "preflight"
if [[ -z "$FROM" ]]; then   # re-run: the recorded build if it still exists, else keep the installed copy
    FROM="$(sed -n 's/^source //p' "$OPT/BUILD-INFO" 2>/dev/null)"
    [[ -x "$FROM/llama-server" ]] || FROM="$OPT/bin"
fi
[[ -x "$FROM/llama-server" ]] || die "no llama.cpp build: pass --from <build>/bin (the directory holding llama-server)"
command -v nvidia-smi >/dev/null || die "NVIDIA driver not found (nvidia-smi)"
if ! port_free 8080 && ! systemctl is-active --quiet "$UNIT"; then
    die "port 8080 is in use by another process; stop your own llama-server first"
fi
[[ -d /etc/polkit-1/rules.d ]] || die "polkit rules directory missing (/etc/polkit-1/rules.d)"
[[ -d "$MODELS_DIR" ]] || die "models directory $MODELS_DIR not found (is the drive mounted?)"
if [[ "$MODE" == hardened ]] && python3 "$HERE/bin/kent_llama_source.py" record "$MODELS_DIR" 2>/dev/null | grep -qx 'automount 1'; then
    die "$MODELS_DIR is on a desktop automount: the hardened profile needs a system mount (an /etc/fstab entry)"
fi

ensure_service_account kent-llama /nonexistent "Kent local model server (llama.cpp)"
if ! getent group kent-models >/dev/null; then
    run groupadd --system kent-models
    manifest_add group kent-models
elif ! manifest_has group kent-models; then
    die "group 'kent-models' already exists and was not created by Kent; refusing to adopt it"
fi
operator_reads_models

# --- Code: a root-owned copy of the build (binary + the libraries it links from its build dir) ---
if [[ "$(readlink -f "$FROM")" != "$OPT/bin" ]]; then
    claim_path path "$OPT"
    ensure_dir "$OPT" 0755 root root
    ensure_dir "$OPT/bin" 0755 root root
    ensure_dir "$OPT/lib" 0755 root root
    ensure_dir "$OPT/libexec" 0755 root root
    WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
    libs="$(ldd "$FROM/llama-server" | awk -v d="$(readlink -f "$FROM")/" 'index($3, d) == 1 { print $3 }')"
    [[ -n "$libs" ]] || die "llama-server links no libraries from $FROM (unexpected build layout)"
    run install -m 0755 -o root -g root "$FROM/llama-server" "$OPT/bin/llama-server"
    { echo "source $(readlink -f "$FROM")"
      git -C "$FROM/../.." log -1 --format='commit %H %cd' 2>/dev/null || true
      git -C "$FROM/../.." remote get-url origin 2>/dev/null | sed 's/^/origin /' || true
      echo "installed $(date -Is)"
      sha256sum "$FROM/llama-server" | awk '{ print "sha256 " $1 "  bin/llama-server" }'
    } > "$WORK/BUILD-INFO"
    for l in $libs; do
        # Keep the soname the binary asks for (e.g. libggml.so.0), as a real file.
        run install -m 0644 -o root -g root "$l" "$OPT/lib/$(basename "$l")"
        sha256sum "$l" | awk -v n="$(basename "$l")" '{ print "sha256 " $1 "  lib/" n }' >> "$WORK/BUILD-INFO"
    done
    run install -m 0644 -o root -g root "$WORK/BUILD-INFO" "$OPT/BUILD-INFO"
fi
run install -m 0755 -o root -g root "$HERE/bin/kent_llama_launch.py" "$OPT/libexec/kent-llama-launch"
run install -m 0755 -o root -g root "$HERE/bin/kent_llama_hashes.py" "$OPT/libexec/kent-llama-hashes"
run install -m 0755 -o root -g root "$HERE/bin/kent_llama_source.py" "$OPT/libexec/kent-llama-source"
for s in kent-llama-verify kent-llama-wait kent-llama-tuning; do
    run install -m 0755 -o root -g root "$HERE/libexec/$s" "$OPT/libexec/$s"
done

# --- Config ---
claim_path path "$CONF"
# 0751: operators can read the records by name (models.sha256, models.source: not secret, so they
# can check the models themselves) but not list the directory; llama.env stays 0640.
ensure_dir "$CONF" 0751 root kent-llama
[[ -f "$CONF/llama.env" ]] || place_file file "$HERE/llama.env" "$CONF/llama.env" 0640 root kent-llama
# systemd creates the unit's CacheDirectory (CUDA kernel cache) on first start; record it so
# uninstall always removes it (a cache: `path`, not `state`).
manifest_add path /var/cache/kent-llama

# --- Models: apply the profile's mode, hash, read-only mount ---
apply_models_mode "$MODE" "$PREV_MODE"
record_source
record_hashes
ensure_dir /srv/kent 0755 root root
# While the read-only bind mount is up, the directory underneath cannot be re-permissioned (EROFS).
findmnt -rn "$MNT" >/dev/null || ensure_dir "$MNT" 0755 root root
WORK="${WORK:-$(mktemp -d)}"; trap 'rm -rf "$WORK"' EXIT
sed "s#@SRC@#$MODELS_DIR#g" "$HERE/systemd/$MOUNT_UNIT.in" > "$WORK/$MOUNT_UNIT"
if findmnt -rn "$MNT" >/dev/null && [[ "$(current_models_dir)" != "$MODELS_DIR" ]]; then
    run systemctl stop "$UNIT" "$MOUNT_UNIT"
fi
place_file unit "$WORK/$MOUNT_UNIT" "/etc/systemd/system/$MOUNT_UNIT" 0644 root root

# --- Units and the operators' start/stop permission ---
place_file unit "$HERE/systemd/kent-llama-tuning.service" /etc/systemd/system/kent-llama-tuning.service 0644 root root
place_file unit "$HERE/systemd/$UNIT" "/etc/systemd/system/$UNIT" 0644 root root
place_file file "$HERE/60-kent-llama.rules" "$RULE" 0644 root root
run systemctl daemon-reload

if [[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    long_step "starting $UNIT — checks the model hashes and loads the model, a minute or two"
    systemctl restart "$UNIT" || die "$UNIT failed to start; see journalctl -u $UNIT"
    opts="$(findmnt -rn -o OPTIONS "$MNT")"
    for o in ro nodev nosuid noexec; do [[ ",$opts," == *",$o,"* ]] || die "$MNT is not mounted $o ($opts)"; done
    alias="$(curl -s -m 10 http://127.0.0.1:8080/v1/models | jq -r '.data[0].id' 2>/dev/null)"
    [[ "$alias" == "$(sed -n 's/^ALIAS=//p' "$CONF/llama.env")" ]] || die "llama-server answers as '$alias', not the configured alias"
    extra="$(ss -ltnH '( sport = :8080 )' | awk '{print $4}' | sort -u | grep -vx '127.0.0.1:8080' || true)"
    [[ -z "$extra" ]] || die "llama-server is reachable beyond loopback: $extra"
    user="$(ps -o user= -p "$(systemctl show -p MainPID --value "$UNIT")")"
    [[ "$user" == kent-llama ]] || die "llama-server runs as $user, not kent-llama"
    log "OK: $UNIT serving '$alias' on 127.0.0.1:8080 as kent-llama; models read-only at $MNT ($opts)"
else
    log "OK: installed (not started); start with: kent llama start"
fi
audit_event "install completed"
log "done."
