#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/kent-core/install.sh
# Kent's tools, data and schedules, running as the kent account (created by the hermes module):
#   tools    /opt/kent-core/bin (audit chain, digest, learnings/escalation relay + circuit
#            breaker, QA audit, kent-gent) and the human-facing `kent` command
#            (/usr/local/bin/kent → /opt/kent-core/bin/kent)
#   broker   /opt/kent-core/libexec/kent-broker (kent-broker.socket, /run/kent-broker.sock 0600 kent):
#            runs Kent's own tools outside Hermes's sandbox; kent-broker-client stands in for them inside
#   entry    /opt/kent-core/libexec/kent-exec — Kent's side of `kent`, reachable only through
#            /etc/sudoers.d/92-kent-operators (%kent-operators may run it as kent, nothing else)
#   config   /etc/kent/kent/kent.conf, credentials/ (Kent's gateway key and Gitea token, copied
#            from the litellm and gitea modules; audit HMAC secret)   root:kent 0640
#   data     /var/lib/kent/{kent.db, audit/, digests/, inbox/}   kent-owned
#   timers   system units as kent: poll-learnings (1 min), audit-ingest (5 min), digest 07:00,
#            qa-audit 02:00, audit-anchor 04:30
# Also enrols the invoking human into kent-operators (single-operator setup).
# Requires: litellm, gitea and hermes modules.
# Usage: sudo ./install.sh [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="kent-core"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"

