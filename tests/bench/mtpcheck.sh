#!/usr/bin/env bash
# mtpcheck.sh [ROUNDS] [lean|final|q6|pair] — the deterministic MTP investigation (operator, 2026-10-05).
# Question: on this box a 2-token MTP step costs 2.05x a plain step but a 3-token step only 2.59x. Why?
# Prime suspect: llama.cpp runs every multi-token step on -tb threads (production 4) and single-token steps
# on -t (5) (src/llama-context.cpp:1362).
#   B. step cost without speculation: llama-bench (same commit as production, c26cbdf), one forward pass of
#      N = 1,2,3,4 tokens (= plain decode, and the verify pass of 1-3 MTP drafts) at context depth 2048, -t 4 and
#      -t 5 (llama-bench uses one count for both), 10 reps
#   C. MTP sweep (lean, operator 2026-10-05): tests/perf/mtp_probe.py (fixed prompts, greedy, reuse checked, sysmon
#      flags), edit + agentic workloads, one server per variant: none, MTP n_max 1/2/3 (p_min 0) at -tb 4
#      (production), MTP n_max 1 at -tb 5; ROUNDS rounds (default 2), order shuffled per round (seed = round)
#   E. tests/perf/mtp_report.py: tables, flags, predicted vs measured
# Host settings: never changed here. The operator sets them by hand (tests/bench/hostprofile.sh apply);
# this job runs `hostprofile.sh check` AFTER stopping kent-llama and refuses to measure on any mismatch,
# and records the check again at the end (hostprofile-start.txt / hostprofile-end.txt).
# kent-llama is stopped for the run and started again at the end if it was running. Kent's state is not touched.
#   final (the remaining checks before production): no step cost; sweep none, MTP n1/n2/n3 (p_min 0) and
#      n1/n2 (p_min 0.8), all at -tb 5, on edit + agentic + prose; then 2-slot concurrency (edit and agentic at
#      the same time, mtp_probe CONCURRENT=1) for none, n1 and n2 at -tb 5. ROUNDS shuffled rounds each.
#   q6: Q6 against Q4 (operator): q6-none, q6-n2p08-tb5 and n2p08-tb5 (Q4 reference) on edit + agentic + prose;
#      a variant named q6-* runs Qwen3.6-35B-A3B-UD-Q6_K_XL (hash-checked); fit probe first: if Q6 with MTP does
#      not start at 256k, every variant runs at 128k.
#   pair: only the 2-slot concurrency phase (edge + agentic at once) for none, n2p08-tb5 and n2p0-tb5, all at
#      -tb 5 (the threshold variant under 2 slots was never measured).
# Start detached:  tests/bench/submit.sh mtpcheck mtpcheck.sh   (final: ... mtpcheck.sh 2 final)
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"; P="$B/../perf"
ROUNDS=${1:-2}
SET=${2:-lean}
[[ $SET == lean || $SET == final || $SET == q6 || $SET == pair ]] || { echo "mtpcheck: SET must be lean, final, q6 or pair" >&2; exit 2; }
BENCH=$HOME/Documents/repo/bench
RUN=$BENCH/runs/mtpcheck-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/mtpcheck.log" 2>&1
log() { echo "$(date '+%F %T') mtpcheck: $*"; }
M=/srv/kent/models
MODEL=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
SHA=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
MODEL_Q6=Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf
SHA_Q6=35fce994cd36104a7dc1bd8a4bdf13778145664c00fdef6773aebc9246e5019c
CTX=262144
SRV=/opt/kent-llama/bin; SRVLIB=/opt/kent-llama/lib
LB=/home/administrator/Downloads/repo/llama-cpp-turboquant/build-cuda13/bin   # llama-bench, same commit (c26cbdf)
PID=

stop_server() {
  [[ -n $PID ]] && { kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; }
  PID=
  for _ in $(seq 30); do ss -ltnH '( sport = :8081 )' | grep -q . || break; sleep 1; done
}

