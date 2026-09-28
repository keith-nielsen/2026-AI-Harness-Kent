#!/usr/bin/env bash
# =============================================================================
# Kent — uninstall.sh (run from a checkout of the repository)
#
#   sudo ./uninstall.sh [--purge] [--dry-run]
#
# Removes every installed Kent module in reverse dependency order, exactly as recorded in its
# manifest (/var/lib/kent-install/manifest). Kent's data (its database, audit chain, Gent
# archives, Gitea repositories, metrics and logs) is kept unless --purge. Files in operators'
# home directories (~/.config/kent) are listed for the operator to delete, not removed by root.
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
ARGS=()
for a in "$@"; do
    case "$a" in
        --purge)   ARGS+=(--purge-state) ;;
        --dry-run) ARGS+=(--dry-run) ;;
        -h|--help) sed -n '2,/^# =====/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done
[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo ./uninstall.sh $*" >&2; exit 1; }
MODULES=(gent kent-core hermes searxng gitea grafana alloy loki node_exporter prometheus litellm)
rc=0
for mod in "${MODULES[@]}"; do
    if [[ ! -f "/var/lib/kent-install/manifest/$mod" ]]; then printf '  %-14s not installed\n' "$mod"; continue; fi
    printf '  %-14s ' "$mod"
    if out="$("$ROOT/install/services/$mod/uninstall.sh" "${ARGS[@]}" 2>&1)"; then
        echo "✓ $(tail -1 <<<"$out" | sed 's/^\[[0-9:]*\] [a-z_-]*: //')"
    else
        echo "✗"; tail -10 <<<"$out" | sed 's/^/      /'; rc=1
    fi
done
op="${SUDO_USER:-}"
if [[ -n "$op" && -d "$(getent passwd "$op" | cut -d: -f6)/.config/kent" ]]; then
    echo ""
    echo "Your personal Kent credentials remain in ~$op/.config/kent (delete them when no longer needed)."
fi
exit "$rc"