while [[ $# -gt 0 ]]; do case "$1" in --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac; done
export DRY_RUN; require_root
audit_event "install started (${INVOCATION_ARGS})"
OP="$(operator_user)"
OPT=/opt/kent-core; ETC=/etc/kent/kent; CRED=$ETC/credentials; DATA=/var/lib/kent
UNITS=(kent-audit-anchor kent-audit-ingest kent-digest kent-poll-learnings kent-qa-audit)

log "preflight"
need 'getent passwd kent >/dev/null' "hermes module not installed (kent account missing)"
need 'getent group kent-operators >/dev/null' "hermes module not installed (kent-operators group missing)"
need '[[ -s /etc/kent/litellm/credentials/kent_key ]]' "litellm module not installed (Kent's gateway key missing)"
need '[[ -s /etc/kent/gitea/credentials/kent_token ]]' "gitea module not installed (Kent's Gitea token missing)"

# --- Tools and entry points (root-owned) ----------------------------------------------------
claim_path path "$OPT"
ensure_dir "$OPT" 0755 root root
ensure_dir "$OPT/bin" 0755 root root
ensure_dir "$OPT/libexec" 0755 root root
for f in "$HERE"/bin/*.py "$HERE/bin/kent"; do run install -m 0755 -o root -g root "$f" "$OPT/bin/"; done
for f in kent-exec kent-broker kent-broker-client; do run install -m 0755 -o root -g root "$HERE/libexec/$f" "$OPT/libexec/$f"; done
for f in "$KENT_ROOT/schemas/kent.sql" "$KENT_ROOT/schemas/stack.sql"; do run install -m 0644 -o root -g root "$f" "$OPT/"; done
# Command names Kent itself uses (its SOUL and skills refer to these; its PATH has $OPT/bin).
for t in gent:kent_gent audit:kent_audit digest:kent_digest poll-learnings:kent_poll_learnings qa-audit:kent_qa_audit; do
    run ln -sfn "${t#*:}.py" "$OPT/bin/kent-${t%%:*}"
done
if [[ -L /usr/local/bin/kent || ! -e /usr/local/bin/kent ]]; then
    claim_path file /usr/local/bin/kent
    run ln -sfn "$OPT/bin/kent" /usr/local/bin/kent
else
    die "/usr/local/bin/kent exists and is not Kent's symlink"
fi

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cat > "$WORK/sudoers" <<EOF
# Kent: members of kent-operators may run Kent's entry point as the kent account, and nothing
# else. kent-exec validates its arguments and records the calling human in the audit chain.
# Installed by install/services/kent-core/install.sh; removed by its uninstall.
%kent-operators ALL=(kent) NOPASSWD: $OPT/libexec/kent-exec
# sudo -C: the kent command hands Kent the folders an operator grants as open descriptors
# (kent --grant DIR); kent-exec checks each and closes every other descriptor.
Defaults!$OPT/libexec/kent-exec closefrom_override
EOF
visudo -cf "$WORK/sudoers" >/dev/null || die "sudoers syntax check failed"
place_file file "$WORK/sudoers" /etc/sudoers.d/92-kent-operators 0440 root root

# --- Configuration and credentials (root-owned, readable by kent) ---------------------------
claim_path path "$ETC"
ensure_dir "$ETC" 0750 root kent
ensure_dir "$CRED" 0750 root kent
cat > "$WORK/kent.conf" <<CONF
# Kent configuration — written by install/services/kent-core/install.sh
KENT_DATA=$DATA
KENT_DB=$DATA/kent.db
AUDIT_LOG=$DATA/audit/hmac_chain.log
AUDIT_SECRET=$CRED/audit_hmac_secret
STACKS_DIR=/var/lib/kent-gent/stacks
GENT_ARCHIVE_DIR=/var/lib/kent-gent/archive
GENT_BIN=/opt/kent-gent/bin
LOKI_URL=http://127.0.0.1:3100
PROM_URL=http://127.0.0.1:9090
GATEWAY_URL=http://127.0.0.1:4000/v1
GATEWAY_KEY_FILE=$CRED/litellm_kent_key
GITEA_URL=http://127.0.0.1:3000
GITEA_TOKEN_FILE=$CRED/gitea_kent_token
TEMPLATE_REPO=kent/stack-template
# 1: when a Gent finishes, Kent assesses it (one frontier call) and puts the verdict in the notice
AUTO_ASSESS=1
CONF
place_file file "$WORK/kent.conf" "$ETC/kent.conf" 0640 root kent
place_file file /etc/kent/litellm/credentials/kent_key "$CRED/litellm_kent_key" 0640 root kent
place_file file /etc/kent/gitea/credentials/kent_token "$CRED/gitea_kent_token" 0640 root kent
if [[ ! -s "$CRED/audit_hmac_secret" ]]; then
    [[ "$DRY_RUN" -eq 1 ]] || (umask 077; openssl rand -hex 32 > "$WORK/secret")
    place_file file "$WORK/secret" "$CRED/audit_hmac_secret" 0640 root kent
fi

# --- Data (kept on uninstall unless --purge-state) ----------------------------------------------
ensure_dir "$DATA" 0750 kent kent
for d in audit:0700 digests:0750 inbox:0700; do ensure_dir "$DATA/${d%%:*}" "${d##*:}" kent kent; done
for s in kent.db audit digests inbox; do manifest_add state "$DATA/$s"; done
if [[ "$DRY_RUN" -eq 0 ]]; then
    runuser -u kent -- sqlite3 "$DATA/kent.db" < "$KENT_ROOT/schemas/kent.sql"
    chmod 0600 "$DATA/kent.db"
fi

# --- Schedules (system units, run as kent) ---------------------------------------------------------
for u in "${UNITS[@]}"; do
    for ext in service timer; do place_file unit "$HERE/systemd/$u.$ext" "/etc/systemd/system/$u.$ext" 0644 root root; done
done
run systemctl daemon-reload
# enable, then restart: an already-active timer keeps its old schedule until restarted.
for u in "${UNITS[@]}"; do run systemctl enable "$u.timer"; run systemctl restart "$u.timer"; done

# --- Broker: Kent's own tools, run outside Hermes's sandbox (docs/design/kent-sandbox.md §4) -------
for u in kent-broker.socket kent-broker@.service; do place_file unit "$HERE/systemd/$u" "/etc/systemd/system/$u" 0644 root root; done
run systemctl daemon-reload
run systemctl enable kent-broker.socket
run systemctl restart kent-broker.socket
run rm -rf /run/kent-broker   # socket directory of the first draft (root-owned, unreachable for kent)

# --- Operator (single-operator setup: the human who ran the installer) ----------------------------
if ! id -nG "$OP" | tr ' ' '\n' | grep -qx kent-operators; then
    run usermod -aG kent-operators "$OP"
    manifest_add member "kent-operators $OP"
    log "added $OP to kent-operators (takes effect at next login, or: newgrp kent-operators)"
fi
# Grants (kent --grant DIR, docs/design/kent-sandbox.md §5): the kent account may pass through the
# operator's home to a granted folder, never list or read the home itself (ACL u:kent:--x).
OPHOME="$(getent passwd "$OP" | cut -d: -f6)"
if [[ -d "$OPHOME" && "$OPHOME" != / ]] && ! getfacl -cp "$OPHOME" 2>/dev/null | grep -qx 'user:kent:--x'; then
    run setfacl -m u:kent:x "$OPHOME"
    manifest_add acl "kent $OPHOME"
    log "kent may pass through $OPHOME to folders you grant (not list it)"
fi

# --- Verify ------------------------------------------------------------------------------------------
if [[ "$DRY_RUN" -eq 0 ]]; then
    RU() { runuser -u kent -- env KENT_CONF="$ETC/kent.conf" HOME="$DATA" "$@"; }
    RU /usr/bin/python3 "$OPT/bin/kent_audit.py" append kent-core install "kent-core installed" \
        || die "audit chain append failed"
    RU /usr/bin/python3 "$OPT/bin/kent_audit.py" anchor >/dev/null || die "audit chain does not verify"
    RU /usr/bin/python3 "$OPT/bin/kent_audit_ingest.py" >/dev/null || die "audit ingest failed"
    RU /usr/bin/python3 "$OPT/bin/kent_digest.py" >/dev/null || die "digest failed"
    armed() {
        local c=0 u
        for u in "${UNITS[@]}"; do
            if [[ "$(systemctl show "$u.timer" -p NextElapseUSecRealtime --value 2>/dev/null)" =~ [0-9] ]] \
               || systemctl is-active --quiet "$u.service"; then c=$((c + 1)); fi
        done
        echo "$c"
    }
    for _ in $(seq 1 30); do n="$(armed)"; [[ "$n" -eq 5 ]] && break; sleep 2; done
    [[ "$n" -ge 5 ]] || die "expected 5 armed kent timers, found $n"
    # As the operator (a fresh login session picks up the new group membership):
    runuser -u "$OP" -- /usr/local/bin/kent status >/dev/null || die "'kent status' failed for $OP"
    # The broker answers Kent the way the sandbox does (the client under a tool's name) and refuses
    # what is not on its list.
    T="$(runuser -u kent -- mktemp -d "$DATA/work/broker-check.XXXXXX")"
    runuser -u kent -- ln -s "$OPT/libexec/kent-broker-client" "$T/kent-audit"
    b="$(cd "$T" && runuser -u kent -- "$T/kent-audit" verify 2>&1)" || { rm -rf "$T"; die "kent-broker did not answer (got: ${b:0:160})"; }
    r="$(cd "$T" && runuser -u kent -- "$T/kent-audit" anchor 2>&1)" && { rm -rf "$T"; die "kent-broker accepted a command it must refuse"; }
    rm -rf "$T"
    [[ "$r" == *kent-broker:* ]] || die "kent-broker refusal not reported (got: ${r:0:160})"
    log "OK: kent-core installed; audit chain valid; digest written; $n timers armed; broker answers; 'kent' works for $OP"
fi
audit_event "install completed"
log "done."
