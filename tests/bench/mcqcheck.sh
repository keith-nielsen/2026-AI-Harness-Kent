#!/usr/bin/env bash
# mcqcheck.sh "MODELS" "SETS" [TASKS] — multiple-choice log-likelihood scoring of the Qwen3.6-35B-A3B quants with
# llama-perplexity (operator, 2026-10-07: get to the bottom of Q6 not beating Q4 on the probes). Deterministic: no
# sampling, no chat template; each question's answer options are scored by log-likelihood and the best one counts.
#   MODELS: q4 q6 q8 (the bench files, SHA-checked); q4kv84/88/44 = Q4 with -ctk/-ctv q8_0/q4_0, q8_0/q8_0, q4_0/q4_0. NP (env) = -np, default 5
#   (2026-10-13 runs: -np 4 for mmlu-val/winogrande/hellaswag, -np 5 for ARC). SETS (bench/mcq-data, hash-checked via MANIFEST.json):
#     hellaswag  10,042 HellaSwag validation     mmlu-val   1,531 MMLU validation      mmlu-test 14,042 MMLU test
#     arc-c      ARC-Challenge validation        arc-e      ARC-Easy validation        truthfulqa  817 TruthfulQA mc1
#     winogrande 1,267 Winogrande debiased
#   TASKS: questions per set (0 = all, the default); a subset is the same fixed-seed sample for every model.
#     A set can carry its own count as set:N (e.g. hellaswag:1500), overriding TASKS for that set.
#   Set-major order (every model through one set, then the next set): each finished set is a complete paired
#   comparison, so a run cut short by DEADLINE still gives whole sets.
#   DEADLINE (env, optional, e.g. 17:40; a bare time already past = tomorrow): no (model, set) starts with under 10 min left, and a running one is
#   stopped at the deadline (its log then lacks the final line -> INCOMPLETE in the report).
#   Memory (2026-10-13): RSS = weights + ~2 GB per -np sequence (logits buffer: 2048 x 248k vocab x 4 B); -np 16 put
#   Q4 at 51 GB, so -np 5 (ARC has 5-option questions; -np 4 = Q4 27 GB, Q6 36 GB): TruthfulQA (needs -np >= its option count, up to 16) cannot run in this RAM.
#   --no-host and --no-op-offload made no difference. Before: peak RSS per (model, set) from /usr/bin/time and
#   Rss{Anon,File,Shmem} sampled every 2 s to RUN/<set>-<model>.rss; run under submit.sh with MEMMAX so an
#   overshoot kills llama-perplexity only, not the host.
#   Build: upstream llama.cpp v0.6.0 (bench/llama-builds/v0.6.0). Offload as the bench: -ngl 99 -ncmoe 40, -fa on,
#   -t 8, -lm none (= no mmap; this build has no --no-mmap); f16 KV, context 2048 per sequence batch. -np 16: one sequence per answer option, so up to 4
#   four-option questions per batch and TruthfulQA questions with up to 16 options (default 4 refuses 5+ options).
#   --no-op-offload: this hybrid (recurrent) model splits each batch per answer option (split_equal,
#   llama-memory-hybrid.cpp:89), so micro-batches stay small whatever -b/-ub say; with op offload each one >= 32 tokens
#   copies all RAM-held experts to the GPU (ggml-cuda.cu:5928). -b/-ub 2048 bought nothing and cost 1.6 GB, so 512.
#   Batch composition tips ~3% of near-tie answers: keep these flags fixed across models.
#   Peak GPU memory per (model, set) is sampled every 0.5 s (nvidia-smi) and logged.
#   Per (model, set): RUN/<set>-<model>.log (llama-perplexity's own per-question lines); mcq_report.py turns them into
#   per-question results, accuracies with 95% intervals and paired McNemar tests -> RUN/report.md, RUN/per_item.csv.
#   Host: browsers and Discord closed, kent-llama stopped (GPU and RAM to itself), hostprofile.sh check (refuse on
#   mismatch), kent-llama started again at the end if it was running.
# Start detached:  tests/bench/submit.sh mcq mcqcheck.sh "q4 q6 q8" "hellaswag mmlu-val arc-c arc-e winogrande truthfulqa"
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"
BENCH=$HOME/Documents/repo/bench
RUN=$BENCH/runs/mcqcheck-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/mcqcheck.log" 2>&1
log() { echo "$(date '+%F %T') mcqcheck: $*"; }
read -r -a MODELS <<< "${1:?usage: mcqcheck.sh \"MODELS\" \"SETS\" [TASKS]}"
read -r -a SETS <<< "${2:?usage: mcqcheck.sh \"MODELS\" \"SETS\" [TASKS]}"
TASKS=${3:-0}
BIN=$BENCH/llama-builds/v0.6.0/build-cuda/bin
DATA=$BENCH/mcq-data
M=/srv/kent/models
declare -A FILE=([q4]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf [q4kv84]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
                 [q4kv88]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf [q4kv44]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf [q6]=Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf
                 [q8]=Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL.gguf)
