#!/usr/bin/env bash
# pdbuildcuda.sh — CUDA llama-server for the parallel-decision (JEV) test on the production model (operator,
# 2026-10-16). Source: bench/llama-builds/v0.6.0-pd (v0.6.0 d812350 + pd-v0.6.0-patches, made by pdbuild.sh).
# Flags: exactly those of the production build (v0.6.0/BUILD-INFO, = /opt/kent-llama/bin/llama-server), so the
# only difference from production is the patches. Output: v0.6.0-pd/build-cuda/bin + BUILD-INFO.
# No model server is started or stopped (nice 19, -j6). Start detached: tests/bench/submit.sh pdbuildcuda pdbuildcuda.sh
set -uo pipefail
export PATH=/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin
LB=$HOME/Documents/repo/bench/llama-builds
SRC=$LB/v0.6.0-pd
LOG=$LB/pdbuildcuda.log
exec >>"$LOG" 2>&1
log() { echo "$(date '+%F %T') pdbuildcuda: $*"; }
SHA=d81235049384534c167caea52b85a694f6103d14
FLAGS=(-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=ON
       -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA_FA_QUANTS=all)
log "start"
[[ $(git -C "$SRC" rev-parse HEAD) == "$SHA" ]] || { log "source is not at $SHA"; exit 1; }
[[ -d $SRC/tools/parallel-decision ]] || { log "patches not applied in $SRC"; exit 1; }
want=$(grep -m1 '^flags ' "$LB/v0.6.0/BUILD-INFO" | cut -d' ' -f2-)
[[ $want == "${FLAGS[*]}" ]] || { log "flags differ from the production build: $want"; exit 1; }
t0=$(date +%s)
nice -n 19 cmake -S "$SRC" -B "$SRC/build-cuda" "${FLAGS[@]}" > "$SRC/cmake-cuda.log" 2>&1 \
  || { log "configure failed: $(grep -m3 -E 'error|Error' "$SRC/cmake-cuda.log" | tr '\n' ' ')"; exit 1; }
nice -n 19 cmake --build "$SRC/build-cuda" --target llama-server -j6 > "$SRC/build-cuda.log" 2>&1 \
  || { log "build failed: $(grep -m3 -E 'error' "$SRC/build-cuda.log" | tr '\n' ' ')"; exit 1; }
BIN=$SRC/build-cuda/bin
ver=$("$BIN/llama-server" --version 2>&1 | grep -m1 '^version')
"$BIN/llama-server" --help 2>&1 | grep -q -- '--decision-seqs' || { log "built binary has no --decision-seqs"; exit 1; }
{
  echo "source v0.6.0 commit $SHA + $(ls "$LB"/pd-v0.6.0-patches/*.patch | xargs -n1 basename | tr '\n' ' ')"
  echo "built $(date -Iseconds) $ver"
  echo "flags ${FLAGS[*]}"
  (cd "$BIN" && sha256sum llama-server ./*.so* 2>/dev/null)
} > "$SRC/BUILD-INFO-cuda"
log "built in $(( $(date +%s) - t0 )) s: $ver ($BIN/llama-server)"
