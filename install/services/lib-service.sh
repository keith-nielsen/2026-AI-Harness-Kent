#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/lib-service.sh
# Shared helpers for per-service install/uninstall modules.
#
# Conventions (see install/services/README.md):
#   - Each service runs under its own system account, never a shared one.
#   - Everything a module creates is recorded in a manifest; uninstall removes
#     only what the manifest lists. Pre-existing accounts/units/paths that Kent
#     did not create are never adopted, modified, or deleted.
#   - --dry-run prints every privileged action instead of running it.
# =============================================================================
set -euo pipefail
# Traced runs (uninstall.sh --trace): bash running as root does not import PS4 from the
# environment, so set the timestamped script:line prefix here.
if [[ -o xtrace ]]; then PS4='+ $(date +%H:%M:%S.%3N) ${BASH_SOURCE[0]##*/}:${LINENO}: '; fi
# The module's own command-line arguments, captured here because modules source this file
# before their option loop shifts them away (a sourced file sees its caller's "$@").
INVOCATION_ARGS="$*"

# Installer bookkeeping lives apart from Kent's own home (/var/lib/kent belongs to the kent
# account), like dpkg's /var/lib/dpkg: manifests and moved-aside originals, root-only.
INSTALL_STATE="/var/lib/kent-install"
MANIFEST_DIR="${KENT_MANIFEST_DIR:-$INSTALL_STATE/manifest}"   # override only for tests

