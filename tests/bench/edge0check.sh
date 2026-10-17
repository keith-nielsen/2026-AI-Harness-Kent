#!/usr/bin/env bash
# edge0check.sh — does the Edge0 model load with its recovery LoRA, and how fast is K=4? (operator, 2026-10-05)
# One server per variant, production arguments (256k, 2 slots, no -tb), then tests/perf/mtp_probe.py on edit +
# agentic + prose (greedy), one round:
#   edge0-k4-lora  Edge0-35B-A3B-preview Q4_1, 4 experts per token, + its LoRA   (upstream build: qwen3next arch)
#   edge0-k4       the same without the LoRA
#   q4-k4          our Q4 (UD-Q4_K_XL) forced to 4 experts per token          (production build)
#   q4             our Q4 as production runs it (8 experts), the reference
#   q4-up, q4-up-k4  our Q4 on the UPSTREAM build (K=8, K=4; q8_0 V: upstream has no turbo3) — is the newer build
#                  itself faster? (Edge0 without LoRA on upstream was 24% faster than our Q4 K=4 on production)
# Each server's load log is kept and summarised (architecture, expert count, LoRA lines, VRAM).
# Host settings are never changed here: hostprofile.sh check after `kent llama stop` (refuse on mismatch), at the end.
# Optional argument: the variants to run (default: all four), e.g. edge0check.sh "edge0-k4-lora edge0-k4".
# The upstream build has no --no-mmap: its equivalent is --load-mode none (-lm none).
# Start detached:  tests/bench/submit.sh edge0 edge0check.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"; P="$B/../perf"
RUN=$HOME/Documents/repo/bench/runs/edge0check-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/edge0check.log" 2>&1
log() { echo "$(date '+%F %T') edge0check: $*"; }
M=/srv/kent/models
UP=$HOME/Documents/repo/bench/llama-builds/upstream/build-cuda/bin
PROD=/opt/kent-llama/bin
declare -A FILE=([edge0-k4-lora]=Edge0-35B-A3B-preview-Q4_1.gguf [edge0-k4]=Edge0-35B-A3B-preview-Q4_1.gguf
                 [q4-k4]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf [q4]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
                 [q4-up]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf [q4-up-k4]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf)
declare -A BIN=([edge0-k4-lora]=$UP [edge0-k4]=$UP [q4-k4]=$PROD [q4]=$PROD [q4-up]=$UP [q4-up-k4]=$UP)
declare -A LIB=([edge0-k4-lora]=$UP [edge0-k4]=$UP [q4-k4]=/opt/kent-llama/lib [q4]=/opt/kent-llama/lib [q4-up]=$UP [q4-up-k4]=$UP)
declare -A LOADOPT=([edge0-k4-lora]="-lm none" [edge0-k4]="-lm none" [q4-k4]="--no-mmap" [q4]="--no-mmap"
                    [q4-up]="-lm none" [q4-up-k4]="-lm none")
VARIANTS=${1:-edge0-k4-lora edge0-k4 q4-k4 q4}
declare -A CTV=([edge0-k4-lora]=q8_0 [edge0-k4]=q8_0 [q4-k4]=turbo3 [q4]=turbo3 [q4-up]=q8_0 [q4-up-k4]=q8_0)
declare -A EXTRA=([edge0-k4-lora]="--override-kv qwen3next.expert_used_count=int:4 --lora $M/Edge0-35B-A3B-preview-lora-F16.gguf"
                  [edge0-k4]="--override-kv qwen3next.expert_used_count=int:4"
                  [q4-k4]="--override-kv qwen35moe.expert_used_count=int:4" [q4]=""
                  [q4-up]="" [q4-up-k4]="--override-kv qwen35moe.expert_used_count=int:4")
PID=

stop_server() {
  [[ -n $PID ]] && { kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; }
  PID=
  for _ in $(seq 30); do ss -ltnH '( sport = :8081 )' | grep -q . || break; sleep 1; done
}

serve() {  # serve VARIANT — verbose load log (-lv 3) so the LoRA and expert-count lines are visible
  local v=$1
  ss -ltnH '( sport = :8081 )' | grep -q . && { log "port 8081 in use"; return 1; }
  # shellcheck disable=SC2086
  LD_LIBRARY_PATH=${LIB[$v]} "${BIN[$v]}/llama-server" -m "$M/${FILE[$v]}" --alias locally-run-model -ngl 99 -ncmoe 40 \
    -c 262144 -t 5 -ub 1024 --jinja ${LOADOPT[$v]} --parallel 2 -kvu -fa on -ctk q8_0 -ctv "${CTV[$v]}" --spec-type none \
    ${EXTRA[$v]} -rea off --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.00 --presence-penalty 0.1 --repeat-penalty 1.0 \
    --host 127.0.0.1 --port 8081 -lv 3 > "$RUN/$v.server.log" 2>&1 &
  PID=$!
  for _ in $(seq 600); do
    curl -sf 127.0.0.1:8081/health >/dev/null && { log "$v up: vram $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"; return 0; }
    kill -0 "$PID" 2>/dev/null || { log "$v: server died: $(grep -i -E 'error|fail' "$RUN/$v.server.log" | tail -3 | tr '\n' ' ')"; PID=; return 1; }
    sleep 1
  done
  log "$v: not healthy after 600 s"; stop_server; return 1
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

log "start (kent-llama was $( ((LLAMA_WAS_ACTIVE)) && echo active || echo stopped))"
trap finish EXIT
if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; fi
if ! "$B/hostprofile.sh" check > "$RUN/hostprofile-start.txt" 2>&1; then
  log "refused: host profile mismatch after stopping kent-llama: $(grep MISMATCH "$RUN/hostprofile-start.txt" | tr '\n' ' ')"; exit 1
fi
log "host profile: OK"

for v in $VARIANTS; do
  serve "$v" || continue
  log "$v load: $(grep -h -i -E 'general.architecture|expert_used_count|n_expert_used|lora|adapter' "$RUN/$v.server.log" \
    | sed 's/  */ /g' | sort -u | head -8 | tr '\n' ';')"
  WORKLOADS=edit,agentic,prose python3 "$P/mtp_probe.py" http://127.0.0.1:8081 "$v" "$RUN/probe-$v.json" "$PID" \
    > "$RUN/probe-$v.out" 2>&1 || log "$v: probe failed: $(tail -2 "$RUN/probe-$v.out" | tr '\n' ' ')"
  log "$v: $(grep ' tok/s ' "$RUN/probe-$v.out" | sed 's/  */ /g' | cut -d' ' -f2-5 | tr '\n' ';')"
  stop_server
done
