#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/gent/install.sh
# The Gent runtime: isolated CrewAI project teams that Kent spawns on demand.
#
#   network  kent-gent-net (Docker, --internal: no route to internet or LAN)
#            172.30.0.0/24, host side 172.30.0.1 on bridge kent-gent0
#   egress   kent-squid.service  own Squid instance, account kent-squid:kent-squid,
#            127.0.0.1:3129, GET/HEAD + CONNECT:443 to public addresses only
#            (the vendor squid.service is neither used nor modified)
#   bridges  kent-gent-egress.socket   172.30.0.1:3129 -> 127.0.0.1:3129 (proxy)
#            kent-gent-gateway.socket  172.30.0.1:4000 -> 127.0.0.1:4000 (LiteLLM)
#            systemd-socket-proxyd, DynamicUser; the ONLY host services a Gent can reach
#   firewall if UFW is active: two allow rules, inbound on kent-gent0 to exactly
#            those two ports (removed on uninstall)
#   image    kent-gent:current  (pinned base digest, hash-locked deps, see image/)
#   tools    /opt/kent-gent/bin/kent-{spawn,destroy}-gent, run by the operator via
#            /etc/sudoers.d/91-kent-gent (exactly those two commands, NOPASSWD)
#   state    /var/lib/kent-gent/{stacks,keys,registry,archive}
#   keys     /etc/kent/litellm/gent-keys/<id>.key  (read by the gateway; identity gent-<id>)
#
# Per-Gent accounts (gent-<id>) are created and removed by the spawn/destroy
# tools, never by this installer; uninstall destroys every registered Gent first.
# Requires: docker, the litellm and gitea modules, squid binary (package squid).
# Usage: sudo ./install.sh [--no-start] [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="gent"
HERE="$(cd "$(dirname "$0")" && pwd)"
KENT_ROOT="$(cd "$HERE/../../.." && pwd)"
source "$HERE/../lib-service.sh"

NET=kent-gent-net; SUBNET=172.30.0.0/24; GW_IP=172.30.0.1; BRIDGE=kent-gent0
OPT=/opt/kent-gent; STATE=/var/lib/kent-gent; SQUID_CONF=/etc/kent/squid
GW_KEYS=/etc/kent/litellm/gent-keys; SUDOERS=/etc/sudoers.d/91-kent-gent
IMAGE=kent-gent; START=1
UNITS=(kent-squid.service kent-gent-egress.socket kent-gent-egress.service kent-gent-gateway.socket kent-gent-gateway.service)
while [[ $# -gt 0 ]]; do
    case "$1" in --no-start) START=0; shift ;; --dry-run) DRY_RUN=1; shift ;; *) die "unknown option: $1" ;; esac
done
export DRY_RUN; require_root
OP="$(operator_user)"
audit_event "install started ($*)"

log "preflight"
systemctl is-active --quiet docker || die "docker is not running"
getent group litellm >/dev/null || die "litellm module not installed (group litellm missing)"
[[ -d /etc/kent/litellm ]] || die "litellm module not installed"
[[ -s /etc/kent/gitea/credentials/admin_password ]] || die "gitea module not installed"
[[ -x /usr/sbin/squid ]] || ensure_package squid
[[ -x /usr/lib/systemd/systemd-socket-proxyd ]] || die "systemd-socket-proxyd missing"
# Only the loopback listener matters (the bridge socket on 172.30.0.1:3129 is ours).
if ss -ltnH 'src 127.0.0.1:3129' | grep -q . && ! systemctl is-active --quiet kent-squid 2>/dev/null \
   && ! manifest_has unit /etc/systemd/system/kent-squid.service; then die "127.0.0.1:3129 in use"; fi

