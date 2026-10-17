#!/usr/bin/env bash
# speccheck.sh "BUILDS" ["SETS"] [RUN_DIR] — speed bake-off across llama.cpp builds (Strata / Edge0 ideas, 2026-10-05).
#   BUILDS: any of  current (/opt/kent-llama)  forktip  upstream  upstream-pd  (built by llamaprep.sh)
#   SETS (default "spec k4 cache"):
#     spec   tests/perf/spec_bench.py: none, ngram-simple, ngram-mod, MTP n1 p0 / n1 p0.8
#            (MTP n2 p0.8 dropped 2026-10-05: one draft token already does not pay on this CPU-bound box)
#            then tests/perf/llama_bench.py (2 slots) for none and every variant >3% faster on edit+agentic
#            and not >3% slower on prose
#     k4     spec_bench.py: stock at K=4 (--override-kv expert_used_count=4); Edge0 Q4_1 at K=4 with and
#            without its recovery LoRA, and at K=8 (quantization alone)
#     moe    forktip only: none + --moe-cache on (Turing needs MIN_EXPERT_KB=512, small reserve)
#     cache  tests/perf/cache_bench.py: server defaults, -cms 0, -cram 0
#     decision  upstream-pd only: tests/perf/decision_bench.py, /v1/decision vs chat (router tiers, validator)
#   e.g. speccheck.sh "current forktip upstream"     speccheck.sh upstream "k4"
#   RUN_DIR (optional): resume an earlier run there; any variant whose result file exists is not run again.
# Each build first probes whether MTP fits at 256k; if not, that build runs everything at 128k.
# A variant whose server does not start (e.g. Edge0's qwen3next GGUF on an older build) is logged and skipped.
# Production arguments otherwise (2 slots -kvu, -ncmoe 40, q8_0 K, turbo3 V (q8_0 on upstream), -rea off,
# production sampling), no mmproj (Edge0 has none), on :8081. kent-llama is stopped for the run and started
# again at the end if it was running. Kent's state is not touched. Host settings are never changed here: the
# job runs tests/bench/hostprofile.sh check AFTER stopping kent-llama, refuses on a mismatch, checks again at the end.
# Start detached:  tests/bench/submit.sh speccheck speccheck.sh "current forktip upstream"
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"; P="$B/../perf"
BUILDS_ARG=${1:?usage: speccheck.sh "BUILDS" ["SETS"]}; SETS=${2:-spec k4 cache}
BENCH=$HOME/Documents/repo/bench
RUN=${3:-$BENCH/runs/speccheck-$(date +%m%d%H%M)}
mkdir -p "$RUN"
exec >>"$RUN/speccheck.log" 2>&1
log() { echo "$(date '+%F %T') speccheck: $*"; }
M=/srv/kent/models
declare -A MFILE=([stock]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf [edge0]=Edge0-35B-A3B-preview-Q4_1.gguf)
declare -A MSHA=([stock]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1 [edge0]=)
EDGE0_LORA=$M/Edge0-35B-A3B-preview-lora-F16.gguf
declare -A BIN=([current]=/opt/kent-llama/bin [forktip]=$BENCH/llama-builds/forktip/build-cuda/bin
                [upstream]=$BENCH/llama-builds/upstream/build-cuda/bin
                [upstream-pd]=$BENCH/llama-builds/upstream-pd/build-cuda/bin)
declare -A LIB=([current]=/opt/kent-llama/lib [forktip]=$BENCH/llama-builds/forktip/build-cuda/bin
                [upstream]=$BENCH/llama-builds/upstream/build-cuda/bin
                [upstream-pd]=$BENCH/llama-builds/upstream-pd/build-cuda/bin)
declare -A CTV=([current]=turbo3 [forktip]=turbo3 [upstream]=q8_0 [upstream-pd]=q8_0)
PID=

stop_server() {
  [[ -n $PID ]] && { kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; }
  PID=
  for _ in $(seq 30); do ss -ltnH '( sport = :8081 )' | grep -q . || break; sleep 1; done
}

