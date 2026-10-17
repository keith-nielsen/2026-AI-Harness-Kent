#!/usr/bin/env bash
# kvcheck.sh "VARIANTS" [CTX] [q4|q6] — KV cache type test on upstream llama.cpp v0.6.0 (operator, 2026-10-06).
#   VARIANTS: K-V cache type pairs, e.g. "q8_0-q8_0 q8_0-q4_0" (run in that order), optionally with the MTP draft
#   context's own cache types (-ctkd/-ctvd; default f16, they do not follow -ctk/-ctv): "q8_0-q8_0+q8_0-q8_0".
#   Every variant: production arguments (Q4 MTP model, -ngl 99 -ncmoe 40, 2 slots -kvu, -t 5 no -tb, -ub 1024,
#   -fa on, -rea off + production sampling) with MTP 2 drafts, p_min 0, -lm none (v0.6.0 has no --no-mmap), -lv 4
#   (v0.6.0 logs llama.cpp info such as the KV cache size at level 4, "trace").
#   Per variant:
#     size   the server's own KV cache line (K and V MiB), attention rotation, CUDA buffers, VRAM used/free after
#            warm-up and the peak over the run (nvidia-smi every 2 s -> vram-<variant>.csv)
#     speed  tests/perf/depth_probe.py: 8 x 8K, 4 x 16K, 2 x 32K, 1 x 64K prompts, copy + prose, greedy + sampled
#            (decode tok/s, prefill, MTP drafts accepted per request) -> depth.csv, report.md
#   Context: CTX if given; otherwise 256k, unless the FIRST variant fails at 256k (does not start, or dies during the
#   depth probe) -> it is rerun and every variant runs at 128k (operator: fit Q8 K+V at 256k, else all at 128k).
# Host settings are never changed here: hostprofile.sh check after `kent llama stop` (refuse on mismatch), at the end.
# Brave, Chrome, Chromium and Discord are killed at the start if running (their GPU memory skews the fit).
# Model (2026-10-07): q4 (default, the production MTP file) or q6 (UD-Q6_K_XL, same MTP head; JEV-gate candidate);
#   q6 variant names start with "q6-". Every other argument stays the production one.
# Start detached:  tests/bench/submit.sh kv kvcheck.sh "q8_0-q8_0"
#                  tests/bench/submit.sh kvq6 kvcheck.sh "q8_0-q4_0" 131072 q6
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"; P="$B/../perf"
BENCH=$HOME/Documents/repo/bench
RUN=$BENCH/runs/kvcheck-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/kvcheck.log" 2>&1
log() { echo "$(date '+%F %T') kvcheck: $*"; }
read -r -a VARIANTS <<< "${1:?usage: kvcheck.sh \"K-V ...\" [CTX]}"
FORCE_CTX=${2:-}
MSEL=${3:-q4}
M=/srv/kent/models
case $MSEL in
  q4) MODEL=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf MODEL_SHA=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1 MTAG= ;;
  q6) MODEL=Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf MODEL_SHA=35fce994cd36104a7dc1bd8a4bdf13778145664c00fdef6773aebc9246e5019c MTAG=q6- ;;
  *) echo "kvcheck: model must be q4 or q6" >&2; exit 2 ;;
esac
CORPUS=$BENCH/corpus/kent-repo-1a9b067.txt
CORPUS_SHA=9316541c430fd067
BIN=$BENCH/llama-builds/v0.6.0/build-cuda/bin
CTX=${FORCE_CTX:-262144}
TYPES=" f16 q8_0 q5_1 q5_0 q4_1 q4_0 "
PID= VPID=

stop_server() {
  [[ -n $PID ]] && { kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; }
  [[ -n $VPID ]] && { kill "$VPID" 2>/dev/null; wait "$VPID" 2>/dev/null; }
  PID= VPID=
  for _ in $(seq 30); do ss -ltnH '( sport = :8081 )' | grep -q . || break; sleep 1; done
}

serve() {  # serve NAME CTK CTV [CTKD CTVD]
  local name=$1 ctk=$2 ctv=$3 draft=()
  [[ -n ${4:-} ]] && draft=(-ctkd "$4" -ctvd "$5")
  ss -ltnH '( sport = :8081 )' | grep -q . && { log "port 8081 in use"; return 1; }
  # straight to the file: a pipeline here made `wait` hang (nvidia-smi ignores the closed pipe, 2026-10-06)
  nvidia-smi --query-gpu=timestamp,memory.used,memory.free --format=csv,noheader,nounits -lms 2000 \
    -f "$RUN/vram-$name.csv" &
  VPID=$!
  LD_LIBRARY_PATH=$BIN "$BIN/llama-server" -m "$M/$MODEL" --alias locally-run-model \
    -ngl 99 -ncmoe 40 -c "$CTX" -t 5 -ub 1024 --jinja -lm none --parallel 2 -kvu -fa on -ctk "$ctk" -ctv "$ctv" \
    --spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0 "${draft[@]}" \
    -rea off --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.00 --presence-penalty 0.1 --repeat-penalty 1.0 \
    --host 127.0.0.1 --port 8081 -lv 4 > "$RUN/$name.server.log" 2>&1 &
  PID=$!
  for _ in $(seq 900); do
    if curl -sf 127.0.0.1:8081/health >/dev/null; then
      log "$name up (ctx $CTX): vram used/free MiB $(nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader,nounits)"
      log "$name size: $(grep -o 'llama_kv_cache: size = .*' "$RUN/$name.server.log" | sed 's/  */ /g' | tr '\n' ';')"
      log "$name rotation: $(grep -oE 'n_rot_[kv] = [0-9]+' "$RUN/$name.server.log" | sort -u | tr '\n' ' ')"
      log "$name buffers: $(grep -E 'CUDA0 (model|KV|compute) buffer size' "$RUN/$name.server.log" | sed 's/^.*: *//' | sort -u | tr '\n' ';')"
      grep -qi 'flash.attn.*\(disabled\|not supported\)\|falling back to CPU' "$RUN/$name.server.log" \
        && log "$name WARNING: flash attention / CPU fallback message in the log"
      return 0
    fi
    kill -0 "$PID" 2>/dev/null || { log "$name: server died: $(grep -m3 -iE 'out of memory|error|failed' "$RUN/$name.server.log" | tr '\n' ' ')"; PID=; stop_server; return 1; }
    sleep 1
  done
  log "$name: not healthy after 900 s"; stop_server; return 1
}