# --- Egress proxy -------------------------------------------------------------
ensure_service_account kent-squid /var/lib/kent-squid "Kent Gent egress proxy"
claim_path path "$SQUID_CONF"
ensure_dir "$SQUID_CONF" 0750 root kent-squid
place_file file "$KENT_ROOT/configs/kent-squid.conf" "$SQUID_CONF/squid.conf" 0640 root kent-squid
[[ "$DRY_RUN" -eq 1 ]] || /usr/sbin/squid -k parse -f "$SQUID_CONF/squid.conf" 2>&1 | grep -qE "FATAL|ERROR" \
    && die "squid rejected $SQUID_CONF/squid.conf" || true
manifest_add state /var/lib/kent-squid
manifest_add state /var/log/kent-squid

# --- State, keys, tools ---------------------------------------------------------
ensure_dir "$STATE" 0755 root root
for d in stacks:0755 keys:0711 registry:0700 archive:0755; do ensure_dir "$STATE/${d%%:*}" "${d##*:}" root root; done
manifest_add state "$STATE"
ensure_dir "$GW_KEYS" 0750 root litellm

claim_path path "$OPT"
ensure_dir "$OPT" 0755 root root
ensure_dir "$OPT/bin" 0755 root root
ensure_dir "$OPT/share" 0755 root root
for t in kent-spawn-gent kent-destroy-gent; do
    place_file file "$HERE/bin/$t" "$OPT/bin/$t" 0755 root root
done
place_file file "$KENT_ROOT/schemas/stack.sql" "$OPT/share/stack.sql" 0644 root root

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cat > "$WORK/sudoers" <<EOF
# Kent: the operator (and Kent, running as the operator) may spawn and destroy
# Gents. Both tools are root-owned and validate every argument. Installed by
# install/services/gent/install.sh; removed by its uninstall.
$OP ALL=(root) NOPASSWD: $OPT/bin/kent-spawn-gent, $OPT/bin/kent-destroy-gent
EOF
visudo -cf "$WORK/sudoers" >/dev/null || die "sudoers syntax check failed"
place_file file "$WORK/sudoers" "$SUDOERS" 0440 root root

# --- Network ----------------------------------------------------------------------
if docker network inspect "$NET" >/dev/null 2>&1; then
    manifest_has dockernet "$NET" || die "docker network $NET exists and was not created by Kent"
else
    run docker network create --internal --driver bridge --subnet "$SUBNET" --gateway "$GW_IP" \
        -o "com.docker.network.bridge.name=$BRIDGE" --label kent.module=gent "$NET" >/dev/null
fi
manifest_add dockernet "$NET"

# --- Image --------------------------------------------------------------------------
mkdir -p "$WORK/ctx"
cp "$HERE/image/Dockerfile" "$HERE/image/requirements.lock" "$WORK/ctx/"
cp -r "$KENT_ROOT/templates/app/gent" "$WORK/ctx/gent"
rm -rf "$WORK/ctx/gent/__pycache__"
TAG="$(cd "$WORK/ctx" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-12)"
BASE="$(awk '/^FROM /{print $2; exit}' "$WORK/ctx/Dockerfile")"
if ! docker image inspect "$BASE" >/dev/null 2>&1; then
    run docker pull -q "$BASE" >/dev/null
    manifest_add dockerbase "$BASE"          # pulled by Kent → removed on uninstall
fi
log "building image $IMAGE:$TAG"
if [[ "$DRY_RUN" -eq 1 ]]; then
    run docker build -q --label kent.module=gent -t "$IMAGE:$TAG" -t "$IMAGE:current" "$WORK/ctx"
elif ! docker build --progress=plain --label kent.module=gent -t "$IMAGE:$TAG" -t "$IMAGE:current" "$WORK/ctx" \
        > "$WORK/build.log" 2>&1; then
    tail -30 "$WORK/build.log" >&2
    die "image build failed (last 30 lines above)"
fi
manifest_add dockerimage "$IMAGE"
# Drop superseded builds (a tag still used by a running Gent is kept: docker refuses).
if [[ "$DRY_RUN" -eq 0 ]]; then
    # (grep finds nothing on a fresh install: must not trip set -e/pipefail)
    { docker images --format '{{.Repository}}:{{.Tag}}' "$IMAGE" | grep -vxF -e "$IMAGE:$TAG" -e "$IMAGE:current" || true; } \
        | while read -r old; do docker rmi "$old" >/dev/null 2>&1 && log "removed superseded image $old" || true; done
