#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/verify-clean.sh
# Confirm that no Kent component is left on this machine. After `uninstall.sh --purge` no
# manifest remains, so this checks a fixed list of every name and place Kent uses.
#
#   install/services/verify-clean.sh            (or: ./uninstall.sh --verify)
#
# Prints one line per finding:
#   LEFTOVER      a Kent component still present                 -> exit 1
#   WARN          a Kent port in use (something listens; may not be Kent)
#   INFO          worth knowing, not a leftover (something on 8080, the local-model port; the
#                 operator's own ~/.config/kent, which uninstall leaves for them to delete; the
#                 download cache /var/cache/kent-install, kept by a plain uninstall)
#   NOT CHECKED   could not be inspected (usually needs root, or a tool is missing)
# Exit: 0 = no Kent components found, 1 = leftovers found, 2 = usage error.
#
# Runs without root; as a normal user it cannot read root-only places (polkit rules, ufw
# rules, inside root-only directories) and says so under NOT CHECKED. Run as root (as
# uninstall.sh does after --purge) for full coverage. Read-only: changes nothing.
#
# Testing: KENT_VERIFY_ROOT=<dir> checks paths (and <dir>/etc/passwd, <dir>/etc/group,
# <dir>/etc/ufw/user.rules) under a fake root; KENT_VERIFY_HOME sets the operator's home;
# KENT_VERIFY_SKIP="accounts units docker ports ufw" skips checks that query the live system.
# =============================================================================
set -uo pipefail
R="${KENT_VERIFY_ROOT:-}"
SKIP=" ${KENT_VERIFY_SKIP:-} "
[[ $# -eq 0 ]] || { [[ "$1" == -h || "$1" == --help ]] && { sed -n '2,/^# =====/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0; }
                    echo "usage: verify-clean.sh (no options; see --help)" >&2; exit 2; }

# The operator whose home is checked: the sudo caller when run as root, else the current user.
if [[ -n "${KENT_VERIFY_HOME:-}" ]]; then OPHOME="$KENT_VERIFY_HOME"
elif [[ $EUID -eq 0 && -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then OPHOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else OPHOME="$HOME"; fi

ACCOUNTS=(litellm prometheus node_exporter loki alloy gitea kent kent-llama kent-searxng kent-squid)
GROUPS_=(kent-operators kent-models "${ACCOUNTS[@]}")
PORTS=(4000 3000 3001 3100 3129 8888 9090 9100 9095 12345)

left=0; n_left=0
found()   { printf 'LEFTOVER     %-9s %s\n' "$1" "$2"; left=1; n_left=$((n_left + 1)); }
warn()    { printf 'WARN         %-9s %s\n' "$1" "$2"; }
info()    { printf 'INFO         %-9s %s\n' "$1" "$2"; }
unknown() { printf 'NOT CHECKED  %-9s %s\n' "$1" "$2"; }
skipped() { [[ "$SKIP" == *" $1 "* ]]; }

# --- Accounts and groups (grafana is a vendor package's account and is never flagged) ------
if ! skipped accounts; then
    if [[ -n "$R" ]]; then
        pw() { cut -d: -f1 "$R/etc/passwd" 2>/dev/null; }; gr() { cut -d: -f1 "$R/etc/group" 2>/dev/null; }
    else
        pw() { getent passwd | cut -d: -f1; }; gr() { getent group | cut -d: -f1; }
    fi
    users="$(pw)"; groups="$(gr)"
    for a in "${ACCOUNTS[@]}"; do grep -qx "$a" <<<"$users" && found account "$a"; done
    while read -r a; do [[ -n "$a" ]] && found account "$a"; done < <(grep -E '^gent-' <<<"$users")
    for g in "${GROUPS_[@]}"; do grep -qx "$g" <<<"$groups" && found group "$g"; done
    while read -r g; do [[ -n "$g" ]] && found group "$g"; done < <(grep -E '^gent-' <<<"$groups")
fi

# --- systemd units: unit files (system and the operator's user units), plus loaded units ---
if ! skipped units; then
    for d in /etc/systemd/system /usr/lib/systemd/system /lib/systemd/system /run/systemd/system \
             "$OPHOME/.config/systemd/user"; do
        # kent.conf: Kent's drop-ins in vendor units (alloy.service.d, grafana-server.service.d)
        for f in "$R$d"/kent-* "$R$d"/srv-kent-* "$R$d"/*.wants/kent-* "$R$d"/*.wants/srv-kent-* "$R$d"/kent-*.d \
                 "$R$d"/*.d/kent.conf; do
            [[ -e "$f" || -L "$f" ]] && found unit "${f#"$R"}"
        done
    done
    if [[ -z "$R" ]] && command -v systemctl >/dev/null; then
        while read -r u _; do [[ -n "$u" ]] && found unit "$u (loaded)"; done < \
            <(systemctl list-units --all --no-legend --plain 'kent-*' 'srv-kent-*' 2>/dev/null)
        if [[ $EUID -ne 0 ]]; then
            while read -r u _; do [[ -n "$u" ]] && found unit "$u (user unit)"; done < \
                <(systemctl --user list-units --all --no-legend --plain 'kent-*' 2>/dev/null)
        fi
    fi
fi

# --- Paths Kent creates ----------------------------------------------------------------
shopt -s nullglob
for p in "$R"/etc/kent "$R"/opt/kent-* "$R"/var/lib/kent "$R"/var/lib/kent-* "$R"/var/cache/kent-* \
         "$R"/var/log/kent-* "$R"/srv/kent "$R"/usr/local/bin/kent "$R"/etc/sudoers.d/9[0-9]-kent-* \
         "$R"/var/lib/systemd/timers/stamp-kent-* "$R$OPHOME"/.local/share/systemd/timers/stamp-kent-* \
         "$R"/var/lib/systemd/linger/kent; do
    [[ "$p" == "$R/var/cache/kent-install" ]] && continue   # the download cache: see below
    [[ -e "$p" || -L "$p" ]] && found path "${p#"$R"}"
done
# The installer's download cache survives a plain uninstall on purpose (--purge removes it).
if [[ -d "$R/var/cache/kent-install" ]]; then
    sz="size needs root"; [[ -r "$R/var/cache/kent-install" && -x "$R/var/cache/kent-install" ]] \
        && sz="$(du -sh "$R/var/cache/kent-install" 2>/dev/null | cut -f1)"
    info path "/var/cache/kent-install: download cache for reinstalls ($sz); reclaim: sudo rm -rf /var/cache/kent-install"
fi
# The operator's own credentials: uninstall removes the files it placed there and deliberately
# leaves the rest for the operator to delete, so this is information, not a leftover.
if [[ -e "$R$OPHOME/.config/kent" ]]; then
    info path "$OPHOME/.config/kent: your personal Kent credentials (delete when no longer needed)"
fi
# polkit rules: /etc/polkit-1/rules.d is root:polkitd 0750, unreadable to a normal user.
if [[ -d "$R/etc/polkit-1/rules.d" ]]; then
    if [[ -r "$R/etc/polkit-1/rules.d" && -x "$R/etc/polkit-1/rules.d" ]]; then
        for p in "$R"/etc/polkit-1/rules.d/*kent*; do found path "${p#"$R"}"; done
    else unknown path "/etc/polkit-1/rules.d (needs root)"; fi
fi
shopt -u nullglob

# --- Firewall rules tagged by the gent module -----------------------------------------------
if ! skipped ufw; then
    rules="$R/etc/ufw/user.rules"
    if [[ -n "$R" ]]; then
        [[ -f "$rules" ]] && grep -q 'kent-gent' "$rules" && found ufw "rules mentioning kent-gent in /etc/ufw/user.rules"
    elif [[ -r /etc/ufw/user.rules ]]; then
        grep -q 'kent-gent' /etc/ufw/user.rules && found ufw "rules mentioning kent-gent (ufw status)"
    elif [[ -e /etc/ufw/user.rules ]]; then unknown ufw "ufw rules (needs root)"
    fi
fi

# --- Docker objects: label kent.module or kent-* names ------------------------------------
if ! skipped docker; then
    if ! command -v docker >/dev/null; then :
    elif ! docker info >/dev/null 2>&1; then unknown docker "docker not reachable (daemon down, or no access)"
    else
        while read -r o; do [[ -n "$o" ]] && found docker "container $o"; done < <(
            { docker ps -a --filter label=kent.module --format '{{.Names}}'
              docker ps -a --format '{{.Names}}' | grep -E '^kent-'; } 2>/dev/null | sort -u)
        while read -r o; do [[ -n "$o" ]] && found docker "image $o"; done < <(
            { docker images --filter label=kent.module --format '{{.Repository}}:{{.Tag}}'
              docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^kent-'; } 2>/dev/null | sort -u)
        while read -r o; do [[ -n "$o" ]] && found docker "network $o"; done < <(
            docker network ls --format '{{.Name}}' 2>/dev/null | grep -E '^kent-')
        while read -r o; do [[ -n "$o" ]] && found docker "volume $o"; done < <(
            { docker volume ls -q --filter label=kent.module; docker volume ls -q | grep -E '^kent-'; } 2>/dev/null | sort -u)
    fi
fi

# --- Ports Kent's services listen on (evidence only: something else may use them) ---------
if ! skipped ports && command -v ss >/dev/null; then
    listening="$(ss -ltnH 2>/dev/null | awk '{ print $4 }')"
    for port in "${PORTS[@]}"; do
        addr="$(grep -E ":${port}\$" <<<"$listening" | paste -sd, -)"
        [[ -n "$addr" ]] && warn port "$port in use ($addr): Kent's port; check what listens (ss -ltnp)"
    done
    addr="$(grep -E ':8080$' <<<"$listening" | paste -sd, -)"
    [[ -n "$addr" ]] && info port "8080 in use ($addr): the local-model port; fine if it is your own llama-server"
fi

if [[ $EUID -ne 0 && -z "$R" ]]; then
    unknown scope "ran as $(id -un): root-only places are not visible; run as root for full coverage"
fi
if [[ "$left" -eq 0 ]]; then echo "no Kent components found"; else echo "$n_left Kent component(s) left"; fi
exit "$left"