# serve BUILD MODEL NAME CTX EXTRA... — start llama-server on :8081, wait until healthy; 1 if it dies or never answers
serve() {
  local build=$1 model=$2 name=$3 ctx=$4; shift 4
  ss -ltnH '( sport = :8081 )' | grep -q . && { log "port 8081 in use"; return 1; }
  [[ -f $M/${MFILE[$model]} ]] || { log "$name: model file missing: ${MFILE[$model]}"; return 1; }
  LD_LIBRARY_PATH=${LIB[$build]} nice -n 5 "${BIN[$build]}/llama-server" -m "$M/${MFILE[$model]}" \
    --alias locally-run-model -ngl 99 -ncmoe 40 -c "$ctx" \
    -t 5 -tb 4 -ub 1024 --jinja --no-mmap --parallel 2 -kvu -fa on -ctk q8_0 -ctv "${CTV[$build]}" \
    -rea off --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.00 --presence-penalty 0.1 --repeat-penalty 1.0 \
    --host 127.0.0.1 --port 8081 -lv 1 "$@" > "$RUN/$name.server.log" 2>&1 &
  PID=$!
  for _ in $(seq 600); do
    curl -sf 127.0.0.1:8081/health >/dev/null && {
      log "$name up (ctx $ctx): vram $(nvidia-smi --query-gpu=memory.used --format=csv,noheader) [$*]"; return 0; }
    kill -0 "$PID" 2>/dev/null || { log "$name: server died: $(tail -3 "$RUN/$name.server.log" | tr '\n' ' ')"; PID=; return 1; }
    sleep 1
  done
  log "$name: not healthy after 600 s"; stop_server; return 1
}

spec() {  # spec BUILD MODEL NAME CTX EXTRA... — one server, spec_bench, stop
  local build=$1 model=$2 name=$3 ctx=$4; shift 4
  [[ -f $RUN/spec-$name.json ]] && { log "$name: done in an earlier run; skipped"; return 0; }
  # shellcheck disable=SC2068
  serve "$build" "$model" "$name" "$ctx" $@ || return 1
  python3 "$P/spec_bench.py" http://127.0.0.1:8081 "$name" "$RUN/spec-$name.json" 3 || log "$name: spec_bench failed"
  stop_server
}

LLAMA_WAS_ACTIVE=0
systemctl is-active --quiet kent-llama && LLAMA_WAS_ACTIVE=1
finish() {
  stop_server
  "$B/hostprofile.sh" check > "$RUN/hostprofile-end.txt" 2>&1 && log "host profile at end: OK" \
    || log "WARNING: host profile changed during the run: $(grep MISMATCH "$RUN/hostprofile-end.txt" | tr '\n' ' ')"
  if ((LLAMA_WAS_ACTIVE)); then kent llama start >/dev/null 2>&1 && log "kent-llama started again" \
    || log "WARNING: kent llama start failed"; fi
  log "done: $RUN"
}
trap finish EXIT

log "start: builds [$BUILDS_ARG] sets [$SETS] (kent-llama was $( ((LLAMA_WAS_ACTIVE)) && echo active || echo stopped))"
echo "${MSHA[stock]}  $M/${MFILE[stock]}" | sha256sum -c --quiet - || { log "hash mismatch for ${MFILE[stock]}"; exit 1; }
if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; fi
if ! "$B/hostprofile.sh" check > "$RUN/hostprofile-start.txt" 2>&1; then
  log "refused: host profile mismatch after stopping kent-llama: $(grep MISMATCH "$RUN/hostprofile-start.txt" | tr '\n' ' ')"; exit 1
fi
log "host profile: OK ($(grep -E '^(core_clock_mhz|cpu_temp_c)=' "$RUN/hostprofile-start.txt" | tr '\n' ' '))"

declare -A SPEC=(
  [none]="--spec-type none"
  [ngram-simple]="--spec-type ngram-simple"
  [ngram-mod]="--spec-type ngram-mod"
  [mtp-n1-p0]="--spec-type draft-mtp --spec-draft-n-max 1 --spec-draft-p-min 0"
  [mtp-n1-p08]="--spec-type draft-mtp --spec-draft-n-max 1 --spec-draft-p-min 0.8"
  [mtp-n2-p08]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.8"
)
ORDER=(none ngram-simple ngram-mod mtp-n1-p0 mtp-n1-p08)
K4="--override-kv qwen35moe.expert_used_count=int:4"
E4="--override-kv qwen3next.expert_used_count=int:4"
E8="--override-kv qwen3next.expert_used_count=int:8"

