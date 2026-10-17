#!/usr/bin/env bash
# llamaprep.sh — prepare the llama.cpp bake-off (2026-10-05): three CUDA builds + the Edge0 35B model.
#   A. download Edge0/Edge0-35B-A3B-preview @3fe15cb (Apache-2.0, ~19.7 GB) in the background, SHA-256 checked
#   B. meanwhile build llama-server (CUDA sm_75, the current build's flags) in ~/Documents/repo/bench/llama-builds:
#        forktip      TheTom/llama-cpp-turboquant bcb85fc (turbo3 KV, --moe-cache)
#        upstream     ggml-org/llama.cpp 0504396
#        upstream-pd  upstream + thecodacus parallel-decision (hand-merged, task type renamed PARALLEL_DECISION)
#   C. convert with Edge0's own tools (bench/edge0-tools, Edge0 9a56e4d): MLX int4 → GGUF Q4_1 (K=4) + LoRA GGUF,
#      then install both 0444 into the models folder.
# No model server is started. kent-llama is stopped during the conversion only (oomd) and started again after.
# Start detached:  tests/bench/submit.sh llamaprep llamaprep.sh
set -uo pipefail
export PATH=/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin
BENCH=$HOME/Documents/repo/bench
RUN=$BENCH/runs/llamaprep-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/llamaprep-check.log" 2>&1
log() { echo "$(date '+%F %T') llamaprep: $*"; }
BUILDS=$BENCH/llama-builds
MODELS=/media/administrator/DATA/models
DL=$MODELS/.dl/edge0-35b            # the converter detects the tier from this directory name
REV=3fe15cbf2bd5bbcdfd611035e1ac88971ff1cdab
HF=https://huggingface.co/Edge0/Edge0-35B-A3B-preview/resolve/$REV
declare -A SHA=(
  [lora_edge0_35b.safetensors]=a407f6d27bb55e39e6461bd4bce1fd90489e70c7582b68a796fb764bdb441ec1
  [model-00001-of-00004.safetensors]=f7fff28fecb8221e53787c6339c938b113bcb94c7bbc60d09a8826eba08ef1ab
  [model-00002-of-00004.safetensors]=31dcdb1c49eebdb1505bd14e3cb33f9cf900bd2546b638f2464694ae763a033f
  [model-00003-of-00004.safetensors]=3e66de06a1f03dade16a612a368cfce4a4c9caa4efd7d28185454384082cec03
  [model-00004-of-00004.safetensors]=a5d0cf03519c26f8b506df6b0ba60526e5c08c8cea22d0c21ce92950e58a5422
  [prerouter_edge0_35b.safetensors]=93d07af08e1f5c8d174e941016740cb4d2c143c6b90f33581237679eca9b006f
  [tokenizer.json]=87a7830d63fcf43bf241c3c5242e96e62dd3fdc29224ca26fed8ea333db72de4
)
SMALL=(README.md chat_template.jinja config.json configuration.json generation_config.json
       model.safetensors.index.json preprocessor_config.json processor_config.json tokenizer_config.json vocab.json)
# the converter needs numpy: /usr/bin/python3 has none (2026-10-05 failure), the operator's ai-env has 2.4.4
PY=${PY:-$HOME/ai-env/bin/python3}
# lora_mlx_to_gguf.py's cross-check imports gguf-py (llama.cpp's Python package): use the upstream checkout's
export PYTHONPATH=$BUILDS/upstream/gguf-py
OUT_GGUF=$MODELS/Edge0-35B-A3B-preview-Q4_1.gguf
OUT_LORA=$MODELS/Edge0-35B-A3B-preview-lora-F16.gguf

verified() {
  for f in "${!SHA[@]}"; do echo "${SHA[$f]}  $DL/$f" | sha256sum -c --quiet - 2>/dev/null || return 1; done
}