serve() {  # serve NAME TB EXTRA... — production arguments except -tb and the speculation flags; q6-* = Q6 model
  local name=$1 tb=$2 model=$MODEL; shift 2
  [[ $name == q6-* ]] && model=$MODEL_Q6
  ss -ltnH '( sport = :8081 )' | grep -q . && { log "port 8081 in use"; return 1; }
  LD_LIBRARY_PATH=$SRVLIB "$SRV/llama-server" -m "$M/$model" --alias locally-run-model -ngl 99 -ncmoe 40 \
    -c "$CTX" -t 5 -tb "$tb" -ub 1024 --jinja --no-mmap --parallel 2 -kvu -fa on -ctk q8_0 -ctv turbo3 \
    -rea off --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.00 --presence-penalty 0.1 --repeat-penalty 1.0 \
    --host 127.0.0.1 --port 8081 -lv 1 "$@" > "$RUN/$name.server.log" 2>&1 &
  PID=$!
  for _ in $(seq 600); do
    curl -sf 127.0.0.1:8081/health >/dev/null && { log "$name up: vram $(nvidia-smi --query-gpu=memory.used --format=csv,noheader) [-tb $tb $*]"; return 0; }
    kill -0 "$PID" 2>/dev/null || { log "$name: server died: $(tail -3 "$RUN/$name.server.log" | tr '\n' ' ')"; PID=; return 1; }
    sleep 1
  done
  log "$name: not healthy after 600 s"; stop_server; return 1
}

LLAMA_WAS_ACTIVE=0
systemctl is-active --quiet kent-llama && LLAMA_WAS_ACTIVE=1
finish() {
  stop_server
  if ((LLAMA_WAS_ACTIVE)); then kent llama start >/dev/null 2>&1 && log "kent-llama started again" \
    || log "WARNING: kent llama start failed"; fi
  "$B/hostprofile.sh" check > "$RUN/hostprofile-end.txt" 2>&1 && log "host profile at end: OK" \
    || log "WARNING: host profile changed during the run: $(grep MISMATCH "$RUN/hostprofile-end.txt" | tr '\n' ' ')"
  python3 "$P/mtp_report.py" "$RUN" > "$RUN/report.md" 2>&1 && log "report: $RUN/report.md" || log "report failed"
  log "done: $RUN"
}

log "start (set $SET, rounds $ROUNDS; kent-llama was $( ((LLAMA_WAS_ACTIVE)) && echo active || echo stopped))"
echo "$SHA  $M/$MODEL" | sha256sum -c --quiet - || { log "hash mismatch for $MODEL"; exit 1; }
if [[ $SET == q6 ]]; then
  echo "$SHA_Q6  $M/$MODEL_Q6" | sha256sum -c --quiet - || { log "hash mismatch for $MODEL_Q6"; exit 1; }
fi
trap finish EXIT
if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; fi
if ! "$B/hostprofile.sh" check > "$RUN/hostprofile-start.txt" 2>&1; then
  log "refused: host profile mismatch after stopping kent-llama: $(grep MISMATCH "$RUN/hostprofile-start.txt" | tr '\n' ' ')"; exit 1
fi
log "host profile: OK ($(grep -E '^(core_clock_mhz|cpu_temp_c)=' "$RUN/hostprofile-start.txt" | tr '\n' ' '))"

# B. step cost (lean only)
if [[ $SET == lean ]]; then
log "B: llama-bench step cost (N=1..4, depth 2048, -t 4 and 5, 10 reps)"
LD_LIBRARY_PATH=$LB "$LB/llama-bench" -m "$M/$MODEL" -ngl 99 -ncmoe 40 -fa on -ctk q8_0 -ctv turbo3 -ub 1024 \
  -mmp 0 -p 1,2,3,4 -n 0 -d 2048 -r 10 -t 4,5 -o json > "$RUN/stepcost.json" 2> "$RUN/stepcost.err" \
  && log "B: done" || log "B: llama-bench failed: $(tail -2 "$RUN/stepcost.err" | tr '\n' ' ')"
