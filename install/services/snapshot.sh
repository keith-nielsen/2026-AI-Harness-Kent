#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/snapshot.sh
# Read-only record of everything a Kent service install can touch, so an
# install can be audited and an uninstall proven to restore the prior state.
# Independent of the installers' own manifests.
#
# Usage:
#   sudo ./snapshot.sh take <label>          # e.g. pre-litellm, post-litellm
#   ./snapshot.sh diff <labelA> <labelB>      # what changed between two snapshots
#   ./snapshot.sh list
#
# Snapshots live in ~/.local/share/kent/install-records/<label>/ (operator-owned).
# Run "take" with sudo so root-only paths (credentials, /var/lib) are visible;
# file contents of secrets are never recorded, only their metadata and hashes.
# =============================================================================
set -euo pipefail

OPERATOR="${KENT_OPERATOR:-${SUDO_USER:-$USER}}"
OPERATOR_HOME="$(getent passwd "$OPERATOR" | cut -d: -f6)"
RECORDS="${OPERATOR_HOME}/.local/share/kent/install-records"

# Paths whose files are listed with mode/owner/size AND content hash.
HASHED_PATHS=(
    /etc/kent
    /etc/systemd/system
    /usr/local/bin
    /etc/cron.d
    /etc/sudoers.d
    /etc/apt/sources.list.d
    /etc/apt/keyrings
    /etc/squid
    /etc/grafana
    /etc/alloy
    /etc/default
    /etc/ufw
    "${OPERATOR_HOME}/.config/kent"
    "${OPERATOR_HOME}/.hermes/profiles/kent/config.yaml"
    /var/lib/kent
)
# Paths listed with mode/owner/size only (large trees, e.g. venvs, state).
LISTED_PATHS=(
    /opt
    /var/lib/litellm
    /var/lib/gitea /var/lib/prometheus /var/lib/loki /var/lib/alloy /var/lib/grafana
)

take() {
    local label="$1" out="${RECORDS}/$1"
    [[ -e "$out" ]] && { echo "snapshot '$label' already exists: $out" >&2; exit 1; }
    # Records live in the operator's home: create (or repair) the parent dirs as
    # the operator, never as root (an earlier version left ~/.local/share/kent
    # root-owned when first run via sudo).
    local base="${OPERATOR_HOME}/.local/share/kent"
    install -d -m 0755 -o "$OPERATOR" -g "$(id -gn "$OPERATOR")" "${OPERATOR_HOME}/.local/share" "$base" "$RECORDS"
    [[ $EUID -eq 0 ]] || echo "WARNING: not root; root-only paths will be missing from the snapshot" >&2
    mkdir -p "$out"

    {
        echo "label: $label"
        echo "taken: $(date -Is)"
        echo "host: $(hostname)"
        echo "by: ${OPERATOR} (euid ${EUID})"
        echo "repo_commit: $(git -C "$(dirname "$0")" rev-parse --short HEAD 2>/dev/null || echo n/a)"
        echo "repo_dirty: $(git -C "$(dirname "$0")" status --porcelain 2>/dev/null | wc -l) file(s)"
    } > "$out/META"

    getent passwd | sort > "$out/accounts.passwd"
    getent group  | sort > "$out/accounts.group"

    systemctl list-unit-files --no-legend --no-pager 2>/dev/null | awk '{print $1, $2}' | sort > "$out/systemd.unit-files"
    systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null \
        | awk '{print $1}' | sort > "$out/systemd.running-services"

    ss -ltnH 2>/dev/null | awk '{print $4}' | sort -u > "$out/listening.tcp"

    dpkg-query -W -f='${Package} ${Version}\n' 2>/dev/null | sort > "$out/packages.dpkg"

    ufw status verbose 2>/dev/null > "$out/firewall.ufw" || echo "ufw unavailable" > "$out/firewall.ufw"
    { docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null | sort
      docker network ls --format 'network {{.Name}} {{.Driver}}' 2>/dev/null | sort
      docker ps -a --format 'container {{.Names}} {{.Image}}' 2>/dev/null | sort
    } > "$out/docker.objects"

    : > "$out/files.hashed"
    for p in "${HASHED_PATHS[@]}"; do
        [[ -e "$p" ]] || { echo "ABSENT $p" >> "$out/files.hashed"; continue; }
        find "$p" -xdev \( -type f -o -type l -o -type d \) -printf '%y %m %u:%g %s %p\n' 2>/dev/null \
            | sort -k5 | while read -r type mode owner size path; do
                hash="-"
                [[ "$type" == "f" ]] && hash="$(sha256sum "$path" 2>/dev/null | cut -c1-16)"
                echo "$type $mode $owner $size $hash $path"
            done >> "$out/files.hashed"
    done

    : > "$out/files.listed"
    for p in "${LISTED_PATHS[@]}"; do
        [[ -e "$p" ]] || { echo "ABSENT $p" >> "$out/files.listed"; continue; }
        find "$p" -xdev -printf '%y %m %u:%g %p\n' 2>/dev/null | sort -k4 >> "$out/files.listed"
    done

    [[ -n "${SUDO_USER:-}" ]] && chown -R "$OPERATOR:$OPERATOR" "$RECORDS"
    echo "snapshot '$label' saved: $out"
    wc -l "$out"/* | sed 's|'"$out"'/||' | grep -v total
}

diff_snapshots() {
    local a="${RECORDS}/$1" b="${RECORDS}/$2"
    [[ -d "$a" && -d "$b" ]] || { echo "unknown snapshot(s): $1 $2" >&2; exit 1; }
    local changed=0
    for f in accounts.passwd accounts.group systemd.unit-files systemd.running-services \
             listening.tcp packages.dpkg firewall.ufw docker.objects files.hashed files.listed; do
        if ! diff -q "$a/$f" "$b/$f" >/dev/null 2>&1; then
            changed=1
            local added removed
            added=$(diff "$a/$f" "$b/$f" | grep -c '^>' || true)
            removed=$(diff "$a/$f" "$b/$f" | grep -c '^<' || true)
            echo "=== $f: +${added} -${removed}"
            diff "$a/$f" "$b/$f" | grep -E '^[<>]' | head -"${DIFF_LINES:-40}" || true
        fi
    done
    [[ "$changed" -eq 0 ]] && echo "IDENTICAL: '$1' and '$2' match on every recorded item."
    return 0
}

case "${1:-}" in
    take) [[ -n "${2:-}" ]] || { echo "usage: $0 take <label>" >&2; exit 2; }; take "$2" ;;
    diff) [[ -n "${2:-}" && -n "${3:-}" ]] || { echo "usage: $0 diff <A> <B>" >&2; exit 2; }; diff_snapshots "$2" "$3" ;;
    list) ls -1 "$RECORDS" 2>/dev/null || echo "(no snapshots)";;
    *) sed -n '2,/^# =====/p' "$0" | sed '$d'; exit 2 ;;
esac
