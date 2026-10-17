#!/usr/bin/env bash
# mutt.sh start stock|carnice|occamy|stock-q6|stock-q8|nemotron|stock-k4|edge0-k4-lora|edge0-k4|stock-mtp|q6-mtp|kv-q8q8|kv-q8q4|kv-q51q4|kv-q6q8q4|kv-q6q8q8|strata-a RUN_DIR | stop | status
# Serve one model under test (MUTT) for the bench: llama-server on 127.0.0.1:8081 with exactly the
# tests/perf arguments (256k, 2 slots, -rea off, production sampling; every MoE expert layer on the CPU:
# -ncmoe 40 for the Qwen3.6 models (40 layers + an unused MTP head), 52 for Nemotron's hybrid blocks; its MTP head is not loaded), no
# mmproj for any model, and the recording proxy on 127.0.0.1:8080 (where the gateway's dev config points).
# LV=3 (environment) logs llama.cpp's load details (memory breakdown) for fit tests; default 1 = errors only.
# kent-llama must be stopped first (`kent llama stop`). Each start checks the model's SHA-256.
# K=4 entries (2026-10-05, Edge0 review): stock-k4 = the stock file with 4 experts per token; edge0-k4(-lora) =
# Edge0-35B-A3B-preview repacked to GGUF Q4_1 (llamaprep.sh) on the upstream build, with/without its recovery LoRA.
# Edge0's SHA-256 comes from its conversion manifest (an empty SHA entry = read it there).
# MTP entries (2026-10-05): stock-mtp = Q4 + MTP 2 drafts, p_min 0.8; q6-mtp = Q6 + the same, at 128k (Q6 + MTP
# at 256k failed to start once: ~0.5 GB VRAM margin). No -tb any more (batch threads = -t 5, as production will).
# KV entries (2026-10-06, kvcheck.sh): kv-q8q8 / kv-q8q4 = Q4 + MTP 2 drafts, p_min 0, on upstream v0.6.0 (built with every
# flash-attention K/V pair), K q8_0 and V q8_0 / q4_0; kv-q51q4 = K q5_1, V q4_0. MTP draft cache stays f16 (default).
# kv-q6q8q4 (2026-10-07) = kv-q8q4 with the Q6 file (same MTP head): the JEV-gate candidate, run at KV_CTX=131072;
# kv-q6q8q8 = the same with V q8_0.
# Context: KV_CTX (environment), default 256k.
# Strata entries (2026-10-07, operator: Qwen3.8-Flash-Next on the probes only, to compare raw ability with Qwen3.6):
# strata-a = Strata (image strata:82f46a8) with Qwen3.8-Flash-Next GSQ-RCO IQ3_XXS, config variant A (stratacfg.sh:
# KV int8 + streaming 20480, 128k, MTP 4 drafts, English draft vocabulary, no vision, --prefill 512), in the locked-down
# container (stratanet.sh: no internet, attempts logged to RUN_DIR/strata-net/sink.jsonl). Browsers and Discord are
# closed first (Strata uses ~60 of the 62 GB RAM). The proxy adds the API key and, where a request has none, thinking
# off and the Qwen3.6 runs' llama-server sampling (temp 0.7, top-p 0.8, top-k 20, presence 0.1). One request at a time.
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
M=/srv/kent/models
PY=/usr/bin/python3
declare -A FILE=(
  [stock]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
  [carnice]=Carnice-Qwen3.6-MoE-35B-A3B-APEX-I-Quality.gguf
  [occamy]=Accio-Lab_occamy-1.0-Q4_K_M.gguf
  [stock-q6]=Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf
  [stock-q8]=Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL.gguf
  [nemotron]=NVIDIA-Nemotron-3.5-Lightning-30B-A3B-Q4_K_M.gguf
  [stock-k4]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
  [edge0-k4-lora]=Edge0-35B-A3B-preview-Q4_1.gguf
  [edge0-k4]=Edge0-35B-A3B-preview-Q4_1.gguf
  [stock-mtp]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
  [q6-mtp]=Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf
  [kv-q8q8]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
  [kv-q8q4]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
  [kv-q51q4]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
  [kv-q6q8q4]=Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf
  [kv-q6q8q8]=Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf
  [strata-a]=Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf
)
declare -A SHA=(
  [stock]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
  [carnice]=f1fef05db8f1fd8d6b346320af393e64ab91c837ec621c09f5bcf99ece837876
  [occamy]=a1c8f49661c12c219cf2dfe2e9c9ee6ebeddccb351e979f191e24d89cd0727c6
  [stock-q6]=35fce994cd36104a7dc1bd8a4bdf13778145664c00fdef6773aebc9246e5019c
  [stock-q8]=6c6b816537abad90b250a0972b345466028d861ddfe316d5f0de31ca6440f781
  [nemotron]=66a1746c559969e0d737d660e698b85bea8ee382ceadf4c1238be91c946ae38d
  [stock-k4]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
  [edge0-k4-lora]=""
  [edge0-k4]=""
  [stock-mtp]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
  [q6-mtp]=35fce994cd36104a7dc1bd8a4bdf13778145664c00fdef6773aebc9246e5019c
  [kv-q8q8]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
  [kv-q8q4]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
  [kv-q51q4]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
  [kv-q6q8q4]=35fce994cd36104a7dc1bd8a4bdf13778145664c00fdef6773aebc9246e5019c
  [kv-q6q8q8]=35fce994cd36104a7dc1bd8a4bdf13778145664c00fdef6773aebc9246e5019c
  [strata-a]=219ea929900dfa9ef091f3aa473fdba6874b65fcb36526d7d851ac9e95856d15
)
UPSTREAM=$HOME/Documents/repo/bench/llama-builds/upstream/build-cuda/bin
V06=$HOME/Documents/repo/bench/llama-builds/v0.6.0/build-cuda/bin
EDGE0_MANIFEST=/media/administrator/DATA/models/.dl/edge0-35b-gguf/manifest.json
declare -A EXTRA=(
  [stock-k4]="--override-kv qwen35moe.expert_used_count=int:4"
  [edge0-k4-lora]="--override-kv qwen3next.expert_used_count=int:4 --lora $M/Edge0-35B-A3B-preview-lora-F16.gguf"
  [edge0-k4]="--override-kv qwen3next.expert_used_count=int:4"
  [stock-mtp]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.8"
  [q6-mtp]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.8"
  [kv-q8q8]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0"
  [kv-q8q4]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0"
  [kv-q51q4]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0"
  [kv-q6q8q4]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0"
  [kv-q6q8q8]="--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0"
)
declare -A CTXM=([q6-mtp]=131072 [kv-q8q8]=${KV_CTX:-262144} [kv-q8q4]=${KV_CTX:-262144} [kv-q51q4]=${KV_CTX:-262144} [kv-q6q8q4]=${KV_CTX:-262144} [kv-q6q8q8]=${KV_CTX:-262144})
declare -A BINDIR=([edge0-k4-lora]=$UPSTREAM [edge0-k4]=$UPSTREAM [kv-q8q8]=$V06 [kv-q8q4]=$V06 [kv-q51q4]=$V06 [kv-q6q8q4]=$V06 [kv-q6q8q8]=$V06)
declare -A CTV=([edge0-k4-lora]=q8_0 [edge0-k4]=q8_0 [kv-q8q8]=q8_0 [kv-q8q4]=q4_0 [kv-q51q4]=q4_0 [kv-q6q8q4]=q4_0 [kv-q6q8q8]=q8_0)
declare -A CTK=([kv-q51q4]=q5_1)
declare -A NCMOE=([stock]=40 [carnice]=40 [occamy]=40 [stock-q6]=40 [stock-q8]=40 [nemotron]=52 [stock-k4]=40
                  [edge0-k4-lora]=40 [edge0-k4]=40 [stock-mtp]=40 [q6-mtp]=40 [kv-q8q8]=40 [kv-q8q4]=40 [kv-q51q4]=40 [kv-q6q8q4]=40 [kv-q6q8q8]=40)
