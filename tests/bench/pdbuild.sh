#!/usr/bin/env bash
# pdbuild.sh — CPU-only llama-server builds for the parallel-decision normal-path check (JEV step 1-2, operator
# 2026-10-13): v0.6.0-cpu (stock v0.6.0) and v0.6.0-pd (v0.6.0 + bench/llama-builds/pd-v0.6.0-patches). Same
# source commit, same flags, so any output difference comes from the patches. Lowest CPU priority, 3 jobs:
# safe while the operator works. Start detached: tests/bench/submit.sh pdbuild pdbuild.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
LB=$HOME/Documents/repo/bench/llama-builds
LOG=$LB/pdbuild.log
exec >>"$LOG" 2>&1
log() { echo "$(date '+%F %T') pdbuild: $*"; }
log "start"
for v in cpu pd; do
  d=$LB/v0.6.0-$v
  if [[ ! -d $d/.git ]]; then
    git clone -q --shared "$LB/v0.6.0" "$d" && git -C "$d" checkout -q d81235049384534c167caea52b85a694f6103d14 \
      || { log "$v: clone failed"; exit 1; }
    if [[ $v == pd ]]; then
      git -C "$d" apply "$LB"/pd-v0.6.0-patches/*.patch || { log "pd: patches do not apply"; exit 1; }
      log "pd: patches applied: $(ls "$LB"/pd-v0.6.0-patches/*.patch | xargs -n1 basename | tr '\n' ' ')"
    fi
  fi
  t0=$(date +%s)
  nice -n 19 cmake -S "$d" -B "$d/build-cpu" -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DLLAMA_CURL=OFF \
    -DGGML_NATIVE=ON -DLLAMA_BUILD_TESTS=OFF > "$d/cmake.log" 2>&1 || { log "$v: cmake failed (see $d/cmake.log)"; exit 1; }
  nice -n 19 cmake --build "$d/build-cpu" --target llama-server -j 3 > "$d/build.log" 2>&1 \
    || { log "$v: build failed: $(grep -m1 -E 'error' "$d/build.log")"; exit 1; }
  log "$v: built llama-server in $(( $(date +%s) - t0 )) s: $d/build-cpu/bin/llama-server"
done
log "done"