for b in $BUILDS_ARG; do
  [[ -x ${BIN[$b]:-}/llama-server ]] || { log "$b: no llama-server at ${BIN[$b]:-?}; skipped"; continue; }
  log "== build $b: $(LD_LIBRARY_PATH=${LIB[$b]} "${BIN[$b]}/llama-server" --version 2>&1 | grep -m1 version)"
  if [[ " $SETS " == *" decision "* && $b == upstream-pd ]]; then
    if serve "$b" stock "$b-decision" 131072 --spec-type none --decision-seqs 16; then
      python3 "$P/decision_bench.py" http://127.0.0.1:8081 "$RUN/decision-$b.json" > "$RUN/decision-$b.out" 2>&1 \
        || log "$b-decision: decision_bench failed: $(tail -2 "$RUN/decision-$b.out" | tr '\n' ' ')"
      grep -h '^router: {\|^validator: {' "$RUN/decision-$b.out" | sed "s/^/$(date '+%F %T') speccheck: $b /"
      stop_server
    fi
    [[ " $SETS " == *" decision "* && $SETS == decision ]] && continue
  fi
  CTX=262144
  if [[ -f $RUN/$b-fit-256k.server.log ]] && grep -q "speccheck: $b-fit-256k up" "$RUN/speccheck.log"; then
    log "$b: fit probe passed in an earlier run (256k)"
  elif [[ -f $RUN/$b-fit-256k.server.log ]]; then CTX=131072; log "$b: fit probe failed in an earlier run: 128k"
  elif serve "$b" stock "$b-fit-256k" $CTX --spec-type draft-mtp --spec-draft-n-max 2; then stop_server
  else CTX=131072; log "$b: MTP does not fit at 256k: this build runs at 128k"; fi

  if [[ " $SETS " == *" spec "* ]]; then
    for v in "${ORDER[@]}"; do spec "$b" stock "$b-$v" $CTX ${SPEC[$v]}; done
    WIN=$(python3 - "$RUN" "$b" <<'EOF'
import json, sys, pathlib
d, b = pathlib.Path(sys.argv[1]), sys.argv[2]
def load(v):
    f = d / f"spec-{b}-{v}.json"
    return json.load(open(f))["workloads"] if f.exists() else None
base = load("none")
for v in ("ngram-simple", "ngram-mod", "mtp-n1-p0", "mtp-n1-p08"):
    w = load(v)
    if not (base and w): continue
    work = sum(w[k]["mean_tps"] for k in ("edit", "agentic")) / sum(base[k]["mean_tps"] for k in ("edit", "agentic"))
    if work > 1.03 and w["prose"]["mean_tps"] / base["prose"]["mean_tps"] > 0.97: print(v)
EOF
)
    log "$b: two-slot check for: none ${WIN//$'\n'/ }"
    for v in none $WIN; do
      [[ -f $RUN/slots-$b-$v.json ]] && { log "$b-slots-$v: done in an earlier run; skipped"; continue; }
      # shellcheck disable=SC2086
      serve "$b" stock "$b-slots-$v" $CTX ${SPEC[$v]} || continue
      python3 "$P/llama_bench.py" http://127.0.0.1:8081 "$b-$v" 2 "$RUN/slots-$b-$v.json" > "$RUN/slots-$b-$v.out" 2>&1 \
        || log "$b-slots-$v: llama_bench failed"
      stop_server
    done
  fi

  if [[ " $SETS " == *" k4 "* ]]; then
    # shellcheck disable=SC2086
    spec "$b" stock "$b-stock-k4" $CTX --spec-type none $K4
    if [[ -f $M/${MFILE[edge0]} ]]; then
      # shellcheck disable=SC2086
      spec "$b" edge0 "$b-edge0-k4-lora" $CTX --spec-type none $E4 --lora "$EDGE0_LORA"
      # shellcheck disable=SC2086
      spec "$b" edge0 "$b-edge0-k4-nolora" $CTX --spec-type none $E4
      # shellcheck disable=SC2086
      spec "$b" edge0 "$b-edge0-k8-nolora" $CTX --spec-type none $E8
    else log "$b: Edge0 model not installed; Edge0 variants skipped"; fi
  fi

  if [[ " $SETS " == *" moe "* && $b == forktip ]]; then
    export GGML_CUDA_MOE_CACHE_MIN_EXPERT_KB=512 GGML_CUDA_MOE_CACHE_RESERVE_MB=512
    spec "$b" stock "$b-moecache" $CTX --spec-type none --moe-cache on
    unset GGML_CUDA_MOE_CACHE_MIN_EXPERT_KB GGML_CUDA_MOE_CACHE_RESERVE_MB
    grep -h -m3 -i 'moe-cache\|MoE cache' "$RUN/$b-moecache.server.log" | sed "s/^/$(date '+%F %T') speccheck: $b-moecache log: /"
  fi

  if [[ " $SETS " == *" cache "* ]]; then
    declare -A CACHE=([default]="" [cms0]="-cms 0" [nocache]="-cram 0")
    for v in default cms0 nocache; do
      [[ -f $RUN/cache-$b-$v.json ]] && { log "$b-cache-$v: done in an earlier run; skipped"; continue; }
      # shellcheck disable=SC2086
      serve "$b" stock "$b-cache-$v" $CTX --spec-type none ${CACHE[$v]} || continue
      python3 "$P/cache_bench.py" http://127.0.0.1:8081 "$b-$v" "$RUN/cache-$b-$v.json" || log "$b-cache-$v: cache_bench failed"
      stop_server
    done
  fi
done
