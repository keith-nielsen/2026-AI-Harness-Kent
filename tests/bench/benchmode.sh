#!/usr/bin/env bash
# kent-bench (installed root-owned as /opt/kent-bench/bin/kent-bench by install-bench-tools.sh)
#   on      put this Kent host into bench mode
#   reset   return Kent to the known bench state (before every test item)
#   off     undo bench mode, restoring every original and checking it by hash
#   status
#   golden  re-baseline the golden Kent state (bench mode off)
# Runs as root. Everything it uses (this script, the fixtures, the gateway installer) is the
# root-owned copy under /opt/kent-bench/src, never the operator-writable repo.
#
# on:  - gateway: the bench flavour chosen when the bench tools were installed ($OPT/gateway:
#        dev = every tier -> 127.0.0.1:8080, i.e. the MUTT behind the bench proxy; sim-routed =
#        production routing with frontier -> the oracle on 127.0.0.1:4010, answered live);
#        the installed flavour is recorded and restored by `off`
#      - kent.conf: TEMPLATE_COMMITS=0 (learnings reviewed, never committed to the template) and
#        NOTICE_LOOKBACK_HOURS=0 (a new chat sees no notices from earlier test items)
#      - search: kent-searxng stopped; the fixture server answers on 127.0.0.1:8888 (Gents reach it
#        through Kent's kent-gent-search.socket forwarder on 172.30.0.1:8888) and serves the
#        fixture sites on 172.30.0.1:80
#      - egress: Squid gets a hosts file mapping the fixture host names to 172.30.0.1, ONE rule allowing
#        read-only HTTP to 172.30.0.1:80, and then refuses every other destination: Gents see only the
#        frozen fixture web (refusals are in Squid's log, so attempts to reach out are counted)
#      - share: /var/lib/kent-bench/share (kent:kent-operators 2770) for Kent's local-host items
#      - Kent's state: EVERYTHING in Kent's home (/var/lib/kent: Hermes memories, skills, sessions,
#        state.db, work/, dotfiles such as .gitconfig, caches) except the data Kent's services keep
#        there (kent.db, audit/, digests/, inbox/, stuck/). The pre-bench copy is saved for `off`;
#        Kent is set to the GOLDEN state (saved by the first `on` ever and kept across bench
#        sessions, so every model starts from the same Kent; `golden` re-baselines it)
# reset: Kent's state back to golden; refuses while a Gent or Kent's Hermes runs; prints the state
#        hash, which the bench runner records per test item.
# off:   undoes all of it and checks every restored file and Kent's state against the hashes
#        recorded by `on`. Every switch is logged (journal tag kent-bench).
set -euo pipefail
SRC=/opt/kent-bench/src
BENCH_GATEWAY=$(cat /opt/kent-bench/gateway 2>/dev/null || echo dev)   # set by install-bench-tools.sh --gateway
STATE=/var/lib/kent-bench; FIX=$STATE/fixtures
KCONF=/etc/kent/kent/kent.conf; SQUID=/etc/kent/squid/squid.conf; HOSTS=/etc/kent/squid/bench-hosts
LCONF=/etc/kent/litellm/config.yaml; UNIT=kent-bench-fixtures
BRIDGE=172.30.0.1
KHOME=/var/lib/kent
KEEP=(kent.db kent.db-wal kent.db-shm kent.db-journal audit digests inbox stuck)   # service data: never reset
GOLDEN=$STATE/golden-kent-state.tar; PREBENCH=$STATE/prebench-kent-state.tar
die() { echo "kent-bench: $*" >&2; exit 1; }
log() { echo "kent-bench: $*"; logger -t kent-bench -- "human:${SUDO_USER:-root} $*"; }
[[ $EUID -eq 0 ]] || die "run as root"
[[ -d $SRC/tests/bench ]] || die "$SRC missing; run install-bench-tools.sh"
no_hermes() { ! pgrep -u kent -f hermes >/dev/null || die "Kent's Hermes is running; wait for it to finish"; }
no_gents() { ! docker ps --format '{{.Names}}' | grep -q '^kent-gent-' || die "a Gent is running; stop it first"; }
flavour() { case "$(head -1 "$LCONF")" in *litellm_config.yaml*) echo prod ;; *sim-routed*) echo sim-routed ;;
            *litellm_config.sim.yaml*) echo sim ;; *) echo dev ;; esac; }
