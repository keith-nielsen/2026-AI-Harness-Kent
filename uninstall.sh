#!/usr/bin/env bash
# =============================================================================
# Kent — uninstall.sh (run from a checkout of the repository)
#
#   sudo ./uninstall.sh [--purge] [--dry-run] [--log FILE] [--trace] [--keep-going]
#   ./uninstall.sh --check       only the pre-flight check (no root needed; changes nothing)
#   ./uninstall.sh --verify      only check that no Kent component is left (no root needed;
#                                root sees more)
#
# Removes every installed Kent module in reverse dependency order, exactly as recorded in its
# manifest (/var/lib/kent-install/manifest). Kent's data (its database, audit chain, Gent
# archives, Gitea repositories, metrics and logs) is kept unless --purge. Files in operators'
# home directories (~/.config/kent) are listed for the operator to delete, not removed by root.
# The installer's download cache (/var/cache/kent-install: pinned binaries, .deb and Python
# packages, reused by the next install) is also kept unless --purge. To reclaim its space
# without purging: sudo rm -rf /var/cache/kent-install
#
#   --purge      also remove Kent's data and the download cache (/var/cache/kent-install);
#                afterwards verify-clean.sh checks that no Kent component is left (only when
#                every module succeeded and not with --dry-run)
#   --dry-run    print what would be done; changes nothing
#   --log FILE   also write every module's full output to FILE (owned by the invoking user), with
#                a header (repository commit, host, time) and each module's exit status
#   --trace      also trace every command of this script and of each module (timestamp, script,
#                line) into FILE.trace, kept apart so FILE holds exactly what the scripts printed;
#                needs --log
#   --keep-going continue with the remaining modules after one fails (default: stop at the first
#                failure, print its likely cause and the command to resume; modules already
#                uninstalled are skipped when you re-run)
#   --check      look for what would make the uninstall fail halfway (processes still running as
#                Kent accounts outside Kent's own services, Docker stopped, the dpkg lock held, the
#                model drive not mounted, damaged manifests) and report. The same check runs before
#                every uninstall and stops it before anything is changed (a --dry-run only warns).
#   --verify     only run install/services/verify-clean.sh (a fixed list of every Kent name and
#                place) and report what is left
# --check and --verify are report-only and take no other option.
#
# Exit: 0 done (or --check: ready; --verify: nothing left)
#       1 a module failed (stopped there; with --keep-going: after trying the rest)
#       2 usage error (unknown or conflicting option; not run as root)
#       3 pre-flight check refused (nothing was changed)
#       4 leftovers: verify-clean.sh found Kent components (--verify, or the check after a
#         --purge in which every module succeeded; listed in the output and the log)
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=install/services/lib-preflight.sh
source "$ROOT/install/services/lib-preflight.sh"
# shellcheck source=install/services/lib-uninstall-hints.sh
source "$ROOT/install/services/lib-uninstall-hints.sh"
ORIG_ARGS=("$@")   # for the resume command printed after a failure
ARGS=(); LOG=""; TRACE=0; KEEP_GOING=0; MODE=uninstall; NOPTS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge)      ARGS+=(--purge-state) ;;
        --dry-run)    ARGS+=(--dry-run) ;;
        --log)        [[ -n "${2:-}" ]] || { echo "--log needs a file" >&2; exit 2; }; LOG="$2"; shift ;;
        --trace)      TRACE=1 ;;
        --keep-going) KEEP_GOING=1 ;;
        --check)      [[ "$MODE" == uninstall ]] || { echo "--check and --verify cannot be combined" >&2; exit 2; }; MODE=check ;;
        --verify)     [[ "$MODE" == uninstall ]] || { echo "--check and --verify cannot be combined" >&2; exit 2; }; MODE=verify ;;
        -h|--help)    sed -n '2,/^# =====/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift; NOPTS=$((NOPTS + 1))
done
if [[ "$MODE" != uninstall ]]; then
    [[ "$NOPTS" -eq 1 ]] || { echo "--$MODE takes no other option (see --help)" >&2; exit 2; }
fi
[[ "$TRACE" -eq 0 || -n "$LOG" ]] || { echo "--trace needs --log FILE" >&2; exit 2; }

# --- Report-only modes: no root needed, nothing changed --------------------------------------
if [[ "$MODE" == check ]]; then
    echo "Kent uninstall pre-flight check:"
    if preflight_check; then echo "  ✓ ready to uninstall"; exit 0; fi
    echo "Fix the problems above, then run the check again."; exit 3
fi
if [[ "$MODE" == verify ]]; then
    if "$ROOT/install/services/verify-clean.sh"; then exit 0; else st=$?; fi
    [[ "$st" -eq 1 ]] && exit 4
    exit "$st"
fi