fi

# --- Units --------------------------------------------------------------------------
for u in "${UNITS[@]}"; do place_file unit "$HERE/units/$u" "/etc/systemd/system/$u" 0644 root root; done
run systemctl daemon-reload

# --- Firewall (only if UFW is active; default INPUT policy drops bridge traffic) ------
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
    for port in 3129 4000; do
        rule="allow in on $BRIDGE to $GW_IP port $port proto tcp"
        run ufw $rule comment "kent-gent" >/dev/null
        manifest_add ufwrule "$rule"
    done
else
    log "UFW not active; no firewall rules needed"
fi

if [[ "$START" -eq 1 ]]; then
    run systemctl enable --now kent-squid.service kent-gent-egress.socket kent-gent-gateway.socket
    run systemctl restart kent-squid.service
fi

# --- Verify ---------------------------------------------------------------------------
if [[ "$START" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    for _ in $(seq 1 15); do ss -ltnH 'src 127.0.0.1:3129' | grep -q . && break; sleep 1; done
    code() { curl -s -o /dev/null -m 15 -w '%{http_code}' "$@" || true; }
    [[ "$(code -x http://127.0.0.1:3129 https://example.com/)" == 200 ]] || die "proxy: public HTTPS fetch failed"
    [[ "$(code -x http://127.0.0.1:3129 http://127.0.0.1:4000/health)" == 403 ]] || die "proxy: loopback destination NOT refused"
    [[ "$(code -x http://127.0.0.1:3129 http://192.168.1.1/)" == 403 ]] || die "proxy: LAN destination NOT refused"
    # From inside the Gent network, as an unprivileged user with the Gent image.
    probe="$(docker run --rm --network "$NET" --user 65534:65534 --read-only --cap-drop ALL \
        --security-opt no-new-privileges --entrypoint python "$IMAGE:current" -c '
import requests, socket
def st(f):
    try: return str(f())
    except Exception as e: return type(e).__name__
out = {}
out["direct_internet"] = st(lambda: socket.create_connection(("1.1.1.1", 443), timeout=5) and "OPEN")
out["gateway_nokey"] = st(lambda: requests.get("http://172.30.0.1:4000/v1/models", timeout=10).status_code)
P = {"http": "http://172.30.0.1:3129", "https": "http://172.30.0.1:3129"}
out["proxy_web"] = st(lambda: requests.get("https://example.com/", proxies=P, timeout=20).status_code)
out["proxy_gitea"] = st(lambda: requests.get("http://127.0.0.1:3000/", proxies=P, timeout=10).status_code)
out["host_gitea"] = st(lambda: socket.create_connection(("172.30.0.1", 3000), timeout=5) and "OPEN")
print(out)' 2>&1 | tail -1)"
    log "isolation probe: $probe"
    [[ "$probe" == *"'direct_internet': 'OSError'"* || "$probe" == *"'direct_internet': 'TimeoutError'"* ]] \
        || die "Gent network reaches the internet directly: $probe"
    [[ "$probe" == *"'gateway_nokey': '401'"* ]] || die "Gent network cannot reach the gateway bridge: $probe"
    [[ "$probe" == *"'proxy_web': '200'"* ]] || die "Gent network cannot use the egress proxy: $probe"
    [[ "$probe" == *"'proxy_gitea': '403'"* ]] || die "proxy lets a Gent reach host services: $probe"
    [[ "$probe" != *"'host_gitea': 'OPEN'"* ]] || die "Gent network reaches host port 3000 directly: $probe"
    log "OK: Gent network isolated (no direct egress, gateway + proxy only)"
fi
audit_event "install completed"
log "done. Spawn Gents with: kent-gent spawn (Kent) or sudo $OPT/bin/kent-spawn-gent <id> <project_dir>"
