#!/usr/bin/env bash
# depthcheck.sh [ROUNDS] [VARIANTS] [CTX] — the gap tests before the production change (operator, 2026-10-05).
#   1. llama-batched-bench, fixed schedule (no arrival-timing modes): -tb 4 vs default, 1 and 2 sequences,
#      2048-token prompts, 128 generated, order A B B A
#   2. tests/perf/depth_probe.py for each variant: 8 x 8K, 4 x 16K, 2 x 32K, 1 x 64K prompt tokens (frozen corpus),
#      copy + prose tasks, greedy + seeded sampled (temperature 0.7) -> one CSV row per request: depth.csv
#      variants (all with batch threads = -t, i.e. no -tb): q4-none, q4-n1 (MTP 1), q4-n2p08 (MTP 2, p_min 0.8),
#      q6-n2p08 (Q6, MTP 2, p_min 0.8); ROUNDS rounds (default 2), round 2 in reverse order
#   Report: tests/perf/depth_report.py -> report.md (summary by variant x bucket x task x sampling); depth.csv for
#   any other cut. Fit probe first: if Q6 with MTP does not start at 256k, every variant runs at 128k.
# Host settings are never changed here: hostprofile.sh check after `kent llama stop` (refuse on mismatch), at the end.
# VARIANTS (optional) limits the depth phase to those variants and skips the -tb step; CTX (optional) forces the
# context (no fit probe), e.g. depthcheck.sh 2 q6-n2p08 131072 (Q6 + MTP ran out of GPU memory at 256k on 32K
# prompts, 2026-10-06 01:49).
# Start detached:  tests/bench/submit.sh depth depthcheck.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"; P="$B/../perf"
ROUNDS=${1:-2}
BENCH=$HOME/Documents/repo/bench
RUN=$BENCH/runs/depthcheck-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/depthcheck.log" 2>&1
log() { echo "$(date '+%F %T') depthcheck: $*"; }
M=/srv/kent/models
CORPUS=$BENCH/corpus/kent-repo-1a9b067.txt
CORPUS_SHA=9316541c430fd067
declare -A FILE=([q4]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf [q6]=Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf)
declare -A SHA=([q4]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
                [q6]=35fce994cd36104a7dc1bd8a4bdf13778145664c00fdef6773aebc9246e5019c)
