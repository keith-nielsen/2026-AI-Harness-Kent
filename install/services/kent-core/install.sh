#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/kent-core/install.sh
# Kent itself (the operator-level agent), on top of the user's Hermes install:
#   tools    /opt/kent-core/bin (root-owned; audit chain, digest, learnings/
#            escalation relay, QA audit) + launchers in ~/.local/bin
#   config   ~/.config/kent/kent.conf, audit HMAC secret
#   data     ~/.local/share/kent/{kent.db, audit/, digests/, stacks/}
#   timers   systemd --user: audit-ingest & poll-learnings (5 min), digest 07:00,
#            qa-audit 02:00, audit-anchor 04:30
#   hermes   profile "kent": SOUL.md, crew-designer skill, gateway wiring
#            (prior SOUL.md and config.yaml moved aside, restored on uninstall)
# Requires: litellm (keys in ~/.config/kent) and gitea (kent token) modules.
# Usage: sudo ./install.sh [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="kent-core"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"

while [[ $# -gt 0 ]]; do case "$1" in --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac; done
export DRY_RUN; require_root
audit_event "install started"
OP="$(operator_user)"; OH="$(getent passwd "$OP" | cut -d: -f6)"; OG="$(id -gn "$OP")"
CFG="$OH/.config/kent"; DATA="$OH/.local/share/kent"; BIN="$OH/.local/bin"; UNITS="$OH/.config/systemd/user"
PROFILE="$OH/.hermes/profiles/kent"; OPT=/opt/kent-core

log "preflight"
command -v hermes >/dev/null 2>&1 || runuser -u "$OP" -- bash -lc 'command -v hermes' >/dev/null || die "Hermes not found for $OP (prerequisite)"
[[ -d "$PROFILE" ]] || die "Hermes profile 'kent' missing: run as $OP: hermes profile create kent --no-skills"
[[ -s "$CFG/litellm_kent_key" ]] || die "LiteLLM kent key missing: install the litellm module first"
[[ -s "$CFG/gitea_kent_token" ]] || die "Gitea kent token missing: install the gitea module first"
[[ "$(loginctl show-user "$OP" -p Linger --value 2>/dev/null)" == "yes" ]] \
    || warn "linger is off for $OP: Kent's timers only run while $OP is logged in"

# --- Tools (root-owned) -------------------------------------------------------
claim_path path "$OPT"
ensure_dir "$OPT/bin" 0755 root root
for f in "$HERE"/bin/*.py; do run install -m 0755 -o root -g root "$f" "$OPT/bin/"; done
for f in "$KENT_ROOT/schemas/kent.sql" "$KENT_ROOT/schemas/stack.sql"; do run install -m 0644 -o root -g root "$f" "$OPT/"; done

# --- Operator config + secrets ------------------------------------------------
ensure_dir "$CFG" 0700 "$OP" "$OG" "$OP"
TMP="$(mktemp)"; cat > "$TMP" <<CONF
# Kent configuration — written by install/services/kent-core/install.sh
KENT_DATA=$DATA
KENT_DB=$DATA/kent.db
AUDIT_LOG=$DATA/audit/hmac_chain.log
AUDIT_SECRET=$CFG/audit_hmac_secret
STACKS_DIR=/var/lib/kent-gent/stacks
GENT_ARCHIVE_DIR=/var/lib/kent-gent/archive
GENT_BIN=/opt/kent-gent/bin
LOKI_URL=http://127.0.0.1:3100
PROM_URL=http://127.0.0.1:9090
GATEWAY_URL=http://127.0.0.1:4000/v1
GATEWAY_KEY_FILE=$CFG/litellm_kent_key
GITEA_URL=http://127.0.0.1:3000
GITEA_TOKEN_FILE=$CFG/gitea_kent_token
TEMPLATE_REPO=kent/stack-template
CONF
place_file file "$TMP" "$CFG/kent.conf" 0600 "$OP" "$OG"; rm -f "$TMP"
if [[ ! -s "$CFG/audit_hmac_secret" ]]; then
    claim_path file "$CFG/audit_hmac_secret"
    [[ "$DRY_RUN" -eq 1 ]] || { (umask 077; openssl rand -hex 32 > "$CFG/audit_hmac_secret"); chown "$OP:$OG" "$CFG/audit_hmac_secret"; }
fi

# --- Data (state: removed only with --purge-state) ------------------------------
ensure_dir "$DATA" 0700 "$OP" "$OG" "$OP"
for d in audit digests; do run install -d -m 0700 -o "$OP" -g "$OG" "$DATA/$d"; done
manifest_add state "$DATA/kent.db"; manifest_add state "$DATA/audit"; manifest_add state "$DATA/digests"
[[ "$DRY_RUN" -eq 1 ]] || runuser -u "$OP" -- sqlite3 "$DATA/kent.db" < "$KENT_ROOT/schemas/kent.sql"
[[ "$DRY_RUN" -eq 1 ]] || chmod 0600 "$DATA/kent.db"

# --- Launchers -----------------------------------------------------------------
ensure_dir "$BIN" 0755 "$OP" "$OG" "$OP"
launcher() {  # launcher <name> <command...>
    local t; t="$(mktemp)"
    printf '#!/bin/sh\n# Kent launcher (installed by kent-core; do not edit)\nexec %s "$@"\n' "$2" > "$t"
    place_file file "$t" "$BIN/$1" 0755 "$OP" "$OG"; rm -f "$t"
}
launcher kent "hermes -p kent"
launcher kent-audit "/usr/bin/python3 $OPT/bin/kent_audit.py"
launcher kent-digest "/usr/bin/python3 $OPT/bin/kent_digest.py"
launcher kent-poll-learnings "/usr/bin/python3 $OPT/bin/kent_poll_learnings.py"
launcher kent-qa-audit "/usr/bin/python3 $OPT/bin/kent_qa_audit.py"
launcher kent-gent "/usr/bin/python3 $OPT/bin/kent_gent.py"

# --- Hermes profile --------------------------------------------------------------
move_aside "$PROFILE/SOUL.md"
place_file file "$KENT_ROOT/templates/app/SOUL.md" "$PROFILE/SOUL.md" 0644 "$OP" "$OG"
claim_path path "$PROFILE/skills/kent"
ensure_dir "$PROFILE/skills/kent" 0755 "$OP" "$OG" "$OP"
run cp -r "$KENT_ROOT/skills/kent/crew-designer" "$PROFILE/skills/kent/"
run chown -R "$OP:$OG" "$PROFILE/skills/kent"
# Gateway wiring: keep the prior config for uninstall, then configure in place.
if ! grep -q "^moved $PROFILE/config.yaml " "$(manifest_file)" 2>/dev/null; then
    move_aside "$PROFILE/config.yaml"
    bak="$(awk -v o="$PROFILE/config.yaml" '$1=="moved" && $2==o {print $3}' "$(manifest_file)" 2>/dev/null)"
    [[ "$DRY_RUN" -eq 1 ]] || cp -p "$bak" "$PROFILE/config.yaml"
fi
[[ "$DRY_RUN" -eq 1 ]] || runuser -u "$OP" -- env HOME="$OH" PATH="$OH/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    "$KENT_ROOT/install/services/litellm/configure-hermes.sh" >/dev/null
[[ "$DRY_RUN" -eq 1 ]] || find "$PROFILE" -maxdepth 1 -name 'config.yaml.bak-*' -newer "$OPT/bin/kentlib.py" -delete

# --- User timers -------------------------------------------------------------------
ensure_dir "$UNITS" 0755 "$OP" "$OG" "$OP"
for f in "$HERE"/systemd-user/*; do
    dest="$UNITS/$(basename "$f")"
    if [[ -e "$dest" ]] && ! manifest_has userunit "$OP $dest"; then die "$dest exists and was not created by Kent"; fi
    manifest_add userunit "$OP $dest"
    run install -m 0644 -o "$OP" -g "$OG" "$f" "$dest"
done
run systemctl --user -M "${OP}@" daemon-reload
for t in "$HERE"/systemd-user/*.timer; do run systemctl --user -M "${OP}@" enable --now "$(basename "$t")"; done

# --- Verify ------------------------------------------------------------------------
if [[ "$DRY_RUN" -eq 0 ]]; then
    RU() { runuser -u "$OP" -- env HOME="$OH" KENT_CONF="$CFG/kent.conf" "$@"; }
    RU /usr/bin/python3 "$OPT/bin/kent_audit.py" append kent-core install "kent-core installed" \
        || die "audit chain append failed"
    # Verify and anchor right away (on every install), so the chain is protected from
    # day one rather than only after the first scheduled 04:30 anchor.
    RU /usr/bin/python3 "$OPT/bin/kent_audit.py" anchor >/dev/null || die "audit chain does not verify"
    RU /usr/bin/python3 "$OPT/bin/kent_audit_ingest.py" >/dev/null || die "audit ingest failed"
    RU /usr/bin/python3 "$OPT/bin/kent_digest.py" >/dev/null || die "digest failed"
    n=$(systemctl --user -M "${OP}@" list-timers 'kent-*' --no-legend 2>/dev/null | grep -c kent- || true)
    [[ "$n" -ge 5 ]] || die "expected 5 kent timers, found $n"
    grep -q "Estate Manager" "$PROFILE/SOUL.md" || die "Kent SOUL.md not in place"
    log "OK: kent-core installed; audit chain valid; digest written; $n timers active"
fi
audit_event "install completed"
log "done."