# One-time migration from v0.1, which kept these under /var/lib/kent.
if [[ $EUID -eq 0 && "${DRY_RUN:-0}" -eq 0 && -z "${KENT_MANIFEST_DIR:-}" \
      && -d /var/lib/kent/manifest && ! -d "$INSTALL_STATE/manifest" ]]; then
    install -d -m 0755 -o root -g root "$INSTALL_STATE"
    mv /var/lib/kent/manifest "$INSTALL_STATE/manifest"
    [[ -d /var/lib/kent/backup ]] && mv /var/lib/kent/backup "$INSTALL_STATE/backup"
    sed -i 's#/var/lib/kent/backup#/var/lib/kent-install/backup#g' "$INSTALL_STATE"/manifest/*
    logger -t kent-install -- "migrated installer bookkeeping to $INSTALL_STATE" 2>/dev/null || true
fi
DRY_RUN="${DRY_RUN:-0}"

log()  { echo "[$(date '+%H:%M:%S')] ${SERVICE:-kent}: $*"; }
# Record a privileged change in the system journal (tag kent-install); Alloy
# ships it to Loki and kent-audit-ingest appends it to the HMAC audit chain.
audit_event() { [[ "${DRY_RUN:-0}" -eq 1 ]] || logger -t kent-install -- "${SERVICE:-kent}: $*" 2>/dev/null || true; }
warn() { log "WARN: $*" >&2; }
die()  { log "ERROR: $*" >&2; exit 1; }
# Announce a slow step before it starts: install.sh prints NOTE lines in full on their own line
# (above its progress line), so the expectation is never cut off or wrapped.
long_step() { log "NOTE: $*"; }   # long_step <what> — <how long>

# Run a privileged action, or print it under --dry-run.
run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '  [dry-run]'; printf ' %q' "$@"; printf '\n'
    else
        "$@"
    fi
}

require_root() {
    [[ "$DRY_RUN" -eq 1 ]] && return 0
    [[ $EUID -eq 0 ]] || die "must run as root (sudo); use --dry-run to preview"
}

# The human operator who invoked sudo; Kent-side client config goes to them.
operator_user() {
    local u="${KENT_OPERATOR:-${SUDO_USER:-}}"
    [[ -n "$u" && "$u" != "root" ]] || die "cannot determine operator; run via sudo or set KENT_OPERATOR"
    echo "$u"
}

# --- Manifest ---------------------------------------------------------------
manifest_file() { echo "${MANIFEST_DIR}/${SERVICE:?SERVICE not set}"; }

manifest_has() {  # manifest_has <kind> <value>
    local f; f="$(manifest_file)"
    [[ -f "$f" ]] && grep -qxF "$1 $2" "$f"
}

manifest_add() {  # manifest_add <kind> <value>
    manifest_has "$1" "$2" && return 0
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] manifest += $1 $2"
        return 0
    fi
    install -d -m 0755 "$MANIFEST_DIR"
    echo "$1 $2" >> "$(manifest_file)"
}

# --- Accounts ---------------------------------------------------------------
# Create a dedicated system account + group, or reuse one only if this module
# created it earlier (per the manifest). Refuses to adopt anything else.
ensure_service_account() {  # ensure_service_account <name> <home> <comment>
    local name="$1" home="$2" comment="$3"
    if id "$name" &>/dev/null; then
        manifest_has user "$name" || die "account '$name' already exists and was not created by Kent; refusing to adopt it"
        log "account '$name' already present (created by Kent)"
        return 0
    fi
    if getent group "$name" &>/dev/null && ! manifest_has group "$name"; then
        die "group '$name' already exists and was not created by Kent; refusing to adopt it"
    fi
    run useradd --system --user-group --no-create-home \
        --home-dir "$home" --shell /usr/sbin/nologin --comment "$comment" "$name"
    manifest_add user "$name"
    manifest_add group "$name"
    log "created system account ${name}:${name}"
}

# Remove a file this module created in an earlier version and no longer installs, and drop
# it from the manifest (upgrade clean-up). Files Kent did not create are never touched.
retire_file() {  # retire_file <path>
    local path="$1" mf; mf="$(manifest_file)"
    manifest_has file "$path" || return 0
    run rm -f "$path"
    if [[ "$DRY_RUN" -eq 1 ]]; then echo "  [dry-run] manifest -= file $path"
    else grep -vxF "file $path" "$mf" > "$mf.tmp" || true; mv "$mf.tmp" "$mf"; fi
    log "retired $path (no longer installed by this module)"
}

# Refuse to overwrite a path or unit that Kent did not create.
DRY_MOVED=" "   # paths a --dry-run move_aside would have moved away
claim_path() {  # claim_path <kind> <path>
    local kind="$1" path="$2"
    if [[ -e "$path" ]] && ! manifest_has "$kind" "$path" && [[ "$DRY_MOVED" != *" $path "* ]]; then
        die "'$path' already exists and was not created by Kent; refusing to overwrite it"
    fi
    manifest_add "$kind" "$path"
}

# Create a directory, recording it and every missing ancestor in the manifest so
# uninstall can remove exactly what was created (empty directories only).
# Ancestors are created 0755 and owned by <ancestor_owner> (default root).
ensure_dir() {  # ensure_dir <path> <mode> <owner> <group> [ancestor_owner]
    local path="$1" mode="$2" owner="$3" group="$4" anc_owner="${5:-root}"
    local missing=() p="$path"
    while [[ ! -e "$p" ]]; do missing=("$p" "${missing[@]}"); p="$(dirname "$p")"; done
    local d
    for d in "${missing[@]}"; do
        if [[ "$d" == "$path" ]]; then
            run install -d -m "$mode" -o "$owner" -g "$group" "$d"
        else
            run install -d -m 0755 -o "$anc_owner" -g "$anc_owner" "$d"
        fi
        manifest_add dir "$d"
    done
    # Existing directory: enforce mode/ownership only if Kent created it; a
    # pre-existing (e.g. vendor) directory is used as-is, never re-permissioned.
    if [[ ${#missing[@]} -eq 0 ]] && manifest_has dir "$path"; then
        run install -d -m "$mode" -o "$owner" -g "$group" "$path"
    fi
    return 0
}

port_free() {  # port_free <port>
    ! ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q .
}

# Install one file Kent owns (claimed in the manifest as <kind>: file|unit|dropin).
place_file() {  # place_file <kind> <src> <dest> <mode> <owner> <group>
    local kind="$1" src="$2" dest="$3" mode="$4" owner="$5" group="$6"
    claim_path "$kind" "$dest"
    run install -m "$mode" -o "$owner" -g "$group" "$src" "$dest"
}

# --- Verified downloads (and the download cache) --------------------------
# Every pinned download is kept in KENT_CACHE, so reinstalls do not fetch it again. A cached
# file is checked against the same pinned SHA-256 as a fresh download, so the cache changes
# nothing about what gets installed. The cache belongs to the installer, not to a module: a plain
# uninstall keeps it, `uninstall.sh --purge` removes it, and it can be deleted at any time to
# reclaim space (sudo rm -rf /var/cache/kent-install). KENT_NO_CACHE=1 (install.sh --no-cache)
# neither reads nor writes it.
KENT_CACHE="${KENT_CACHE:-/var/cache/kent-install}"
cache_enabled() { [[ "${KENT_NO_CACHE:-0}" != 1 && "$DRY_RUN" -eq 0 ]]; }
cache_dir() {  # cache_dir <sub>  -> prints the directory (created root-only on first use)
    install -d -m 0700 "$KENT_CACHE" "$KENT_CACHE/$1"   # root-owned when run as root (installers are)
    echo "$KENT_CACHE/$1"
}

# Download to a temp file and verify against the pinned SHA-256 from versions.env.
fetch_verified() {  # fetch_verified <url> <sha256> <dest>
    local url="$1" sha="$2" dest="$3" cached=""
    if [[ "$DRY_RUN" -eq 1 ]]; then
        if [[ "${KENT_NO_CACHE:-0}" != 1 && -f "$KENT_CACHE/downloads/${sha}-$(basename "$url")" ]]; then
            echo "  [dry-run] use cached $(basename "$url") -> $dest (verify sha256 ${sha:0:16}…)"
        else echo "  [dry-run] download $url -> $dest (verify sha256 ${sha:0:16}…)"; fi
        return 0
    fi
    cache_enabled && cached="$(cache_dir downloads)/${sha}-$(basename "$url")"
    if [[ -n "$cached" && -f "$cached" ]]; then
        if [[ "$(sha256sum "$cached" | cut -d' ' -f1)" == "$sha" ]]; then
            install -m 0644 "$cached" "$dest"
            log "verified $(basename "$url") from the download cache (sha256 ${sha:0:16}…)"
            return 0
        fi
        warn "cached $(basename "$url") does not match its pinned SHA-256; removing it and downloading again"
        rm -f "$cached"
    fi
    local tmp; tmp="$(mktemp)"
    long_step "downloading $(basename "$url")$(size_hint "$url") — depends on the connection"
    curl -fsSL --retry 3 -o "$tmp" "$url" || { rm -f "$tmp"; die "download failed: $url"; }
    local got; got="$(sha256sum "$tmp" | cut -d' ' -f1)"
    if [[ "$got" != "$sha" ]]; then
        rm -f "$tmp"
        die "SHA-256 mismatch for $url (expected $sha, got $got); refusing to install"
    fi
    [[ -z "$cached" ]] || install -m 0644 "$tmp" "$cached"
    mv "$tmp" "$dest"
    log "verified $(basename "$url") (sha256 ${sha:0:16}…)"
}
# " (123 MB)" from the server's Content-Length, or nothing; only sets expectations.
size_hint() {
    local n; n="$(curl -sfIL -m 5 "$1" 2>/dev/null | tr -d '\r' | awk 'tolower($1) == "content-length:" { n = $2 } END { print n }')"
    [[ "$n" =~ ^[0-9]+$ && "$n" -gt 0 ]] && echo " ($(( n / 1000000 )) MB)"
    return 0
}

# --- Packages ---------------------------------------------------------------
pkg_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }

# Install a distro/vendor package if absent. Recorded only when Kent installed
# it, so uninstall never removes a package that was already there.
ensure_package() {  # ensure_package <name>
    local name="$1"
    if pkg_installed "$name"; then
        manifest_has package "$name" && log "package $name present (installed by Kent)" \
                                      || log "package $name already present (pre-existing; not recorded)"
        return 0
    fi
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$name"
    manifest_add package "$name"
}

# Dependency on an earlier module: fatal normally; under --dry-run the earlier module changed
# nothing, so the check can only warn (install.sh --dry-run runs every module in sequence).
need() {  # need <condition> <message>
    eval "$1" && return 0
    [[ "$DRY_RUN" -eq 1 ]] && { warn "dry run: $2 (expected: the earlier module was only simulated)"; return 0; }
    die "$2"
}

# A vendor .deb pinned in versions.env: fetched (or taken from the download cache), checked against
# its pinned SHA-256 and installed from the file, so no apt source is needed and system updates
# cannot move it. A package Kent installed earlier is moved to the pinned version (up or down).
ensure_pinned_deb() {  # ensure_pinned_deb <package> <version> <url> <sha256>
    local name="$1" ver="$2" url="$3" sha="$4" have
    have="$(dpkg-query -W -f='${Version}' "$name" 2>/dev/null || true)"
    if pkg_installed "$name" && [[ "$have" == "$ver" ]]; then
        log "package $name $ver present (pinned version)"; return 0
    fi
    local d; d="$(mktemp -d)"; chmod 0755 "$d"     # readable by apt's _apt user
    fetch_verified "$url" "$sha" "$d/$(basename "$url")"
    [[ "$DRY_RUN" -eq 1 ]] || chmod 0644 "$d/$(basename "$url")"
    log "installing $name $ver${have:+ (replacing $have)}"
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --allow-downgrades \
        "$d/$(basename "$url")"
    rm -rf "$d"
    manifest_add package "$name"
}

# Installs before the pins (2026-09-29) added the Grafana apt repository; with pinned packages Kent
# no longer needs it. Removes it only where Kent's manifest says Kent added it.
GRAFANA_APT_LIST=/etc/apt/sources.list.d/grafana.list
GRAFANA_APT_KEY=/etc/apt/keyrings/grafana.asc
retire_grafana_repo() {
    manifest_has file "$GRAFANA_APT_LIST" || return 0
    retire_file "$GRAFANA_APT_LIST"; retire_file "$GRAFANA_APT_KEY"
    [[ "$DRY_RUN" -eq 1 ]] || rm -f /var/lib/apt/lists/apt.grafana.com_*
    log "removed the Grafana apt repository Kent had added (packages are pinned now)"
}

# Record a pre-existing unit's enablement/activity once, so uninstall can put
# it back exactly (used for vendor services Kent configures but doesn't own).
record_unit_state() {  # record_unit_state <unit>
    local unit="$1"
    grep -q "^unitstate ${unit} " "$(manifest_file)" 2>/dev/null && return 0
    local en act
    en="$(systemctl is-enabled "$unit" 2>/dev/null || true)"; en="${en:-unknown}"
    act="$(systemctl is-active "$unit" 2>/dev/null || true)"; act="${act:-unknown}"
    manifest_add unitstate "${unit} ${en} ${act}"
}

# Move a pre-existing path aside (e.g. vendor data that Kent must start fresh
# from) so uninstall can put it back byte-for-byte. Recorded as "moved <orig> <bak>".
BACKUP_ROOT="$INSTALL_STATE/backup"
move_aside() {  # move_aside <path>   (no-op if absent or already moved by Kent)
    local orig="$1" bak="${BACKUP_ROOT}/${SERVICE}${1}"
    grep -q "^moved ${orig} " "$(manifest_file)" 2>/dev/null && return 0
    [[ -e "$orig" ]] || return 0
    ensure_dir "$(dirname "$bak")" 0700 root root
    run mv "$orig" "$bak"
    [[ "$DRY_RUN" -eq 1 ]] && DRY_MOVED+="$orig "
    manifest_add moved "${orig} ${bak}"
    log "moved pre-existing $orig aside -> $bak (restored on uninstall)"
}

# Take a unit down for removal: disable+stop only if it is actually enabled; a static, indirect,
# disabled or generated unit is just stopped (disabling it is a no-op that makes systemctl print a
# long "no installation config" explanation). Extra args select the scope, e.g. --user -M op@.
unit_off() {  # unit_off <unit> [systemctl scope args...]
    local u="$1" st; shift
    st="$(systemctl "$@" is-enabled "$u" 2>/dev/null || true)"
    case "$st" in
        enabled|enabled-runtime|linked|linked-runtime|alias) run systemctl "$@" disable --now "$u" ;;
        *) run systemctl "$@" stop "$u" ;;
    esac
}

# --- Generic uninstall --------------------------------------------------------
# Removes exactly what the module's manifest lists, in dependency order:
# units (stop/disable) → drop-ins → files → paths → state (only with purge) →
# packages Kent installed → restore recorded vendor unit states → restore
# moved-aside paths → dirs (if empty) → accounts → manifest. Extra module-specific steps can run before via
# a function named pre_uninstall, if defined.
uninstall_module() {
    local purge="${PURGE_STATE:-0}"
    local mf; mf="$(manifest_file)"
    [[ -f "$mf" ]] || die "no manifest at $mf; nothing recorded as installed by Kent"
    entries() { awk -v k="$1" '$1 == k { $1 = ""; sub(/^ /, ""); print }' "$mf"; }

    declare -F pre_uninstall >/dev/null && pre_uninstall

    local p u g op h
    # User-level units (systemd --user of the operator): stop, disable, remove.
    while read -r op p; do
        [[ -n "$p" ]] || continue
        case "$p" in *.timer|*.service)
            unit_off "$(basename "$p")" --user -M "${op}@" 2>/dev/null || true ;; esac
        run rm -f "$p"
        # Persistent=true timers leave a last-run record behind; remove it with the unit.
        if [[ "$p" == *.timer ]]; then
            h="$(getent passwd "$op" | cut -d: -f6 || true)"
            [[ -z "$h" ]] || run rm -f "$h/.local/share/systemd/timers/stamp-$(basename "$p")"
        fi
    done < <(entries userunit)
    [[ -n "$(entries userunit)" ]] && { run systemctl --user -M "$(entries userunit | head -1 | cut -d' ' -f1)@" daemon-reload || true; }

    while read -r p; do
        [[ -n "$p" ]] || continue
        u="$(basename "$p")"
        systemctl list-unit-files "$u" &>/dev/null && { unit_off "$u" || true; }
        run rm -f "$p"
        if [[ "$u" == *.timer ]]; then run rm -f "/var/lib/systemd/timers/stamp-$u"; fi
    done < <(entries unit)
    while read -r p; do [[ -n "$p" ]] && run rm -f "$p"; done < <(entries dropin)
    run systemctl daemon-reload

    while read -r p; do [[ -n "$p" ]] && run rm -f "$p"; done < <(entries file)
    while read -r p; do [[ -n "$p" ]] && run rm -rf --one-file-system "$p"; done < <(entries path)

    if [[ "$purge" -eq 1 ]]; then
        while read -r p; do [[ -n "$p" ]] && run rm -rf --one-file-system "$p"; done < <(entries state)
    else
        entries state | sed 's/^/  kept state: /'
    fi

    # Vendor units Kent enabled (e.g. a package's unit): disable before purging the
    # package, which would otherwise leave a dangling *.wants/ symlink behind.
    while read -r u; do
        [[ -n "$u" ]] && { run systemctl disable --now "$u" 2>/dev/null || true; }
    done < <(entries enabled)

    local pkgs; pkgs="$(entries package | tr '\n' ' ')"
    [[ -n "${pkgs// }" ]] && run env DEBIAN_FRONTEND=noninteractive apt-get purge -y $pkgs

    while read -r u en act; do
        [[ -n "$u" ]] || continue
        case "$en" in enabled) run systemctl enable "$u" ;; disabled) run systemctl disable "$u" ;; esac
        case "$act" in active) run systemctl restart "$u" ;; *) run systemctl stop "$u" || true ;; esac
    done < <(entries unitstate)

    # Put back anything Kent moved aside (after its units are stopped above).
    local orig bak
    while read -r orig bak; do
        [[ -n "$orig" && -e "$bak" ]] || continue
        run rm -rf --one-file-system "$orig"
        run mv "$bak" "$orig"
    done < <(entries moved)

    while read -r d; do
        [[ -n "$d" && -d "$d" ]] && run rmdir --ignore-fail-on-non-empty "$d"
    done < <(entries dir | awk '{ print length, $0 }' | sort -rn | cut -d' ' -f2-)

    # Group memberships Kent added to existing (human) accounts: "member <group> <user>".
    while read -r g u; do
        [[ -n "$u" ]] && id -nG "$u" 2>/dev/null | tr ' ' '\n' | grep -qx "$g" && { run gpasswd -d "$u" "$g" || true; }
    done < <(entries member)
    while read -r u; do [[ -n "$u" ]] && id "$u" &>/dev/null && run userdel "$u"; done < <(entries user)
    while read -r g; do [[ -n "$g" ]] && getent group "$g" &>/dev/null && run groupdel "$g"; done < <(entries group)

    if [[ "$purge" -eq 1 ]]; then
        run rm -f "$mf"
        run rmdir --ignore-fail-on-non-empty "$MANIFEST_DIR" "$(dirname "$MANIFEST_DIR")"
        log "uninstalled; manifest removed."; audit_event "uninstalled (purge-state)"
    else
        log "uninstalled; manifest kept (it still lists the preserved state)."; audit_event "uninstalled (state kept)"
    fi
}

# Standard option parsing for uninstall.sh wrappers.
uninstall_main() {
    PURGE_STATE=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --purge-state) PURGE_STATE=1; shift ;;
            --dry-run)     DRY_RUN=1; shift ;;
            *)             die "unknown option: $1" ;;
        esac
    done
    export DRY_RUN PURGE_STATE
    require_root
    uninstall_module
}