declare -A ARGS=([q4-none]="--spec-type none"
                 [q4-n1]="--spec-type draft-mtp --spec-draft-n-max 1 --spec-draft-p-min 0"
                 [q4-n2p08]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.8"
                 [q4-n2p0]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0"
                 [q6-n2p08]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.8")
VARIANTS=(q4-none q4-n1 q4-n2p08 q6-n2p08)
ONLY=${2:-}; FORCE_CTX=${3:-}
[[ -n $ONLY ]] && read -r -a VARIANTS <<< "$ONLY"
LB=/home/administrator/Downloads/repo/llama-cpp-turboquant/build-cuda13/bin   # llama-batched-bench, same commit as production
CTX=262144
PID=

stop_server() {
  [[ -n $PID ]] && { kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; }
  PID=
  for _ in $(seq 30); do ss -ltnH '( sport = :8081 )' | grep -q . || break; sleep 1; done
}

serve() {  # serve NAME QUANT EXTRA... — production arguments, no -tb (batch threads = -t 5)
  local name=$1 q=$2; shift 2
  ss -ltnH '( sport = :8081 )' | grep -q . && { log "port 8081 in use"; return 1; }
  LD_LIBRARY_PATH=/opt/kent-llama/lib /opt/kent-llama/bin/llama-server -m "$M/${FILE[$q]}" --alias locally-run-model \
    -ngl 99 -ncmoe 40 -c "$CTX" -t 5 -ub 1024 --jinja --no-mmap --parallel 2 -kvu -fa on -ctk q8_0 -ctv turbo3 \
    -rea off --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.00 --presence-penalty 0.1 --repeat-penalty 1.0 \
    --host 127.0.0.1 --port 8081 -lv 1 "$@" > "$RUN/$name.server.log" 2>&1 &
  PID=$!
  for _ in $(seq 900); do
    curl -sf 127.0.0.1:8081/health >/dev/null && { log "$name up (ctx $CTX): vram $(nvidia-smi --query-gpu=memory.used --format=csv,noheader) [$*]"; return 0; }
    kill -0 "$PID" 2>/dev/null || { log "$name: server died: $(tail -3 "$RUN/$name.server.log" | tr '\n' ' ')"; PID=; return 1; }
    sleep 1
  done
  log "$name: not healthy after 900 s"; stop_server; return 1
}

LLAMA_WAS_ACTIVE=0
systemctl is-active --quiet kent-llama && LLAMA_WAS_ACTIVE=1
finish() {
  stop_server
  "$B/hostprofile.sh" check > "$RUN/hostprofile-end.txt" 2>&1 && log "host profile at end: OK" \
    || log "WARNING: host profile changed during the run: $(grep MISMATCH "$RUN/hostprofile-end.txt" | tr '\n' ' ')"
  if ((LLAMA_WAS_ACTIVE)); then kent llama start >/dev/null 2>&1 && log "kent-llama started again" \
    || log "WARNING: kent llama start failed"; fi
  python3 "$P/depth_report.py" "$RUN" > "$RUN/report.md" 2>&1 && log "report: $RUN/report.md" || log "report failed"
  log "done: $RUN"
}

log "start (rounds $ROUNDS; kent-llama was $( ((LLAMA_WAS_ACTIVE)) && echo active || echo stopped))"
for q in q4 q6; do echo "${SHA[$q]}  $M/${FILE[$q]}" | sha256sum -c --quiet - || { log "hash mismatch for ${FILE[$q]}"; exit 1; }; done
[[ "$(sha256sum "$CORPUS" | cut -c1-16)" == "$CORPUS_SHA" ]] || { log "corpus hash mismatch"; exit 1; }
trap finish EXIT
if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; fi
if ! "$B/hostprofile.sh" check > "$RUN/hostprofile-start.txt" 2>&1; then
  log "refused: host profile mismatch after stopping kent-llama: $(grep MISMATCH "$RUN/hostprofile-start.txt" | tr '\n' ' ')"; exit 1
fi
log "host profile: OK ($(grep -E '^(core_clock_mhz|cpu_temp_c)=' "$RUN/hostprofile-start.txt" | tr '\n' ' '))"

# 1. fixed-schedule concurrency: -tb 4 vs default (full runs only)
i=0
[[ -n $ONLY ]] || for v in tb4 default default tb4; do
  i=$((i + 1)); tb=(); [[ $v == tb4 ]] && tb=(-tb 4)
  LD_LIBRARY_PATH=$LB "$LB/llama-batched-bench" -m "$M/${FILE[q4]}" -ngl 99 -ncmoe 40 -fa on -ctk q8_0 -ctv turbo3 \
    -c 8192 -ub 1024 -t 5 "${tb[@]}" --no-mmap -npp 2048 -ntg 128 -npl 1,2 --output-format jsonl \
    > "$RUN/batched-$v-$i.jsonl" 2> "$RUN/batched-$v-$i.err" \
    && log "batched $v-$i: $(python3 -c "import json,sys; print(' '.join(f\"npl={d['pl']} S_TG={d['speed_tg']:.2f}\" for d in map(json.loads, open(sys.argv[1])) if 'pl' in d))" "$RUN/batched-$v-$i.jsonl")" \
    || log "batched $v-$i failed: $(tail -2 "$RUN/batched-$v-$i.err" | tr '\n' ' ')"
done

# 2. depth: fit probe, then the variants
if [[ -n $FORCE_CTX ]]; then CTX=$FORCE_CTX; log "context forced: $CTX"
elif serve q6-fit-256k q6 --spec-type draft-mtp --spec-draft-n-max 2; then stop_server
else CTX=131072; log "Q6 with MTP does not start at 256k: every variant runs at 128k"; fi
for r in $(seq "$ROUNDS"); do
  ORDER=("${VARIANTS[@]}"); ((r % 2 == 0)) && mapfile -t ORDER < <(printf '%s\n' "${VARIANTS[@]}" | tac)
  log "round $r order: ${ORDER[*]}"
  for v in "${ORDER[@]}"; do
    # shellcheck disable=SC2086
    serve "$v-r$r" "${v%%-*}" ${ARGS[$v]} || continue
    python3 "$P/depth_probe.py" http://127.0.0.1:8081 "$v" "$r" "$RUN/depth.csv" "$PID" "$CORPUS" \
      > "$RUN/depth-$v-r$r.out" 2>&1 || log "$v-r$r: depth_probe failed: $(tail -2 "$RUN/depth-$v-r$r.out" | tr '\n' ' ')"
    log "$v-r$r: $(grep -c ' tok/s' "$RUN/depth-$v-r$r.out") requests, flagged: $(grep ' tok/s' "$RUN/depth-$v-r$r.out" | grep -vc ' clean$')"
    stop_server
  done
done
