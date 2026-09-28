#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/gitea/install.sh
# Gitea (pinned in ../versions.env; SHA-256 whose signature was GPG-verified):
#   account gitea:gitea     code /opt/kent-gitea     config /etc/kent/gitea/app.ini
#   state   /var/lib/gitea  unit kent-gitea.service  127.0.0.1:3000 (SQLite)
# Bootstrap (idempotent): admin "kentadmin" (password -> operator), service user
# "kent" + scoped token (-> operator, for Hermes), private repo kent/stack-template.
# Usage: sudo ./install.sh [--no-start] [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="gitea"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"
source "$HERE/../versions.env"

ACCOUNT=gitea; OPT=/opt/kent-gitea; CONF=/etc/kent/gitea; CRED=$CONF/credentials
UNIT=kent-gitea.service; PORT=3000; START=1
while [[ $# -gt 0 ]]; do
    case "$1" in --no-start) START=0; shift ;; --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac
done
export DRY_RUN; require_root
audit_event "install started ($*)"
OPERATOR="$(operator_user)"; OPERATOR_HOME="$(getent passwd "$OPERATOR" | cut -d: -f6)"; OKD="$OPERATOR_HOME/.config/kent"

log "preflight"
command -v git >/dev/null || die "git is required"
if ! port_free "$PORT" && ! systemctl is-active --quiet "$UNIT" 2>/dev/null; then die "port $PORT in use"; fi

ensure_service_account "$ACCOUNT" /var/lib/gitea "Gitea (Kent)"

claim_path path "$OPT"
ensure_dir "$OPT" 0755 root root
fetch_verified "$GITEA_URL" "$GITEA_SHA256" "/tmp/kent-gitea.$$"
if [[ "$DRY_RUN" -eq 0 ]]; then
    install -m 0755 -o root -g root "/tmp/kent-gitea.$$" "$OPT/gitea"; rm -f "/tmp/kent-gitea.$$"
    echo "$GITEA_VERSION" > "$OPT/VERSION"
fi

claim_path path "$CONF"
ensure_dir "$CONF" 0750 root "$ACCOUNT"
run install -d -m 0750 -o root -g "$ACCOUNT" "$CRED"
for s in secret_key internal_token jwt_secret lfs_jwt_secret; do
    if [[ ! -s "$CRED/$s" ]]; then
        if [[ "$DRY_RUN" -eq 1 ]]; then echo "  [dry-run] generate $CRED/$s (0640 root:$ACCOUNT)"
        else
            "$OPT/gitea" generate secret "${s^^}" | tr -d '\n' > "$CRED/$s"
            chown root:"$ACCOUNT" "$CRED/$s"; chmod 0640 "$CRED/$s"
        fi
    fi
done
place_file file "$KENT_ROOT/configs/gitea-app.ini" "$CONF/app.ini" 0640 root "$ACCOUNT"

manifest_add state /var/lib/gitea
place_file unit "$HERE/$UNIT" "/etc/systemd/system/$UNIT" 0644 root root
run systemctl daemon-reload
if [[ "$START" -eq 1 ]]; then run systemctl enable "$UNIT"; run systemctl restart "$UNIT"; fi
[[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]] || { log "done (not started)."; exit 0; }

# /api/healthz is Gitea's unauthenticated health endpoint (the API itself
# requires sign-in because REQUIRE_SIGNIN_VIEW = true).
for _ in $(seq 1 60); do curl -sf -m 2 "http://127.0.0.1:${PORT}/api/healthz" >/dev/null && break; sleep 2; done
curl -sf -m 2 "http://127.0.0.1:${PORT}/api/healthz" >/dev/null || die "gitea not healthy; see journalctl -u $UNIT"

# --- Bootstrap (idempotent) -------------------------------------------------
G() { runuser -u "$ACCOUNT" -- env HOME=/var/lib/gitea GITEA_WORK_DIR=/var/lib/gitea \
        "$OPT/gitea" --config "$CONF/app.ini" --work-path /var/lib/gitea "$@"; }
users="$(G admin user list 2>/dev/null | awk 'NR>1 {print $2}')"
ensure_dir "$OKD" 0700 "$OPERATOR" "$OPERATOR" "$OPERATOR"

if ! grep -qx kentadmin <<<"$users"; then
    out="$(G admin user create --username kentadmin --email kentadmin@localhost --admin \
            --random-password --random-password-length 32 --must-change-password=false 2>&1)"
    pw="$(sed -n "s/.*generated random password is '\(.*\)'.*/\1/p" <<<"$out")"
    [[ -n "$pw" ]] || die "could not create kentadmin: $out"
    (umask 077; printf '%s' "$pw" > "$CRED/admin_password"); unset pw
fi
if ! grep -qx kent <<<"$users"; then
    G admin user create --username kent --email kent@localhost --random-password \
        --random-password-length 32 --must-change-password=false >/dev/null 2>&1 || die "could not create user kent"
fi
if [[ ! -s "$CRED/kent_token" ]]; then
    tok="$(G admin user generate-access-token --username kent --token-name kent-agent \
            --scopes 'write:repository,write:issue,read:user' --raw 2>/dev/null | tail -1)"
    [[ "$tok" =~ ^[0-9a-f]{40}$ ]] || die "could not create kent token"
    (umask 077; printf '%s' "$tok" > "$CRED/kent_token"); unset tok
fi
# The operator gets the admin password; Kent's token stays with Kent (copied by kent-core).
dst="$OKD/gitea_admin_password"
claim_path file "$dst"
install -m 0600 -o "$OPERATOR" -g "$OPERATOR" "$CRED/admin_password" "$dst"
retire_file "$OKD/gitea_kent_token"

TOK="$(cat "$CRED/kent_token")"
gapi() { curl -s -m 10 -H "Authorization: token ${TOK}" -H 'Content-Type: application/json' "$@"; }
# Created with admin rights on kent's behalf, so kent's token never needs the
# broader write:user scope that POST /user/repos requires.
if [[ "$(gapi -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/v1/repos/kent/stack-template")" != "200" ]]; then
    curl -s -m 10 -u "kentadmin:$(cat "$CRED/admin_password")" -H 'Content-Type: application/json' \
        -X POST "http://127.0.0.1:${PORT}/api/v1/admin/users/kent/repos" \
        -d '{"name":"stack-template","private":true,"auto_init":true,"default_branch":"main","description":"Kent Gent stack template"}' \
        | jq -e '.full_name == "kent/stack-template"' >/dev/null || die "could not create kent/stack-template"
fi

# --- Verify -------------------------------------------------------------------
[[ "$(gapi "http://127.0.0.1:${PORT}/api/v1/user" | jq -r .login)" == "kent" ]] || die "kent token not accepted"
[[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/v1/repos/kent/stack-template")" =~ ^(401|403|404)$ ]] \
    || die "private repo visible anonymously"
unset TOK
log "OK: gitea $("$OPT/gitea" --version | awk '{print $3}') on 127.0.0.1:${PORT}; kentadmin + kent (token) ready; kent/stack-template private"
audit_event "install completed"
log "done."