kpaths() {   # top-level entries of Kent's home that belong to Kent's state (everything but KEEP)
  (cd "$KHOME" && for p in .[!.]* ..?* *; do
     [[ -e $p || -L $p ]] || continue
     for k in "${KEEP[@]}"; do [[ $p == "$k" ]] && continue 2; done
     echo "$p"
   done)
}

kent_save() {   # $1 = tar file
  local paths; mapfile -t paths < <(kpaths)
  tar -C "$KHOME" -cpf "$1" --files-from=/dev/null "${paths[@]}"
}
kent_restore() {   # $1 = tar file
  pgrep -u kent -f hermes >/dev/null && die "Kent's Hermes is running; wait for it to finish"
  local p
  while IFS= read -r p; do rm -rf "${KHOME:?}/$p"; done < <(kpaths)
  tar -C "$KHOME" -xpf "$1"
}
kent_hash() {   # paths, modes, owners, sizes and file contents of Kent's state
  local paths; mapfile -t paths < <(kpaths)
  ((${#paths[@]})) || { echo empty; return; }
  # regular files, directories and links only: sockets and FIFOs are runtime objects tar cannot save
  (cd "$KHOME" && { find "${paths[@]}" \( -type f -o -type d -o -type l \) -print0 | sort -z |
                      xargs -0 stat -c '%n %a %U:%G %s';
                    find "${paths[@]}" -type f -print0 | sort -z | xargs -0 -r sha256sum; }) | sha256sum | cut -c1-16
}

on() {
  case $BENCH_GATEWAY in dev|sim-routed) ;; *) die "bench gateway '$BENCH_GATEWAY' unknown (install-bench-tools.sh --gateway dev|sim-routed)" ;; esac
  [[ -f $STATE/on ]] && die "already on (see $STATE)"
  [[ -f $STATE/orig.sha256 ]] && die "a previous 'on' did not finish; run 'off' first (it restores the saved originals)"
  no_gents
  no_hermes
  install -d -m 0755 "$STATE"; install -d -m 0700 "$STATE/orig"
  for f in "$KCONF" "$SQUID"; do cp -p "$f" "$STATE/orig/$(basename "$f")"; done
  sha256sum "$KCONF" "$SQUID" > "$STATE/orig.sha256"
  flavour > "$STATE/flavour"
  kent_save "$PREBENCH"; kent_hash > "$STATE/prebench.hash"
  [[ -f $GOLDEN ]] || { cp -p "$PREBENCH" "$GOLDEN"; log "golden Kent state created ($(cat "$STATE/prebench.hash"))"; }
  # fixtures: a root-owned, world-readable copy (the service account cannot read a home directory)
  rm -rf "$FIX"; install -d -m 0755 "$FIX"; cp -r "$SRC/tests/bench/fixtures/." "$FIX/"
  chmod -R a+rX,go-w "$FIX"
  rm -rf "$STATE/share"; install -d -o kent -g kent-operators -m 2770 "$STATE/share"
  # kent.conf
  printf '\n# bench mode (kent-bench on)\nTEMPLATE_COMMITS=0\nNOTICE_LOOKBACK_HOURS=0\n' >> "$KCONF"
  # squid: hosts file + one allow rule placed before the private-address denies
  : > "$HOSTS"; for d in "$FIX"/sites/*/; do [[ -d $d ]] && echo "$BRIDGE $(basename "$d")" >> "$HOSTS"; done
  chown root:kent-squid "$HOSTS"; chmod 0640 "$HOSTS"
  python3 - "$SQUID" "$HOSTS" "$BRIDGE" <<'EOF'
import sys
p, hosts, ip = sys.argv[1:]
s = open(p).read()
anchor = "http_access deny private_dst\n"
assert anchor in s, "squid.conf: anchor rule not found"
add = (f"# --- bench mode (kent-bench) ---\nhosts_file {hosts}\nacl bench_fixture dst {ip}\n"
       "acl bench_port port 80\nhttp_access allow bench_fixture bench_port readonly\n"
       "http_access deny all   # bench: the fixture web only; any other site is refused (and logged)\n"
       "# --- end bench mode ---\n")
open(p, "w").write(s.replace(anchor, add + anchor, 1))
EOF
  /usr/sbin/squid -k parse -f "$SQUID" 2>&1 | grep -E "FATAL|ERROR" && die "squid rejected the bench config; run 'off'"
  systemctl restart kent-squid
  # search: fixture server in place of SearXNG
  systemctl stop kent-searxng
  systemd-run --unit "$UNIT" --collect -p DynamicUser=yes -p AmbientCapabilities=CAP_NET_BIND_SERVICE \
    -p CapabilityBoundingSet=CAP_NET_BIND_SERVICE -p NoNewPrivileges=yes -p ProtectHome=yes \
    /usr/bin/python3 "$FIX/server.py" "$FIX" "127.0.0.1:8888" "$BRIDGE:80" >/dev/null
  for _ in $(seq 20); do curl -sf 127.0.0.1:8888/healthz >/dev/null && break; sleep 0.5; done
  curl -sf 127.0.0.1:8888/healthz >/dev/null || die "fixture server did not start (journalctl -u $UNIT); run 'off'"
  kent_restore "$GOLDEN"
  (cd "$SRC" && install/services/litellm/install.sh --config "$BENCH_GATEWAY") >/dev/null
  date -u +%FT%TZ > "$STATE/on"
  log "bench on (gateway $BENCH_GATEWAY, was $(cat "$STATE/flavour"); Kent state $(kent_hash))"
}

reset() {
  [[ -f $STATE/on ]] || die "bench mode is off"
  no_gents
  no_hermes
  kent_restore "$GOLDEN"
  echo "kent-bench: reset ok; Kent state $(kent_hash)"
}

off() {
  [[ -f $STATE/orig.sha256 ]] || die "not on (no saved originals)"   # also undoes an unfinished 'on'
  no_gents
  no_hermes                       # check everything first: a refusal must leave bench mode fully on
  systemctl stop "$UNIT" 2>/dev/null || true
  [[ -f $PREBENCH ]] && kent_restore "$PREBENCH"
  for f in "$KCONF" "$SQUID"; do cp -p "$STATE/orig/$(basename "$f")" "$f"; done
  rm -f "$HOSTS"
  systemctl restart kent-squid
  systemctl start kent-searxng
  (cd "$SRC" && install/services/litellm/install.sh --config "$(cat "$STATE/flavour")") >/dev/null
  sha256sum -c --quiet "$STATE/orig.sha256" || die "restored files differ from the originals (see $STATE/orig)"
  if [[ -f $STATE/prebench.hash && "$(kent_hash)" != "$(cat "$STATE/prebench.hash")" ]]; then
    die "Kent state not restored (saved copy: $PREBENCH)"; fi
  [[ "$(flavour)" == "$(cat "$STATE/flavour")" ]] || die "gateway flavour not restored"
  rm -f "$STATE/on" "$STATE/orig.sha256" "$PREBENCH" "$STATE/prebench.hash"
  log "bench off (gateway $(flavour); files and Kent state verified)"
}

golden() {
  [[ -f $STATE/on ]] && die "turn bench mode off first"
  install -d -m 0755 "$STATE"
  kent_save "$GOLDEN.new" && mv "$GOLDEN.new" "$GOLDEN"
  log "golden Kent state re-baselined ($(kent_hash))"
}

case "${1:-}" in
  on) on ;;
  off) off ;;
  reset) reset ;;
  golden) golden ;;
  status) if [[ -f $STATE/on ]]; then echo "bench on since $(cat "$STATE/on"); gateway $(flavour); Kent state $(kent_hash)"
          else echo "bench off; gateway $(flavour)"; fi ;;
  *) die "usage: kent-bench on|reset|off|status|golden" ;;
esac
