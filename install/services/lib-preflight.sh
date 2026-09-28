#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/lib-preflight.sh
# Pre-flight check for uninstall.sh: before anything is removed, look for the conditions that
# would make a module fail halfway and leave a half-removed system. Sourced by uninstall.sh
# (and by tests, with KENT_MANIFEST_DIR pointing at fake manifests).
#
#   preflight_check      print one plain-language line per problem (with what to do);
#                        returns the number of problems (0 = ready)
#
# Checks, all driven by what the manifests record:
#   - every manifest line has a kind the uninstaller knows (else the manifest is damaged)
#   - no process runs as an account Kent created outside Kent's own units (an open `kent` chat,
#     a login session): the uninstall stops its own services, but userdel would fail on these
#   - Docker is running if a module recorded Docker objects or runs a container unit
#   - the dpkg/apt lock is free if a module will purge packages
#   - the model directory the llama module re-owned is present (drive mounted)
# =============================================================================

PREFLIGHT_MANIFEST_DIR="${KENT_MANIFEST_DIR:-/var/lib/kent-install/manifest}"
PREFLIGHT_DPKG_LOCKS="${KENT_DPKG_LOCKS:-/var/lib/dpkg/lock-frontend /var/lib/dpkg/lock}"
PREFLIGHT_FUSER="${KENT_FUSER:-fuser}"                 # overridden only by tests
PREFLIGHT_PS="${KENT_PS:-ps}"                          # overridden only by tests
PREFLIGHT_DOCKER="${KENT_DOCKER:-docker}"              # overridden only by tests
# Every kind lib-service.sh's uninstall_module or a module's pre_uninstall understands.
PREFLIGHT_KINDS="user group path dir file unit dropin state package enabled unitstate moved member userunit modelsdir modelsmode dockernet dockerimage dockerbase ufwrule"