STATE=${XDG_RUNTIME_DIR:-/tmp}/kent-bench-mutt
STRATA_DATA=/media/administrator/DATA/strata-data
STRATA_IMAGE=strata:82f46a8
declare -A STRATA_VARIANT=([strata-a]=A)
STRATA_PARAMS='{"reasoning_effort": "none", "temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 0.1}'
die() { echo "mutt: $*" >&2; exit 1; }

strata_start() {  # strata_start NAME RUN_DIR (kent-llama and port 8080 already checked)
  local name=$1 run=$2 v=${STRATA_VARIANT[$1]} up=0
  docker inspect strata >/dev/null 2>&1 && die "a container named strata exists already"
  [[ -f $STRATA_DATA/config/variants/$v.json ]] || die "no Strata variant $v (run stratacfg.sh)"
  echo "mutt: checking SHA-256 of ${FILE[$name]}"
  echo "${SHA[$name]}  $STRATA_DATA/models/IQ3_XXS/${FILE[$name]}" | sha256sum -c --quiet - || die "hash mismatch for ${FILE[$name]}"
  for app in brave chrome chromium chromium-browse Discord; do
    pgrep -x "$app" >/dev/null && { pkill -x "$app"; echo "mutt: closed $app (RAM for Strata)"; }
  done
  "$D/stratanet.sh" up "$run/strata-net" > "$run/stratanet.txt" 2>&1 || die "stratanet up failed: $(tr '\n' ' ' < "$run/stratanet.txt")"
  touch "$STATE/strata"
  docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges -v "$STRATA_DATA":/data \
    --entrypoint sh "$STRATA_IMAGE" -c "cp /data/config/variants/$v.json /data/config/strata-iq3_xxs.json && chmod 0600 /data/config/strata-iq3_xxs.json" \
    || { stop; die "cannot install variant $v"; }
  docker run -d --name strata --network strata-net --ip 172.30.99.10 -v "$HOME/Documents/repo/bench/strata-resolv.conf":/etc/resolv.conf:ro \
    --gpus device=0 --cap-drop ALL --security-opt no-new-privileges --ulimit memlock=-1 --pids-limit 4096 \
    -v "$STRATA_DATA":/data -e FAMILY=qwen -e MODEL=IQ3_XXS -e PORT=8080 -e GPU=0 "$STRATA_IMAGE" >/dev/null \
    || { stop; die "docker run failed"; }
  echo "mutt: Strata variant $v starting (the desktop may freeze while ~43 GB is pinned; up to 25 min)"
  for _ in $(seq 1500); do
    curl -sf --max-time 3 127.0.0.1:18080/health >/dev/null && { up=1; break; }
    [[ $(docker inspect -f '{{.State.Running}}' strata 2>/dev/null) == true ]] \
      || { docker logs --tail 20 strata 2>&1 | tail -5; stop; die "Strata stopped while starting"; }
    sleep 1
  done
  ((up)) || { stop; die "Strata not healthy after 25 min"; }
  docker cp strata:/opt/strata/strata-iq3_xxs.log "$run/strata-engine-load.log" >/dev/null 2>&1 || true
  PROXY_AUTH_FILE=$STRATA_DATA/config/api-key PROXY_SET_PARAMS=$STRATA_PARAMS \
    nohup $PY "$D/proxy.py" 8080 http://127.0.0.1:18080 "$run/proxy" > "$run/proxy.log" 2>&1 &
  echo $! > "$STATE/proxy.pid"
  for _ in $(seq 20); do curl -sf 127.0.0.1:8080/health >/dev/null && break; sleep 0.5; done
  curl -sf 127.0.0.1:8080/health >/dev/null || { stop; die "proxy not answering"; }
  printf '%s\n' "$name" "${FILE[$name]}" "${SHA[$name]}" "$(cd "$run" && pwd)" > "$STATE/model"
  echo "strata $STRATA_IMAGE variant $v (locked down, one request at a time), proxy params $STRATA_PARAMS" > "$STATE/serving"
  echo "mutt: $name serving via proxy 127.0.0.1:8080 -> relay 127.0.0.1:18080 -> Strata (no internet)"
}

