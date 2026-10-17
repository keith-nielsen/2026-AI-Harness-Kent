#!/usr/bin/env bash
# stratanet.sh up LOGDIR | down | status — Strata's locked-down network (operator, 2026-10-07: no internet for the
# model; log whatever it tries to reach).
#   strata-net    --internal 172.30.99.0/24: no route out. Strata sits at 172.30.99.10 (stratarun.sh).
#   strata-sink   172.30.99.53, the only DNS server Strata knows (STRATA_RESOLV, mounted over /etc/resolv.conf);
#                 logs every DNS query and every TCP connection that follows (sink.py) -> LOGDIR/sink.jsonl.
#                 NET_ADMIN only for its own iptables redirect; it is on strata-net alone, so it has no way out either.
#   strata-relay  socat, the one way in: host 127.0.0.1:18080 -> 172.30.99.10:8080. On strata-net and strata-front
#                 (a normal bridge, only to publish the port on loopback); unprivileged, read-only.
#   Limit: a connection to a literal IP (no DNS) fails at once with "network unreachable" and is not logged.
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
IMG=strata-netaux:1
NET=strata-net FRONT=strata-front SUBNET=172.30.99.0/24 SINK_IP=172.30.99.53 STRATA_IP=172.30.99.10
STRATA_RESOLV=$HOME/Documents/repo/bench/strata-resolv.conf
HARDEN=(--cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 256m)

up() {
  local logdir=${1:?usage: stratanet.sh up LOGDIR}
  mkdir -p "$logdir"
  setfacl -m u:0:rwx "$logdir"   # the sink's root has no capabilities (no DAC override): it may write here only
  docker image inspect "$IMG" >/dev/null 2>&1 || docker build -q -t "$IMG" "$D/strata-net" >/dev/null
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal --subnet "$SUBNET" "$NET" >/dev/null
  [[ $(docker network inspect -f '{{.Internal}}' "$NET") == true ]] || { echo "stratanet: $NET is not internal" >&2; exit 1; }
  docker network inspect "$FRONT" >/dev/null 2>&1 || docker network create "$FRONT" >/dev/null
  printf 'nameserver %s\noptions timeout:1 attempts:1\n' "$SINK_IP" > "$STRATA_RESOLV"
  docker rm -f strata-sink strata-relay >/dev/null 2>&1 || true
  docker run -d --name strata-sink --network "$NET" --ip "$SINK_IP" "${HARDEN[@]}" \
    --cap-add NET_ADMIN --cap-add NET_BIND_SERVICE -e SINK_IP="$SINK_IP" -e SINK_LOG=/log/sink.jsonl \
    -v "$logdir":/log "$IMG" python3 -u /sink.py >/dev/null
  docker run -d --name strata-relay --network "$FRONT" -p 127.0.0.1:18080:8080 "${HARDEN[@]}" --read-only \
    --user 65534:65534 "$IMG" socat TCP-LISTEN:8080,fork,reuseaddr "TCP:$STRATA_IP:8080" >/dev/null
  docker network connect "$NET" strata-relay
  sleep 1
  status
}

down() {
  docker rm -f strata-relay strata-sink >/dev/null 2>&1 || true
  echo "stratanet: sink and relay removed (networks kept)"
}

status() {
  for c in strata-sink strata-relay strata; do
    printf '%-13s %s\n' "$c" "$(docker inspect -f '{{.State.Status}} {{range $k,$v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}' "$c" 2>/dev/null)" || printf '%-13s absent\n' "$c"
  done
}

case "${1:-}" in
  up) up "${2:-}" ;;
  down) down ;;
  status) status ;;
  *) echo "usage: stratanet.sh up LOGDIR | down | status" >&2; exit 2 ;;
esac