[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo ./uninstall.sh (see --help; --check and --verify need no root)" >&2; exit 2; }
MODULES=(gent kent-core hermes searxng gitea grafana alloy loki node_exporter prometheus litellm llama)
op="${SUDO_USER:-}"
DRY=0; [[ " ${ARGS[*]} " == *" --dry-run "* ]] && DRY=1
PURGE=0; [[ " ${ARGS[*]} " == *" --purge-state "* ]] && PURGE=1
# Test-only overrides (tests/install/test_uninstall_hints.py), as KENT_MANIFEST_DIR in lib-service.sh.
MDIR_DEFAULT=/var/lib/kent-install/manifest
MDIR="${KENT_MANIFEST_DIR:-$MDIR_DEFAULT}"
SVC="${KENT_SERVICES_DIR:-$ROOT/install/services}"

# --- Log: a file the invoking user owns (never under a path the uninstall removes) -------------
if [[ -n "$LOG" ]]; then
    LOG="$(realpath -m "$LOG")"
    case "$LOG" in /var/lib/kent*|/etc/kent*|/opt/kent*|/srv/kent*) echo "--log must not be inside a Kent path" >&2; exit 2 ;; esac
    files=("$LOG"); [[ "$TRACE" -eq 1 ]] && files+=("$LOG.trace")
    for f in "${files[@]}"; do
        : > "$f"; chmod 0600 "$f"
        [[ -n "$op" && "$op" != root ]] && chown "$op:" "$f"
    done
fi
logf() { [[ -z "$LOG" ]] || printf '%s\n' "$@" >> "$LOG"; }
git_() { git -c safe.directory="$ROOT" -C "$ROOT" "$@" 2>/dev/null; }
logf "# Kent uninstall — $(date -Is)" \
     "# host $(hostname)  invoked by ${op:-root}  args: ${ARGS[*]:-(none)}$( [[ "$TRACE" -eq 1 ]] && echo ' --trace')$( [[ "$KEEP_GOING" -eq 1 ]] && echo ' --keep-going')" \
     "# repo $ROOT  commit $(git_ rev-parse HEAD || echo '?') $(git_ describe --tags --always || true)  $( [[ -z "$(git_ status --porcelain)" ]] && echo clean || echo "DIRTY ($(git_ status --porcelain | wc -l) files)")" \
     "# manifests: $(ls "$MDIR" 2>/dev/null | tr '\n' ' ')"

# --- Trace: bash imports SHELLOPTS, PS4 and BASH_XTRACEFD from the environment and module
# scripts inherit the open descriptor, so every script traces into FILE.trace (time, script:line)
# without mixing into its output.
if [[ "$TRACE" -eq 1 ]]; then
    exec {xfd}>>"$LOG.trace"
    export BASH_XTRACEFD=$xfd
    export PS4='+ $(date +%H:%M:%S.%3N) ${BASH_SOURCE[0]##*/}:${LINENO}: '
    export SHELLOPTS
    set -x
fi

# --- Pre-flight: refuse to start what would fail halfway (a dry run only warns) --------------
if ! pf="$(preflight_check)"; then
    if [[ "$DRY" -eq 1 ]]; then
        printf 'Pre-flight warnings (a real uninstall would stop here):\n%s\n' "$pf"; logf "# pre-flight warnings:" "$pf"
    else
        { { set +x; } 2>/dev/null
          printf 'Pre-flight check failed; nothing was changed:\n%s\n' "$pf"
          echo "Fix the problems above and run the same command again (check only: ./uninstall.sh --check)."; } >&2
        logf "# pre-flight failed; nothing changed:" "$pf" "" "# finished $(date -Is), exit 3"
        [[ -z "$LOG" ]] || echo "Full log: $LOG" >&2
        exit 3
    fi
fi

# The command to resume after a failure: the same options, with --log pointed at a new file so
# the log of the failed run is kept (re-running with the same --log would overwrite it).
resume_cmd() {
    local a cmd=("sudo" "$0") prev=""
    for a in "${ORIG_ARGS[@]}"; do
        if [[ "$prev" == --log ]]; then a="${a%.log}-resume.log"; fi
        cmd+=("$a"); prev="$a"
    done
    printf '%q ' "${cmd[@]}" | sed 's/ $//'
}

