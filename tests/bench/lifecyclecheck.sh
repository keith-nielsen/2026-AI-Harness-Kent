#!/usr/bin/env bash
# lifecyclecheck.sh — a bench job's lifecycle, end to end, to prove nothing changes host settings (2026-10-05).
# Same steps as mtpcheck/speccheck, with a short workload: state -> kent llama stop -> hostprofile check ->
# test server (production arguments, :8081) + one monitored request (mtp_probe, agentic) -> check ->
# server stopped -> check -> kent llama start -> check; then the journal for any tuning activity in the window.
# Pass = boost/SMT/governor/swap/GPU identical at every step and the measured core clock at base (~3.6 GHz).
# Start detached:  tests/bench/submit.sh lifecycle lifecyclecheck.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"; P="$B/../perf"
RUN=$HOME/Documents/repo/bench/runs/lifecyclecheck-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/lifecyclecheck.log" 2>&1
log() { echo "$(date '+%F %T') lifecyclecheck: $*"; }
M=/srv/kent/models; MODEL=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
T0=$(date '+%F %T')
FAIL=0
step() {  # step NAME — hostprofile check, recorded; any mismatch fails the run
  local f="$RUN/hostprofile-$1.txt"
  if "$B/hostprofile.sh" check > "$f" 2>&1; then
    log "$1: OK ($(grep -E '^(smt|boost|governor|swap|gpu_idle_clock_mhz|core_clock_mhz|kent_llama)=' "$f" | tr '\n' ' '))"
  else
    FAIL=1; log "$1: MISMATCH $(grep -E 'MISMATCH|^(smt|boost|core_clock_mhz|kent_llama)=' "$f" | tr '\n' ' ')"
  fi
}

log "start"
step 1-before
kent llama stop >/dev/null 2>&1 && log "kent llama stop: done" || { log "kent llama stop FAILED"; exit 1; }
step 2-after-stop

LD_LIBRARY_PATH=/opt/kent-llama/lib /opt/kent-llama/bin/llama-server -m "$M/$MODEL" --alias locally-run-model \
  -ngl 99 -ncmoe 40 -c 262144 -t 5 -tb 4 -ub 1024 --jinja --no-mmap --parallel 2 -kvu -fa on -ctk q8_0 -ctv turbo3 \
  --spec-type none -rea off --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.00 --presence-penalty 0.1 --repeat-penalty 1.0 \
  --host 127.0.0.1 --port 8081 -lv 1 > "$RUN/server.log" 2>&1 &
PID=$!
for _ in $(seq 600); do curl -sf 127.0.0.1:8081/health >/dev/null && break; kill -0 $PID 2>/dev/null || break; sleep 1; done
if curl -sf 127.0.0.1:8081/health >/dev/null; then
  log "test server up"
  WORKLOADS=agentic python3 "$P/mtp_probe.py" http://127.0.0.1:8081 lifecycle "$RUN/probe.json" "$PID" > "$RUN/probe.out" 2>&1
  log "workload: $(grep ' tok/s ' "$RUN/probe.out")"
  step 3-under-load-done
else
  FAIL=1; log "test server did not start: $(tail -3 "$RUN/server.log" | tr '\n' ' ')"
fi
kill $PID 2>/dev/null; wait $PID 2>/dev/null
step 4-server-stopped

kent llama start >/dev/null 2>&1 && log "kent llama start: done" || { FAIL=1; log "kent llama start FAILED"; }
step 5-after-start

TUNE=$(journalctl -u kent-llama-tuning --since "$T0" --no-pager -o cat 2>/dev/null | grep -v -- '-- No entries --')
[[ -z "$TUNE" ]] && log "journal: no kent-llama-tuning activity since $T0" || { FAIL=1; log "journal: tuning activity: $TUNE"; }
BOOSTLOG=$(journalctl --since "$T0" --no-pager -o cat 2>/dev/null | grep -i -E 'cpufreq/boost|smt/control' | head -3)
[[ -z "$BOOSTLOG" ]] || log "journal mentions: $BOOSTLOG"

((FAIL)) && log "RESULT: FAIL (see hostprofile-*.txt)" || log "RESULT: PASS — host settings identical through stop, workload, start"
log "done: $RUN"