download() {
  if verified; then log "download already complete and verified"; return 0; fi
  mkdir -p "$DL"
  for f in "${SMALL[@]}" "${!SHA[@]}"; do
    for attempt in 1 2 3 4 5; do
      curl -fsSL -C - --retry 5 -o "$DL/$f" "$HF/$f" && break
      log "download $f: attempt $attempt failed"; sleep 30
    done
  done
  for f in "${!SHA[@]}"; do
    echo "${SHA[$f]}  $DL/$f" | sha256sum -c --quiet - || { log "SHA-256 mismatch: $f"; return 1; }
  done
  log "download complete and verified ($(du -sh "$DL" | cut -f1))"
}

build() {  # build NAME
  local src=$BUILDS/$1
  if [[ -x $src/build-cuda/bin/llama-server && $(git -C "$src" log -1 --format=%ct) -lt $(stat -c %Y "$src/build-cuda/bin/llama-server") ]]; then
    log "build $1: already built at $(git -C "$src" log -1 --format=%h)"; return 0; fi
  log "build $1 ($(git -C "$src" log -1 --format='%h %cs'))"
  nice -n 10 cmake -S "$src" -B "$src/build-cuda" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 -DGGML_NATIVE=ON \
    -DBUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=Release > "$RUN/build-$1.log" 2>&1 \
    && nice -n 10 cmake --build "$src/build-cuda" --target llama-server -j8 >> "$RUN/build-$1.log" 2>&1 \
    && "$src/build-cuda/bin/llama-server" --version >> "$RUN/build-$1.log" 2>&1 \
    && { log "build $1 ok: $src/build-cuda/bin/llama-server"; return 0; }
  log "build $1 FAILED: $(grep -m3 -E 'error|Error' "$RUN/build-$1.log" | tr '\n' ' ')"; return 1
}

log "start"
"$PY" -c 'import numpy, gguf' || { log "$PY cannot import numpy and gguf"; exit 1; }
if [[ -f $OUT_GGUF && -f $OUT_LORA ]]; then log "Edge0 GGUF already installed; skipping download"; DLPID=
else download > "$RUN/download.log" 2>&1 & DLPID=$!; log "download started (pid $DLPID)"; fi

for b in forktip upstream upstream-pd; do build "$b"; done

if [[ -n ${DLPID:-} ]]; then
  wait "$DLPID" || { log "download failed: see $RUN/download.log"; exit 1; }
  # systemd-oomd killed the first conversion (13:13, memory pressure > 50% on the user session with kent-llama's
  # 16 GB resident): stop kent-llama while converting, start it again afterwards if it was running
  LLAMA_WAS_ACTIVE=0
  systemctl is-active --quiet kent-llama && LLAMA_WAS_ACTIVE=1
  trap '((LLAMA_WAS_ACTIVE)) && { kent llama start >/dev/null 2>&1 && log "kent-llama started again" || log "WARNING: kent llama start failed"; }' EXIT
  if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; log "kent-llama stopped for the conversion"; fi
  # the adapter cross-check reads the base from <tools>/../models/edge0-35b-gguf/ (Edge0's repo layout)
  mkdir -p "$BENCH/models" && ln -sfn "$DL-gguf" "$BENCH/models/edge0-35b-gguf"
  log "converting (Edge0 tools $(cat "$BENCH/edge0-tools/SOURCE"))"
  "$PY" "$BENCH/edge0-tools/convert_mlx_to_gguf.py" --dir "$DL" > "$RUN/convert.log" 2>&1 \
    || { log "convert FAILED: $(tail -3 "$RUN/convert.log" | tr '\n' ' ')"; exit 1; }
  mv "$DL-gguf/edge0-35b.gguf" "$OUT_GGUF" && mv "$DL-gguf/lora_edge0_35b-gguf.gguf" "$OUT_LORA" \
    && chmod 0444 "$OUT_GGUF" "$OUT_LORA" || { log "install failed"; exit 1; }
  log "installed: $(sha256sum "$OUT_GGUF" "$OUT_LORA" | tr '\n' ' ')"
fi
log "done: $RUN"