stop() {
  for f in proxy llama; do
    [[ -f $STATE/$f.pid ]] && kill "$(cat "$STATE/$f.pid")" 2>/dev/null || true
    rm -f "$STATE/$f.pid"
  done
  if [[ -f $STATE/strata ]]; then   # a Strata entry: the container, then its locked-down network
    local run; run=$(sed -n 4p "$STATE/model" 2>/dev/null)
    [[ -n $run ]] && docker cp strata:/opt/strata/strata-iq3_xxs.log "$run/strata-engine.log" >/dev/null 2>&1
    docker stop -t 90 strata >/dev/null 2>&1; docker rm strata >/dev/null 2>&1
    "$D/stratanet.sh" down >/dev/null 2>&1
    rm -f "$STATE/strata" "$STATE/serving"
  fi
  for _ in $(seq 30); do ss -ltnH '( sport = :8081 or sport = :8080 )' | grep -q . || break; sleep 1; done
}

case "${1:-}" in
start)
  name=${2:?model: stock|carnice|occamy}; run=${3:?run dir}
  [[ -n "${FILE[$name]:-}" ]] || die "unknown model $name"
  systemctl is-active --quiet kent-llama && die "kent-llama is running; run: kent llama stop"
  ss -ltnH '( sport = :8080 or sport = :8081 )' | grep -q . && die "port 8080 or 8081 in use"
  mkdir -p "$run" "$STATE"
  [[ -n ${STRATA_VARIANT[$name]:-} ]] && { strata_start "$name" "$run"; exit 0; }
  echo "mutt: checking SHA-256 of ${FILE[$name]}"
  sha=${SHA[$name]}
  [[ -n $sha ]] || sha=$($PY -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifacts"]["gguf"])' "$EDGE0_MANIFEST") \
    || die "no SHA-256 for ${FILE[$name]}"
  echo "$sha  $M/${FILE[$name]}" | sha256sum -c --quiet - || die "hash mismatch for ${FILE[$name]}"
  bindir=${BINDIR[$name]:-/opt/kent-llama/bin}
  libdir=$bindir; [[ $bindir == /opt/kent-llama/bin ]] && libdir=/opt/kent-llama/lib
  # the upstream builds have no --no-mmap: their equivalent is --load-mode none
  loadopt=--no-mmap; [[ $bindir == "$UPSTREAM" || $bindir == "$V06" ]] && loadopt="-lm none"
  export LD_LIBRARY_PATH=$libdir
  # shellcheck disable=SC2086
  nohup "$bindir/llama-server" -m "$M/${FILE[$name]}" --alias locally-run-model \
    -ngl 99 -ncmoe "${NCMOE[$name]}" -c "${CTXM[$name]:-262144}" -t 5 -ub 1024 --jinja $loadopt --parallel 2 -kvu \
    -fa on -ctk "${CTK[$name]:-q8_0}" -ctv "${CTV[$name]:-turbo3}" ${EXTRA[$name]:---spec-type none} -rea off --temp 0.7 --top-p 0.80 --top-k 20 \
    --min-p 0.00 --presence-penalty 0.1 --repeat-penalty 1.0 --host 127.0.0.1 --port 8081 -lv "${LV:-1}" \
    > "$run/llama.log" 2>&1 &
  echo $! > "$STATE/llama.pid"
  for _ in $(seq 600); do
    curl -sf 127.0.0.1:8081/health >/dev/null && break
    kill -0 "$(cat "$STATE/llama.pid")" 2>/dev/null || { tail -20 "$run/llama.log"; stop; die "llama-server died"; }
    sleep 1
  done
  curl -sf 127.0.0.1:8081/health >/dev/null || { stop; die "llama-server not healthy after 600 s"; }
  nohup $PY "$D/proxy.py" 8080 http://127.0.0.1:8081 "$run/proxy" > "$run/proxy.log" 2>&1 &
  echo $! > "$STATE/proxy.pid"
  for _ in $(seq 20); do curl -sf 127.0.0.1:8080/health >/dev/null && break; sleep 0.5; done
  curl -sf 127.0.0.1:8080/health >/dev/null || { stop; die "proxy not answering"; }
  printf '%s\n' "$name" "${FILE[$name]}" "${SHA[$name]}" "$(cd "$run" && pwd)" > "$STATE/model"
  echo "mutt: $name serving via proxy 127.0.0.1:8080 -> 127.0.0.1:8081"
  ;;
stop) stop; rm -f "$STATE/model"; echo "mutt: stopped" ;;
status)
  if [[ -f $STATE/model ]] && curl -sf 127.0.0.1:8080/health >/dev/null; then
    echo "mutt: $(head -1 "$STATE/model") up"; else echo "mutt: down"; exit 1; fi ;;
*) die "usage: mutt.sh start stock|carnice|occamy|stock-q6|stock-q8|nemotron|stock-k4|edge0-k4-lora|edge0-k4|stock-mtp|q6-mtp|kv-q8q8|kv-q8q4|kv-q51q4|kv-q6q8q4|kv-q6q8q8|strata-a RUN_DIR | stop | status" ;;
esac