declare -A SHA=([q4]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
                [q4kv84]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
                [q4kv88]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
                [q4kv44]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
                [q6]=35fce994cd36104a7dc1bd8a4bdf13778145664c00fdef6773aebc9246e5019c
                [q8]=6c6b816537abad90b250a0972b345466028d861ddfe316d5f0de31ca6440f781)
declare -A SETFILE=([hellaswag]=hellaswag-validation.bin [mmlu-val]=mmlu-validation.bin [mmlu-test]=mmlu-test.bin
                    [arc-c]=arc-challenge-validation.bin [arc-e]=arc-easy-validation.bin
                    [truthfulqa]=truthful-qa-validation.bin [winogrande]=winogrande-debiased-eval.csv)
# extra arguments per model name: the Q4 file with a quantized K/V cache, q4kv<K><V> (operator, 2026-10-13)
declare -A EXTRA=([q4kv84]="-ctk q8_0 -ctv q4_0" [q4kv88]="-ctk q8_0 -ctv q8_0" [q4kv44]="-ctk q4_0 -ctv q4_0")
ARGS=(-c 2048 -b 512 -ub 512 -np "${NP:-5}" -ngl 99 -ncmoe 40 -fa on -t 8 -lm none)

for m in "${MODELS[@]}"; do [[ -n ${FILE[$m]:-} ]] || { echo "mcqcheck: unknown model $m" >&2; exit 2; }; done
declare -A NTASK=()
for i in "${!SETS[@]}"; do
  s=${SETS[$i]%%:*}; [[ ${SETS[$i]} == *:* ]] && NTASK[$s]=${SETS[$i]#*:} || NTASK[$s]=$TASKS
  [[ ${NTASK[$s]} =~ ^[0-9]+$ ]] || { echo "mcqcheck: bad count in ${SETS[$i]}" >&2; exit 2; }
  SETS[$i]=$s
  [[ -n ${SETFILE[$s]:-} ]] || { echo "mcqcheck: unknown set $s" >&2; exit 2; }
done
END=0
[[ -n ${DEADLINE:-} ]] && { END=$(date -d "$DEADLINE" +%s) || exit 2; }
# a bare clock time already past means tomorrow (an overnight run started before midnight)
(( END > 0 && END <= $(date +%s) )) && [[ $DEADLINE =~ ^[0-9]{1,2}:[0-9]{2}$ ]] && END=$(date -d "tomorrow $DEADLINE" +%s)
# operator override for queued jobs: this file present = ignore DEADLINE (2026-10-13: "no hard stop, keep running")
[[ -e $BENCH/runs/NO-DEADLINE ]] && END=0
log "start: models [${MODELS[*]}], sets [$(for s in "${SETS[@]}"; do printf "%s:%s " "$s" "${NTASK[$s]}"; done)] (0 = all), deadline ${DEADLINE:-none}; args ${ARGS[*]}; build $(head -1 "$BENCH/llama-builds/v0.6.0/BUILD-INFO")"
[[ -x $BIN/llama-perplexity ]] || { log "missing $BIN/llama-perplexity"; exit 1; }
/usr/bin/python3 -I - "$DATA" "${SETS[@]}" <<'EOF' || { log "dataset hash check failed"; exit 1; }
import hashlib, json, sys
d, sets = sys.argv[1], sys.argv[2:]
names = {"hellaswag": "hellaswag-validation.bin", "mmlu-val": "mmlu-validation.bin", "mmlu-test": "mmlu-test.bin",
         "arc-c": "arc-challenge-validation.bin", "arc-e": "arc-easy-validation.bin",
         "truthfulqa": "truthful-qa-validation.bin", "winogrande": "winogrande-debiased-eval.csv"}
man = json.load(open(f"{d}/MANIFEST.json"))
for s in sets:
    f = names[s]
    if hashlib.sha256(open(f"{d}/{f}", "rb").read()).hexdigest() != man[f]["sha256"]:
        sys.exit(f"hash mismatch: {f}")
EOF
cp "$DATA/MANIFEST.json" "$RUN/datasets-MANIFEST.json"

LLAMA_WAS_ACTIVE=0
# a queued job can start while the previous job is still restarting kent-llama: let it settle first
for _ in $(seq 90); do [[ $(systemctl is-active kent-llama) == @(activating|deactivating|reloading) ]] || break; sleep 2; done
systemctl is-active --quiet kent-llama && LLAMA_WAS_ACTIVE=1
finish() {
  "$B/hostprofile.sh" check > "$RUN/hostprofile-end.txt" 2>&1 && log "host profile at end: OK" \
    || log "WARNING: host profile changed during the run: $(grep MISMATCH "$RUN/hostprofile-end.txt" | tr '\n' ' ')"
  if ((LLAMA_WAS_ACTIVE)); then kent llama start >/dev/null 2>&1 && log "kent-llama started again" \
    || log "WARNING: kent llama start failed"; fi
  /usr/bin/python3 "$B/mcq_report.py" "$RUN" > "$RUN/report.md" 2> "$RUN/report.err" \
    && log "report: $RUN/report.md" || log "report failed: $(tail -2 "$RUN/report.err" | tr '\n' ' ')"
  log "done: $RUN"
}
trap finish EXIT
# KEEP_APPS=1 (env): leave browsers and Discord open (short smoke tests while the operator works)
[[ ${KEEP_APPS:-0} == 1 ]] && log "KEEP_APPS=1: browsers and Discord left open" || \
for app in brave chrome chromium chromium-browse Discord; do
  pgrep -x "$app" >/dev/null && { pkill -x "$app"; log "closed $app (GPU memory and RAM)"; }
done
sleep 2
if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; fi
ss -ltnH '( sport = :8080 or sport = :8081 )' | grep -q . && { log "a bench model server is up; refusing"; exit 1; }
"$B/hostprofile.sh" check > "$RUN/hostprofile-start.txt" 2>&1 \
  || { log "refused: host profile mismatch: $(grep MISMATCH "$RUN/hostprofile-start.txt" | tr '\n' ' ')"; exit 1; }
log "host profile: OK; other GPU users: $(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader | tr '\n' ';')"

log "MemAvailable after stopping kent-llama: $(awk '/MemAvailable/ {printf "%.1f GB", $2/1048576}' /proc/meminfo); cgroup limit $(cat /sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.max 2>/dev/null || echo unknown)"

OK_MODELS=()
for m in "${MODELS[@]}"; do
  log "$m: checking SHA-256 of ${FILE[$m]}"
  echo "${SHA[$m]}  $M/${FILE[$m]}" | sha256sum -c --quiet - && OK_MODELS+=("$m") || log "$m: hash mismatch, skipped"
done
for s in "${SETS[@]}"; do
  for m in "${OK_MODELS[@]}"; do
    out=$RUN/$s-$m.log
    left=0
    if ((END)); then
      left=$(( END - $(date +%s) ))
      ((left >= 600)) || { log "$m $s: skipped, ${left} s to deadline"; continue; }
    fi
    if [[ $s == winogrande ]]; then mode=(--winogrande --winogrande-tasks "${NTASK[$s]}")
    else mode=(--multiple-choice --multiple-choice-tasks "${NTASK[$s]}"); fi
    t0=$(date +%s)
    nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -lms 500 > "$RUN/$s-$m.vram" 2>/dev/null &
    smi=$!
    ( while sleep 2; do p=$(pgrep -nx llama-perplexit) || continue
        echo "$(date +%s) $(grep -E '^Rss(Anon|File|Shmem)' /proc/$p/status 2>/dev/null | awk '{printf "%s %d ", $1, $2/1024}')"
      done ) > "$RUN/$s-$m.rss" &
    rss=$!
    LD_LIBRARY_PATH=$BIN timeout --signal=TERM --kill-after=30 "$( ((left)) && echo $left || echo 0 )" \
      /usr/bin/time -v -o "$RUN/$s-$m.time" \
      "$BIN/llama-perplexity" -m "$M/${FILE[$m]}" -f "$DATA/${SETFILE[$s]}" "${mode[@]}" "${ARGS[@]}" ${EXTRA[$m]:-} > "$out" 2>&1
    rc=$?
    kill "$smi" "$rss" 2>/dev/null; wait "$smi" "$rss" 2>/dev/null
    log "$m $s: rc $rc, $(( $(date +%s) - t0 )) s, peak GPU $(sort -n "$RUN/$s-$m.vram" | tail -1) MiB, peak RSS $(awk -F': ' '/Maximum resident/ {printf "%.1f GB", $2/1048576}' "$RUN/$s-$m.time" 2>/dev/null), $(grep -hE 'Final (result|Winogrande score)' "$out" | sed 's/^.*: //' | tr '\n' ' ')"
    ((rc == 124)) && log "$m $s: stopped at the deadline"
    ((rc == 137)) && log "$m $s: killed (memory limit or deadline kill)"
    grep -q 'E multiple_choice_score\|E winogrande_score' "$out" && log "$m $s: ERROR: $(grep -m1 -E ' E ' "$out" | cut -c1-200)"
  done
done