fi

# C. MTP sweep
declare -A TB=() ARGS=()
if [[ $SET == lean ]]; then
  for v in none n1p0 n2p0 n3p0; do TB[$v]=4; done
  TB[n1p0-tb5]=5
  WL=edit,agentic
elif [[ $SET == pair ]]; then
  for v in none n2p08-tb5 n2p0-tb5; do TB[$v]=5; done
  WL=edit,agentic
elif [[ $SET == final ]]; then
  TB[none]=4
  for v in n1p0-tb5 n2p0-tb5 n3p0-tb5 n1p08-tb5 n2p08-tb5; do TB[$v]=5; done
  WL=edit,agentic,prose
else
  for v in q6-none q6-n2p08-tb5 n2p08-tb5; do TB[$v]=5; done
  WL=edit,agentic,prose
  if serve q6-fit-256k 5 --spec-type draft-mtp --spec-draft-n-max 2; then stop_server
  else CTX=131072; log "Q6 with MTP does not start at 256k: every variant runs at 128k"; fi
fi
for v in "${!TB[@]}"; do
  base=${v#q6-}
  case $base in
    none*) ARGS[$v]="--spec-type none" ;;
    *) n=${base:1:1}; p=$([[ $v == *p08* ]] && echo 0.8 || echo 0)
       ARGS[$v]="--spec-type draft-mtp --spec-draft-n-max $n --spec-draft-p-min $p" ;;
  esac
done
for r in $(seq "$ROUNDS"); do
  [[ $SET == pair ]] && break
  mapfile -t ORDER < <(python3 -c "import random; v=sorted('${!TB[*]}'.split()); random.seed($r); random.shuffle(v); print('\n'.join(v))")
  log "C: round $r order: ${ORDER[*]}"
  for v in "${ORDER[@]}"; do
    # shellcheck disable=SC2086
    serve "$v-r$r" "${TB[$v]}" ${ARGS[$v]} || continue
    WORKLOADS=$WL python3 "$P/mtp_probe.py" http://127.0.0.1:8081 "$v" "$RUN/probe-$v-r$r.json" "$PID" > "$RUN/probe-$v-r$r.out" 2>&1 \
      || log "$v-r$r: mtp_probe failed: $(tail -2 "$RUN/probe-$v-r$r.out" | tr '\n' ' ')"
    grep -h ' tok/s ' "$RUN/probe-$v-r$r.out" | grep -v ' clean$' | sed "s/^/$(date '+%F %T') mtpcheck: FLAGGED r$r /"
    stop_server
  done
done

# D. 2-slot concurrency (final only): edit and agentic at the same time on one server
if [[ $SET == final || $SET == pair ]]; then
  PAIR=(none n1p0-tb5 n2p0-tb5); [[ $SET == pair ]] && PAIR=(none n2p08-tb5 n2p0-tb5)
  for r in $(seq "$ROUNDS"); do
    mapfile -t ORDER < <(python3 -c "import random; v=sorted('${PAIR[*]}'.split()); random.seed(100 + $r); random.shuffle(v); print('\n'.join(v))")
    log "D: concurrency round $r order: ${ORDER[*]}"
    for v in "${ORDER[@]}"; do
      # shellcheck disable=SC2086
      serve "$v-pair-r$r" "${TB[$v]}" ${ARGS[$v]} || continue
      CONCURRENT=1 python3 "$P/mtp_probe.py" http://127.0.0.1:8081 "$v-pair" "$RUN/probe-$v-pair-r$r.json" "$PID" \
        > "$RUN/probe-$v-pair-r$r.out" 2>&1 || log "$v-pair-r$r: mtp_probe failed: $(tail -2 "$RUN/probe-$v-pair-r$r.out" | tr '\n' ' ')"
      log "D: $(grep ' pair: ' "$RUN/probe-$v-pair-r$r.out")"
      stop_server
    done
  done
fi