run_variant() {  # run_variant K-V[+KD-VD] -> 0 ok, 1 failed to start, 2 died during the probe
  local main=${1%%+*} dr= ctk ctv ctkd= ctvd= name
  [[ $1 == *+* ]] && dr=${1#*+}
  ctk=${main%%-*} ctv=${main##*-}; [[ -n $dr ]] && { ctkd=${dr%%-*}; ctvd=${dr##*-}; }
  name="${MTAG}v06-$main${dr:+-d$dr}-$((CTX / 1024))k"
  serve "$name" "$ctk" "$ctv" $ctkd $ctvd || return 1
  python3 "$P/depth_probe.py" http://127.0.0.1:8081 "$name" 1 "$RUN/depth.csv" "$PID" "$CORPUS" \
    > "$RUN/depth-$name.out" 2>&1 || log "$name: depth_probe failed: $(tail -2 "$RUN/depth-$name.out" | tr '\n' ' ')"
  local alive=1; kill -0 "$PID" 2>/dev/null || alive=0
  log "$name: $(grep -c ' tok/s' "$RUN/depth-$name.out") requests, flagged: $(grep ' tok/s' "$RUN/depth-$name.out" | grep -vc ' clean$'), peak vram used $(cut -d, -f2 "$RUN/vram-$name.csv" | sort -n | tail -1 | tr -d ' ') MiB"
  stop_server
  ((alive)) || { log "$name: server died during the probe: $(grep -m2 -iE 'out of memory|error' "$RUN/$name.server.log" | tr '\n' ' ')"; return 2; }
  return 0
}

LLAMA_WAS_ACTIVE=0
systemctl is-active --quiet kent-llama && LLAMA_WAS_ACTIVE=1
finish() {
  stop_server
  "$B/hostprofile.sh" check > "$RUN/hostprofile-end.txt" 2>&1 && log "host profile at end: OK" \
    || log "WARNING: host profile changed during the run: $(grep MISMATCH "$RUN/hostprofile-end.txt" | tr '\n' ' ')"
  if ((LLAMA_WAS_ACTIVE)); then kent llama start >/dev/null 2>&1 && log "kent-llama started again" \
    || log "WARNING: kent llama start failed"; fi
  [[ -s $RUN/depth.csv ]] && { python3 "$P/depth_report.py" "$RUN" > "$RUN/report.md" 2>&1 && log "report: $RUN/report.md" || log "report failed"; }
  log "done: $RUN"
}

for v in "${VARIANTS[@]}"; do
  for pair in ${v/+/ }; do
    [[ $pair == *-* && $TYPES == *" ${pair%%-*} "* && $TYPES == *" ${pair##*-} "* ]] \
      || { echo "kvcheck: bad variant $v (K-V[+KD-VD] from:$TYPES)" >&2; exit 2; }
  done
done
log "start: model $MODEL; variants [${VARIANTS[*]}] ctx ${FORCE_CTX:-auto (256k, 128k if ${VARIANTS[0]} does not fit)}; build $(head -1 "$BENCH/llama-builds/v0.6.0/BUILD-INFO" 2>/dev/null)"
[[ -x $BIN/llama-server && -s $BENCH/llama-builds/v0.6.0/BUILD-INFO ]] || { log "no finished v0.6.0 build (BUILD-INFO missing)"; exit 1; }
echo "$MODEL_SHA  $M/$MODEL" | sha256sum -c --quiet - || { log "hash mismatch for $MODEL"; exit 1; }
[[ "$(sha256sum "$CORPUS" | cut -c1-16)" == "$CORPUS_SHA" ]] || { log "corpus hash mismatch"; exit 1; }
trap finish EXIT
# GPU memory taint (operator, 2026-10-06): browsers and Discord must not run during the test; kill them if they do
for app in brave chrome chromium chromium-browse Discord; do
  pgrep -x "$app" >/dev/null && { pkill -x "$app"; log "killed $app (GPU memory taint)"; }
done
sleep 2
if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; fi
if ! "$B/hostprofile.sh" check > "$RUN/hostprofile-start.txt" 2>&1; then
  log "refused: host profile mismatch after stopping kent-llama: $(grep MISMATCH "$RUN/hostprofile-start.txt" | tr '\n' ' ')"; exit 1
fi
log "host profile: OK; other GPU users: $(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader | tr '\n' ';')"

first=1
for v in "${VARIANTS[@]}"; do
  run_variant "$v"; rc=$?
  if ((first && rc != 0)) && [[ -z $FORCE_CTX && $CTX == 262144 ]]; then
    log "FIT: $v does not hold at 256k (rc $rc): every variant runs at 128k"
    CTX=131072; run_variant "$v" || log "$v failed at 128k too"
  elif ((first)) && [[ -z $FORCE_CTX ]]; then
    log "FIT: $v holds at $((CTX / 1024))k"
  fi
  first=0
done