_pf_entries() {  # _pf_entries <kind>: values of that kind across all manifests
    awk -v k="$1" '$1 == k { $1 = ""; sub(/^ /, ""); print }' "$PREFLIGHT_MANIFEST_DIR"/* 2>/dev/null
}

_pf_dpkg_busy() {  # is a package manager holding the dpkg lock?
    local f
    if command -v "$PREFLIGHT_FUSER" >/dev/null 2>&1; then
        for f in $PREFLIGHT_DPKG_LOCKS; do
            [[ -e "$f" ]] && "$PREFLIGHT_FUSER" -s "$f" 2>/dev/null && return 0
        done
        return 1
    fi
    # No fuser: fall back to looking for a running package manager.
    pgrep -x 'apt|apt-get|aptitude|dpkg|unattended-upgr|packagekitd' >/dev/null 2>&1
}

# Units the uninstall stops itself: every unit a manifest records, plus Kent's own naming.
_pf_kent_units() {
    { _pf_entries unit; _pf_entries enabled; _pf_entries userunit | awk '{ print $NF }'; } \
        | while read -r u; do [[ -n "$u" ]] && basename "$u"; done | sort -u
}

# Does this systemd unit belong to Kent (so the uninstall stops it and its processes)?
_pf_is_kent_unit() {  # _pf_is_kent_unit <unit> <known-units, newline-separated>
    local unit="$1" known="$2" id label
    [[ -n "$unit" && "$unit" != "-" ]] || return 1
    grep -qxF "$unit" <<<"$known" && return 0
    case "$unit" in
        kent-*|srv-kent-*) return 0 ;;
        docker-*.scope)    # a container: Kent's if it carries the kent.module label
            id="${unit#docker-}"; id="${id%.scope}"
            label="$("$PREFLIGHT_DOCKER" inspect -f '{{index .Config.Labels "kent.module"}}' "$id" 2>/dev/null || true)"
            [[ -n "$label" && "$label" != "<no value>" ]] ;;
        *) return 1 ;;
    esac
}

preflight_check() {
    local n=0 mf bad u pids dir units
    say_problem() { echo "  ✗ $*"; n=$((n + 1)); }

    if [[ -d "$PREFLIGHT_MANIFEST_DIR" && ! -r "$PREFLIGHT_MANIFEST_DIR" ]]; then
        say_problem "cannot read the manifests in $PREFLIGHT_MANIFEST_DIR as $(id -un); run the check with sudo."
        return "$n"
    fi
    if ! compgen -G "$PREFLIGHT_MANIFEST_DIR/*" >/dev/null; then
        echo "  nothing recorded as installed (no manifests in $PREFLIGHT_MANIFEST_DIR)"
        return 0
    fi

    # 1. Damaged manifests: an unknown kind would be silently skipped by the uninstaller.
    for mf in "$PREFLIGHT_MANIFEST_DIR"/*; do
        bad="$(awk -v kinds=" $PREFLIGHT_KINDS " 'NF && index(kinds, " " $1 " ") == 0 { print NR ": " $0 }' "$mf")"
        [[ -z "$bad" ]] || say_problem "manifest $(basename "$mf") has lines the uninstaller does not understand" \
            "(${bad//$'\n'/; }). It may be damaged or from a newer Kent; do not uninstall with this checkout."
    done

    # 2. Processes running as accounts Kent created, outside the units the uninstall stops itself
    #    (an open `kent` chat, a login session, a stray shell): userdel would refuse midway.
    local known pid unit where
    known="$(_pf_kent_units)"
    while read -r u; do
        [[ -n "$u" ]] && getent passwd "$u" >/dev/null 2>&1 || continue
        pids="$(pgrep -u "$u" 2>/dev/null || true)"
        [[ -n "$pids" ]] || continue
        if ! "$PREFLIGHT_PS" -o unit= -p "${pids%%$'\n'*}" >/dev/null 2>&1; then
            say_problem "account '$u' has running processes (PIDs $(head -5 <<<"$pids" | paste -sd, -)); cannot tell" \
                "whether these belong to Kent's own services. Stop anything run as '$u' outside Kent's services."
            continue
        fi
        where=""
        for pid in $pids; do
            unit="$("$PREFLIGHT_PS" -o unit= -p "$pid" 2>/dev/null | tr -d ' ')"
            _pf_is_kent_unit "$unit" "$known" && continue
            where+="${unit:-no unit} PID $pid; "
        done
        [[ -z "$where" ]] || say_problem "account '$u' has processes outside Kent's services (${where%; })." \
            "End them first: close the session or program (e.g. an open 'kent' chat), or sudo loginctl terminate-session <id> for a session-N.scope."
    done < <(_pf_entries user | sort -u)

    # 3. Docker: needed to remove recorded networks/images and to stop container units.
    units="$(_pf_entries unit | while read -r f; do [[ -f "$f" ]] && grep -l '/usr/bin/docker' "$f"; done || true)"
    if [[ -n "$(_pf_entries dockernet)$(_pf_entries dockerimage)$(_pf_entries dockerbase)$units" ]] \
       && ! systemctl is-active --quiet docker 2>/dev/null; then
        say_problem "Docker is not running, but Kent recorded Docker networks, images or container units." \
            "Start it first: sudo systemctl start docker"
    fi

    # 4. Packages to purge: apt fails if another package manager holds the dpkg lock.
    if [[ -n "$(_pf_entries package)" ]] && _pf_dpkg_busy; then
        say_problem "another package manager holds the dpkg lock (e.g. automatic updates)," \
            "and Kent must purge: $(_pf_entries package | paste -sd' ' -). Wait until it finishes, then re-run."
    fi

    # 5. The model directory the llama module re-owned must be present so its owner/mode can be
    #    restored; otherwise the files would be left owned by a group that no longer exists.
    while read -r dir; do
        [[ -z "$dir" || -d "$dir" ]] || say_problem "the model directory $dir is missing (drive not mounted?)." \
            "Mount it first, so the model files can be given back before the kent-models group is removed."
    done < <(_pf_entries modelsdir)

    return "$n"
}
