#!/usr/bin/env bash
# tbcheck.sh — formal check: production's -tb 4 override against the default (no -tb = same as -t 5) (2026-10-05).
# Decision rule, fixed before the run: keep -tb 4 only if it is >= 3% faster than the default on at least one
# production measurement AND not >= 3% slower on any; otherwise remove the override (llama.cpp default).
# Production server arguments otherwise (no MTP, 2 slots -kvu, -t 5, 256k, q8_0/turbo3, -rea off), on :8081.
# Per server run: tests/perf/llama_bench.py 2 (decode 1 and 2 at once; 16k prefill 1 and 2 at once; decode while
# a 32k prompt is read) and tests/perf/mtp_probe.py CONCURRENT=1 (edit + agentic at once). Order A B B A
# (tb4, default, default, tb4) so drift cannot favour one. Report: tests/perf/tb_report.py.
# Host settings are never changed here: hostprofile.sh check after `kent llama stop` (refuse on mismatch) and at
# the end. kent-llama is started again at the end if it was running.
# Start detached:  tests/bench/submit.sh tbcheck tbcheck.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"; P="$B/../perf"
RUN=$HOME/Documents/repo/bench/runs/tbcheck-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/tbcheck.log" 2>&1
log() { echo "$(date '+%F %T') tbcheck: $*"; }
M=/srv/kent/models
MODEL=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
SHA=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
PID=

stop_server() {
  [[ -n $PID ]] && { kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; }
  PID=
  for _ in $(seq 30); do ss -ltnH '( sport = :8081 )' | grep -q . || break; sleep 1; done
}

serve() {  # serve NAME EXTRA... — production arguments, plus EXTRA (the -tb setting under test)
  local name=$1; shift
  ss -ltnH '( sport = :8081 )' | grep -q . && { log "port 8081 in use"; return 1; }
  LD_LIBRARY_PATH=/opt/kent-llama/lib /opt/kent-llama/bin/llama-server -m "$M/$MODEL" --alias locally-run-model \
    -ngl 99 -ncmoe 40 -c 262144 -t 5 "$@" -ub 1024 --jinja --no-mmap --parallel 2 -kvu -fa on -ctk q8_0 -ctv turbo3 \
    --spec-type none -rea off --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.00 --presence-penalty 0.1 \
    --repeat-penalty 1.0 --host 127.0.0.1 --port 8081 -lv 1 > "$RUN/$name.server.log" 2>&1 &
  PID=$!
  for _ in $(seq 600); do
    curl -sf 127.0.0.1:8081/health >/dev/null && { log "$name up [$*]"; return 0; }
    kill -0 "$PID" 2>/dev/null || { log "$name: server died: $(tail -3 "$RUN/$name.server.log" | tr '\n' ' ')"; PID=; return 1; }
    sleep 1
  done
  log "$name: not healthy after 600 s"; stop_server; return 1
}

LLAMA_WAS_ACTIVE=0
systemctl is-active --quiet kent-llama && LLAMA_WAS_ACTIVE=1
finish() {
  stop_server
  "$B/hostprofile.sh" check > "$RUN/hostprofile-end.txt" 2>&1 && log "host profile at end: OK" \
    || log "WARNING: host profile changed during the run: $(grep MISMATCH "$RUN/hostprofile-end.txt" | tr '\n' ' ')"
  if ((LLAMA_WAS_ACTIVE)); then kent llama start >/dev/null 2>&1 && log "kent-llama started again" \
    || log "WARNING: kent llama start failed"; fi
  python3 "$P/tb_report.py" "$RUN" > "$RUN/report.md" 2>&1 && log "report: $RUN/report.md" || log "report failed"
  grep -h '^VERDICT' "$RUN/report.md" | sed "s/^/$(date '+%F %T') tbcheck: /"
  log "done: $RUN"
}

log "start (kent-llama was $( ((LLAMA_WAS_ACTIVE)) && echo active || echo stopped))"
echo "$SHA  $M/$MODEL" | sha256sum -c --quiet - || { log "hash mismatch for $MODEL"; exit 1; }
trap finish EXIT
if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; fi
if ! "$B/hostprofile.sh" check > "$RUN/hostprofile-start.txt" 2>&1; then
  log "refused: host profile mismatch after stopping kent-llama: $(grep MISMATCH "$RUN/hostprofile-start.txt" | tr '\n' ' ')"; exit 1
fi
log "host profile: OK ($(grep -E '^(core_clock_mhz|cpu_temp_c)=' "$RUN/hostprofile-start.txt" | tr '\n' ' '))"

i=0
for v in tb4 default default tb4; do
  i=$((i + 1)); name="$v-$i"
  if [[ $v == tb4 ]]; then serve "$name" -tb 4 || continue; else serve "$name" || continue; fi
  python3 "$P/llama_bench.py" http://127.0.0.1:8081 "$name" 2 "$RUN/bench-$name.json" > "$RUN/bench-$name.out" 2>&1 \
    || log "$name: llama_bench failed: $(tail -2 "$RUN/bench-$name.out" | tr '\n' ' ')"
  log "$name: $(grep -E '^(decode|prefill|interference)' "$RUN/bench-$name.out" | sed 's/  */ /g' | tr '\n' ';')"
  CONCURRENT=1 python3 "$P/mtp_probe.py" http://127.0.0.1:8081 "$v" "$RUN/pair-$name.json" "$PID" \
    > "$RUN/pair-$name.out" 2>&1 || log "$name: pair failed: $(tail -2 "$RUN/pair-$name.out" | tr '\n' ' ')"
  log "$name: $(grep ' pair: ' "$RUN/pair-$name.out")"
  stop_server
done
