#!/usr/bin/env bash
# =============================================================================
# Kent — uninstall.sh (run from a checkout of the repository)
#
#   sudo ./uninstall.sh [--purge] [--dry-run] [--log FILE] [--trace]
#
# Removes every installed Kent module in reverse dependency order, exactly as recorded in its
# manifest (/var/lib/kent-install/manifest). Kent's data (its database, audit chain, Gent
# archives, Gitea repositories, metrics and logs) is kept unless --purge. Files in operators'
# home directories (~/.config/kent) are listed for the operator to delete, not removed by root.
#
#   --log FILE   also write every module's full output to FILE (owned by the invoking user), with
#                a header (repository commit, host, time) and each module's exit status
#   --trace      also trace every command of this script and of each module (timestamp, script,
#                line) into FILE.trace, kept apart so FILE holds exactly what the scripts printed;
#                needs --log
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
ARGS=(); LOG=""; TRACE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge)   ARGS+=(--purge-state); shift ;;
        --dry-run) ARGS+=(--dry-run); shift ;;
        --log)     [[ -n "${2:-}" ]] || { echo "--log needs a file" >&2; exit 2; }; LOG="$2"; shift 2 ;;
        --trace)   TRACE=1; shift ;;
        -h|--help) sed -n '2,/^# =====/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
[[ "$TRACE" -eq 0 || -n "$LOG" ]] || { echo "--trace needs --log FILE" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo ./uninstall.sh (see --help)" >&2; exit 1; }
MODULES=(gent kent-core hermes searxng gitea grafana alloy loki node_exporter prometheus litellm llama)
op="${SUDO_USER:-}"

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
     "# host $(hostname)  invoked by ${op:-root}  args: ${ARGS[*]:-(none)}$( [[ "$TRACE" -eq 1 ]] && echo ' --trace')" \
     "# repo $ROOT  commit $(git_ rev-parse HEAD || echo '?') $(git_ describe --tags --always || true)  $( [[ -z "$(git_ status --porcelain)" ]] && echo clean || echo "DIRTY ($(git_ status --porcelain | wc -l) files)")" \
     "# manifests: $(ls /var/lib/kent-install/manifest 2>/dev/null | tr '\n' ' ')"

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

rc=0
for mod in "${MODULES[@]}"; do
    if [[ ! -f "/var/lib/kent-install/manifest/$mod" ]]; then
        printf '  %-14s not installed\n' "$mod"; logf "" "=== $mod: not installed"
        continue
    fi
    printf '  %-14s ' "$mod"
    out="$(mktemp)"
    logf "" "=== $mod: start $(date +%H:%M:%S)"
    if "$ROOT/install/services/$mod/uninstall.sh" "${ARGS[@]}" >"$out" 2>&1; then st=0; else st=$?; fi
    [[ -z "$LOG" ]] || cat "$out" >> "$LOG"
    logf "=== $mod: exit $st $(date +%H:%M:%S)"
    if [[ "$st" -eq 0 ]]; then
        echo "✓ $(tail -1 "$out" | sed 's/^\[[0-9:]*\] [a-z_-]*: //')"
    else
        echo "✗ (exit $st)"; tail -10 "$out" | sed 's/^/      /'; rc=1
    fi
    rm -f "$out"
done
# Shared parents that one module creates and a later-removed module still used (e.g. /etc/kent,
# created by litellm, still holds llama's config when litellm is uninstalled): remove if empty.
if [[ "$rc" -eq 0 && " ${ARGS[*]} " != *" --dry-run "* ]] && ! compgen -G "/var/lib/kent-install/manifest/*" >/dev/null; then
    for d in /etc/kent /srv/kent; do
        if [[ -d "$d" ]]; then rmdir --ignore-fail-on-non-empty "$d"; logf "removed $d if empty"; fi
    done
fi
# --purge: once every module is gone, drop the installer's own bookkeeping (install logs, the
# emptied manifest and backup directories) so nothing of Kent remains under /var/lib.
if [[ "$rc" -eq 0 && " ${ARGS[*]} " == *" --purge-state "* && " ${ARGS[*]} " != *" --dry-run "* ]]; then
    if compgen -G "/var/lib/kent-install/manifest/*" >/dev/null; then
        echo "  kept /var/lib/kent-install: manifests remain ($(ls /var/lib/kent-install/manifest | tr '\n' ' '))"
    elif [[ -d /var/lib/kent-install ]]; then
        rm -rf --one-file-system /var/lib/kent-install
        logger -t kent-install -- "purged /var/lib/kent-install (all modules uninstalled)" 2>/dev/null || true
        echo "  removed /var/lib/kent-install"; logf "removed /var/lib/kent-install"
    fi
fi
{ set +x; } 2>/dev/null
logf "" "# finished $(date -Is), exit $rc"
if [[ -n "$op" && -d "$(getent passwd "$op" | cut -d: -f6)/.config/kent" ]]; then
    echo ""
    echo "Your personal Kent credentials remain in ~$op/.config/kent (delete them when no longer needed)."
fi
[[ -z "$LOG" ]] || echo "Full log: $LOG$( [[ "$TRACE" -eq 1 ]] && echo " (trace: $LOG.trace)")"
exit "$rc"
