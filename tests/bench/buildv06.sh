#!/usr/bin/env bash
# buildv06.sh — build upstream llama.cpp v0.6.0 for the KV cache tests (2026-10-06).
#   Source: ggml-org/llama.cpp tag v0.6.0, pinned to commit d81235049384 (checked after the clone).
#   Build:  CUDA sm_75, the same flags as llamaprep.sh, plus GGML_CUDA_FA_QUANTS=all so every K/V cache type pair
#           (q8_0 q5_1 q5_0 q4_1 q4_0 f16 bf16, mixed) has a flash-attention kernel; the default only has same-type
#           q4_0/q8_0/f16/bf16 pairs. Targets: llama-server, llama-batched-bench, llama-perplexity.
#   Output: ~/Documents/repo/bench/llama-builds/v0.6.0/build-cuda/bin + BUILD-INFO (commit, flags, sha256).
# No model server is started or stopped: production kent-llama keeps running (build at nice 19, -j6).
# Start detached:  tests/bench/submit.sh buildv06 buildv06.sh
set -uo pipefail
export PATH=/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin
BENCH=$HOME/Documents/repo/bench
RUN=$BENCH/runs/buildv06-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/buildv06-check.log" 2>&1
log() { echo "$(date '+%F %T') buildv06: $*"; }
TAG=v0.6.0
SHA=d81235049384534c167caea52b85a694f6103d14
SRC=$BENCH/llama-builds/$TAG
FLAGS=(-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=ON
       -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA_FA_QUANTS=all)
TARGETS=(llama-server llama-batched-bench llama-perplexity)

log "start"
if [[ ! -d $SRC/.git ]]; then
  git clone -q --depth 1 --branch "$TAG" https://github.com/ggml-org/llama.cpp "$SRC" > "$RUN/clone.log" 2>&1 \
    || { log "clone FAILED: $(tail -2 "$RUN/clone.log" | tr '\n' ' ')"; exit 1; }
fi
got=$(git -C "$SRC" rev-parse HEAD)
[[ $got == "$SHA" ]] || { log "commit mismatch: $TAG is $got, expected $SHA"; exit 1; }
log "source $TAG at $got"

log "configure: ${FLAGS[*]}"
nice -n 19 cmake -S "$SRC" -B "$SRC/build-cuda" "${FLAGS[@]}" > "$RUN/build.log" 2>&1 \
  || { log "configure FAILED: $(grep -m3 -E 'error|Error' "$RUN/build.log" | tr '\n' ' ')"; exit 1; }
grep -q 'GGML_CUDA_FA_QUANTS:STRING=all' "$SRC/build-cuda/CMakeCache.txt" || { log "FA_QUANTS not set to all"; exit 1; }
log "build: ${TARGETS[*]} (nice 19, -j6)"
nice -n 19 cmake --build "$SRC/build-cuda" --target "${TARGETS[@]}" -j6 >> "$RUN/build.log" 2>&1 \
  || { log "build FAILED: $(grep -m3 -E 'error|Error' "$RUN/build.log" | tr '\n' ' ')"; exit 1; }

BIN=$SRC/build-cuda/bin
ver=$("$BIN/llama-server" --version 2>&1 | grep -m1 '^version')
log "built: $ver"
{
  echo "source https://github.com/ggml-org/llama.cpp tag $TAG commit $got"
  echo "built $(date -Iseconds) $ver"
  echo "flags ${FLAGS[*]}"
  (cd "$BIN" && sha256sum "${TARGETS[@]}" ./*.so* 2>/dev/null)
} > "$SRC/BUILD-INFO"
log "done: $SRC/BUILD-INFO"
