#!/usr/bin/env bash
# =============================================================================
# Kent — install/dev/agent-sudo.sh
# Time-limited, path-limited passwordless sudo for unattended Kent build/test
# runs (install, uninstall, snapshot, test helpers under install/services).
#
#   sudo install/dev/agent-sudo.sh grant [HOURS]   # default 48
#   sudo install/dev/agent-sudo.sh revoke
#        install/dev/agent-sudo.sh status          # no sudo needed
#
# SECURITY: the allowed scripts live in a repo your account can write, so while
# the grant is active anything running as your user can effectively act as
# root through them. The path limit stops accidental/untargeted use, not a
# determined attacker in your account. Mitigations: automatic expiry (a
# persistent systemd timer removes the rule, surviving reboots), every sudo
# command is logged (journalctl _COMM=sudo), and revoke works at any time.
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
RULE=/etc/sudoers.d/90-kent-agent
UNIT=kent-agent-sudo-expire

status() {
    if [[ -f "$RULE" ]]; then
        echo "ACTIVE: $RULE"
        grep -E "^# Expires" "$RULE" || true
        systemctl list-timers "$UNIT.timer" --no-pager 2>/dev/null | sed -n '1,2p'
    else
        echo "not active (no $RULE)"
    fi
}

revoke() {
    rm -f "$RULE"
    systemctl disable --now "$UNIT.timer" 2>/dev/null || true
    rm -f "/etc/systemd/system/$UNIT.timer" "/etc/systemd/system/$UNIT.service"
    systemctl daemon-reload
    echo "revoked: passwordless sudo for the Kent agent removed"
}

grant() {
    local hours="${1:-48}"
    [[ "$hours" =~ ^[0-9]+$ && "$hours" -ge 1 && "$hours" -le 168 ]] || { echo "HOURS must be 1-168" >&2; exit 2; }
    local op="${SUDO_USER:?run via sudo from your own account}"
    [[ "$op" != root ]] || { echo "run via sudo from your own account" >&2; exit 2; }
    local expiry; expiry="$(date -d "+${hours} hours" '+%Y-%m-%d %H:%M:%S')"

    local tmp; tmp="$(mktemp)"
    cat > "$tmp" <<EOF
# Kent build agent: passwordless sudo ONLY for the repo's service scripts.
# Granted $(date '+%F %T') for $hours h by $op via install/dev/agent-sudo.sh
# Expires $expiry (removed by $UNIT.timer). Revoke: sudo $REPO/install/dev/agent-sudo.sh revoke
$op ALL=(root) NOPASSWD: $REPO/install/services/*.sh, $REPO/install/services/*/*.sh
EOF
    visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; echo "sudoers syntax check failed" >&2; exit 1; }
    install -m 0440 -o root -g root "$tmp" "$RULE"; rm -f "$tmp"

    # The expiry job must NOT execute anything from the (user-writable) repo as
    # root, so its commands are fixed here in a root-owned unit.
    cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=Expire Kent agent passwordless sudo
[Service]
Type=oneshot
ExecStart=/bin/rm -f $RULE
ExecStart=/bin/systemctl disable $UNIT.timer
ExecStart=/bin/rm -f /etc/systemd/system/$UNIT.timer /etc/systemd/system/$UNIT.service
ExecStart=/bin/systemctl daemon-reload
EOF
    cat > "/etc/systemd/system/$UNIT.timer" <<EOF
[Unit]
Description=Expire Kent agent passwordless sudo at $expiry
[Timer]
OnCalendar=$expiry
Persistent=true
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now "$UNIT.timer" >/dev/null
    echo "granted until $expiry: $op may run $REPO/install/services/{*.sh,*/*.sh} as root without a password"
    status
}

case "${1:-}" in
    grant)  [[ $EUID -eq 0 ]] || { echo "use: sudo $0 grant [HOURS]" >&2; exit 2; }; grant "${2:-48}" ;;
    revoke) [[ $EUID -eq 0 ]] || { echo "use: sudo $0 revoke" >&2; exit 2; }; revoke ;;
    status) status ;;
    *) sed -n '2,/^# =====/p' "$0" | sed '$d'; exit 2 ;;
esac