rc=0; failed=()
for mod in "${MODULES[@]}"; do
    if [[ ! -f "$MDIR/$mod" ]]; then
        printf '  %-14s not installed\n' "$mod"; logf "" "=== $mod: not installed"
        continue
    fi
    printf '  %-14s ' "$mod"
    out="$(mktemp)"
    logf "" "=== $mod: start $(date +%H:%M:%S)"
    if "$SVC/$mod/uninstall.sh" "${ARGS[@]}" >"$out" 2>&1; then st=0; else st=$?; fi
    [[ -z "$LOG" ]] || cat "$out" >> "$LOG"
    logf "=== $mod: exit $st $(date +%H:%M:%S)"
    if [[ "$st" -eq 0 ]]; then
        echo "✓ $(tail -1 "$out" | sed 's/^\[[0-9:]*\] [a-z_-]*: //')"
    else
        echo "✗ (exit $st)"; tail -10 "$out" | sed 's/^/      /'; rc=1; failed+=("$mod")
        hint="$(uninstall_hint "$out")"
        echo "      likely cause: $hint"; logf "=== $mod: likely cause: $hint"
        if [[ "$KEEP_GOING" -eq 0 ]]; then
            rm -f "$out"
            remaining=(); after=0   # modules after the failed one in the uninstall order
            for m in "${MODULES[@]}"; do
                if [[ "$after" -eq 1 && -f "$MDIR/$m" ]]; then remaining+=("$m"); fi
                [[ "$m" == "$mod" ]] && after=1
            done
            { set +x; } 2>/dev/null
            echo ""
            echo "Stopped at '$mod' (exit $st). Not yet uninstalled: $mod ${remaining[*]}"
            [[ -z "$LOG" ]] || echo "Full log: $LOG$( [[ "$TRACE" -eq 1 ]] && echo " (trace: $LOG.trace)")"
            echo "Fix the cause, then resume. Modules already done are skipped (with --purge their manifest"
            echo "is gone) or, without --purge, run again harmlessly (every step is idempotent):"
            echo "  $(resume_cmd)"
            logf "" "# stopped at $mod $(date -Is), exit 1"
            exit 1
        fi
    fi
    rm -f "$out"
done
[[ ${#failed[@]} -eq 0 ]] || echo "  failed (--keep-going): ${failed[*]}; re-run the same command to retry them"
# The clean-up below acts on the real system paths, so only against the real manifests.
REAL=0; [[ "$MDIR" == "$MDIR_DEFAULT" && "$DRY" -eq 0 ]] && REAL=1
# Shared parents that one module creates and a later-removed module still used (e.g. /etc/kent,
# created by litellm, still holds llama's config when litellm is uninstalled): remove if empty.
if [[ "$rc" -eq 0 && "$REAL" -eq 1 ]] && ! compgen -G "$MDIR/*" >/dev/null; then
    for d in /etc/kent /srv/kent; do
        if [[ -d "$d" ]]; then rmdir --ignore-fail-on-non-empty "$d"; logf "removed $d if empty"; fi
    done
fi
# --purge: once every module is gone, drop the installer's own bookkeeping (install logs, the
# emptied manifest and backup directories) so nothing of Kent remains under /var/lib.
if [[ "$rc" -eq 0 && "$PURGE" -eq 1 && "$REAL" -eq 1 ]]; then
    if compgen -G "$MDIR/*" >/dev/null; then
        echo "  kept /var/lib/kent-install: manifests remain ($(ls "$MDIR" | tr '\n' ' '))"
    elif [[ -d /var/lib/kent-install ]]; then
        rm -rf --one-file-system /var/lib/kent-install
        logger -t kent-install -- "purged /var/lib/kent-install (all modules uninstalled)" 2>/dev/null || true
        echo "  removed /var/lib/kent-install"; logf "removed /var/lib/kent-install"
    fi
    if [[ -d /var/cache/kent-install ]] && ! compgen -G "$MDIR/*" >/dev/null; then
        size="$(du -sh /var/cache/kent-install 2>/dev/null | cut -f1)"
        rm -rf --one-file-system /var/cache/kent-install
        logger -t kent-install -- "purged the download cache /var/cache/kent-install" 2>/dev/null || true
        echo "  removed the download cache /var/cache/kent-install (${size:-?})"; logf "removed /var/cache/kent-install"
    fi
fi
# --purge: prove the result (only after every module succeeded, never on a dry run). Leftovers
# make the exit status 4 (the uninstall itself completed).
if [[ "$rc" -eq 0 && "$PURGE" -eq 1 && "$REAL" -eq 1 ]]; then
    echo ""; echo "Checking that no Kent component is left:"
    if vout="$("$ROOT/install/services/verify-clean.sh" 2>&1)"; then vst=0; else vst=$?; rc=4; fi
    sed 's/^/  /' <<<"$vout"; logf "" "# verify-clean.sh (exit $vst):" "$vout"
fi
{ set +x; } 2>/dev/null
logf "" "# finished $(date -Is), exit $rc"
if [[ -n "$op" && -d "$(getent passwd "$op" | cut -d: -f6)/.config/kent" ]]; then
    echo ""
    echo "Your personal Kent credentials remain in ~$op/.config/kent (delete them when no longer needed)."
fi
if [[ "$REAL" -eq 1 && "$PURGE" -eq 0 && -d /var/cache/kent-install ]]; then
    echo ""
    echo "The download cache /var/cache/kent-install ($(du -sh /var/cache/kent-install 2>/dev/null | cut -f1)) is kept for the next install."
    echo "To reclaim the space: sudo rm -rf /var/cache/kent-install   (or uninstall with --purge)"
fi
[[ -z "$LOG" ]] || echo "Full log: $LOG$( [[ "$TRACE" -eq 1 ]] && echo " (trace: $LOG.trace)")"
exit "$rc"
