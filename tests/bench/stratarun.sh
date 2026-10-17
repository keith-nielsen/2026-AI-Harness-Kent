#!/usr/bin/env bash
# stratarun.sh smoke VARIANT... — start Strata (Qwen3.8-Flash-Next IQ3_XXS) in the hardened, locked-down container,
# once per config variant, in order (operator, 2026-10-07). Variants: strata-data/config/variants/<name>.json
# (stratacfg.sh): A = KV int8 + streaming 20480, B = KV k8v4 in VRAM; both --prefill 512 --no-prefill-borrow, no
# vision, English draft vocabulary. "setup" is refused (Strata #1188: k8v4 + streaming loops on this card).
#   Once: kent-llama stopped (Strata uses ~60 GB of the 62 GB RAM), browsers and Discord closed, hostprofile.sh check
#   (refuse on mismatch), stratanet.sh up (no route out; DNS + TCP attempts logged to RUN/sink.jsonl; host reaches it
#   only via 127.0.0.1:18080). The desktop can freeze 1-3 min while ~43 GB is pinned.
#   Per variant: the variant copied over the live config, start, /health (up to 20 min; load time logged), RAM/VRAM
#   after load, the engine's expert-cache lines, then requests saved whole (RUN/<variant>-<name>.json):
#     short   one sentence (which usage/timing fields Strata returns)
#     4k      a ~4K-token prompt of the frozen corpus, 100-word summary
#     loop    #1188's probe verbatim (numbers 1-60 in words, greedy, no reasoning): completion tokens, finish and the
#             longest run of consecutive repeated words (expected: 156 tokens, stop, 0)
#   then stop (the engine log kept). After all: stratanet.sh down, kent-llama started again if it was running.
# Start detached:  tests/bench/submit.sh stratasmoke2 stratarun.sh smoke A B
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"
BENCH=$HOME/Documents/repo/bench
RUN=$BENCH/runs/stratarun-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/stratarun.log" 2>&1
log() { echo "$(date '+%F %T') stratarun: $*"; }
MODE=${1:-}; shift || true
VARIANTS=("$@")
[[ $MODE == smoke && ${#VARIANTS[@]} -gt 0 ]] || { log "usage: stratarun.sh smoke VARIANT..."; exit 2; }
IMAGE=strata:82f46a8
DATA=/media/administrator/DATA/strata-data
RESOLV=$BENCH/strata-resolv.conf
URL=http://127.0.0.1:18080
KEY=$(cat "$DATA/config/api-key")
CORPUS=$BENCH/corpus/kent-repo-1a9b067.txt
for v in "${VARIANTS[@]}"; do
  [[ $v != setup ]] || { log "refused: variant 'setup' is k8v4 + KV streaming (Strata #1188)"; exit 2; }
  [[ -f $DATA/config/variants/$v.json ]] || { log "no variant $v (run stratacfg.sh)"; exit 2; }
done
VPID=

mem() { echo "RAM avail $(free -m | awk '/^Mem:/{print $7}') MiB; VRAM used/free $(nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader,nounits) MiB"; }
in_image() { docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges -v "$DATA":/data \
               --entrypoint sh "$IMAGE" -c "$1"; }
stop_strata() {
  docker logs strata > "$RUN/$1.container.log" 2>&1 || true
  docker cp strata:/opt/strata/strata-iq3_xxs.log "$RUN/$1.engine.log" >/dev/null 2>&1 || true
  docker stop -t 90 strata >/dev/null 2>&1; docker rm strata >/dev/null 2>&1
  [[ -n $VPID ]] && { kill "$VPID" 2>/dev/null; wait "$VPID" 2>/dev/null; VPID=; }
}

run_variant() {  # run_variant NAME -> 0 ok, 1 failed
  local v=$1 t0 up=0
  in_image "cp /data/config/variants/$v.json /data/config/strata-iq3_xxs.json && chmod 0600 /data/config/strata-iq3_xxs.json" \
    || { log "$v: cannot install the variant"; return 1; }
  log "$v: args $(in_image ".venv/bin/python -c \"import json; print(' '.join(x for x in json.load(open('/data/config/strata-iq3_xxs.json'))['args'] if not x.startswith('/')))\"")"
  nvidia-smi --query-gpu=timestamp,memory.used,memory.free --format=csv,noheader,nounits -lms 2000 -f "$RUN/$v.vram.csv" &
  VPID=$!
  t0=$(date +%s)
  docker run -d --name strata --network strata-net --ip 172.30.99.10 -v "$RESOLV":/etc/resolv.conf:ro \
    --gpus device=0 --cap-drop ALL --security-opt no-new-privileges --ulimit memlock=-1 --pids-limit 4096 \
    -v "$DATA":/data -e FAMILY=qwen -e MODEL=IQ3_XXS -e PORT=8080 -e GPU=0 "$IMAGE" >/dev/null \
    || { log "$v: docker run failed"; return 1; }
  log "$v: started; waiting for /health (the desktop may freeze while the model is pinned)"
  for _ in $(seq 1200); do
    curl -sf --max-time 3 "$URL/health" >/dev/null && { up=1; break; }
    [[ $(docker inspect -f '{{.State.Running}}' strata 2>/dev/null) == true ]] \
      || { log "$v: container stopped: $(docker logs --tail 4 strata 2>&1 | tr '\n' ' ' | cut -c1-700)"; stop_strata "$v"; return 1; }
    sleep 1
  done
  ((up)) || { log "$v: not healthy after 20 min"; stop_strata "$v"; return 1; }
  log "$v: healthy after $(( $(date +%s) - t0 )) s; $(mem)"
  docker cp strata:/opt/strata/strata-iq3_xxs.log "$RUN/$v.engine-load.log" >/dev/null 2>&1 \
    && log "$v: engine: $(grep -iE 'expert cache|kv streaming|prefill|free with everything' "$RUN/$v.engine-load.log" | tail -6 | tr '\n' ' ' | cut -c1-900)"
  python3 - "$URL" "$KEY" "$CORPUS" "$RUN" "$v" <<'EOF' >> "$RUN/stratarun.log" 2>&1
import json, re, sys, time, urllib.request
url, key, corpus, run, v = sys.argv[1:6]
def ask(name, content, max_tokens, **extra):
    body = {"model": "strata", "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens, "temperature": 0, **extra}
    req = urllib.request.Request(url + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            out = json.load(r)
    except Exception as e:
        print(f"{v} {name}: FAILED {e!r}", flush=True); return None
    dt = time.time() - t
    json.dump(out, open(f"{run}/{v}-{name}.json", "w"), indent=1)
    msg = out["choices"][0]["message"]
    print(f"{v} {name}: {dt:.1f} s wall, finish {out['choices'][0].get('finish_reason')}, usage {out.get('usage')}, "
          f"extra keys {sorted(set(out) - {'id', 'object', 'created', 'model', 'choices', 'usage'})}, "
          f"answer {(msg.get('content') or '')[:160]!r}", flush=True)
    return out
ask("short", "In one sentence: what is a thermal data logger?", 128)
text = open(corpus, encoding="utf-8").read()[:16000]          # ~4K tokens of the frozen corpus
ask("4k", text + "\n\nIn about 100 words, summarize what the text above covers.", 300)
out = ask("loop", "Write the numbers from 1 to 60 in words, separated by commas, with no other text.", 400,
          reasoning_effort="none")
if out:
    words = re.findall(r"[a-z]+(?:-[a-z]+)?", (out["choices"][0]["message"].get("content") or "").lower())
    rep = sum(1 for a, b in zip(words, words[1:]) if a == b)
    print(f"{v} loop (#1188): completion tokens {(out.get('usage') or {}).get('completion_tokens')}, "
          f"finish {out['choices'][0].get('finish_reason')}, consecutive repeated words {rep} "
          f"-> {'CLEAN' if rep == 0 else 'DEGENERATE'}", flush=True)
EOF
  log "$v: requests done; $(mem); peak VRAM used $(cut -d, -f2 "$RUN/$v.vram.csv" | sort -n | tail -1 | tr -d ' ') MiB"
  stop_strata "$v"
  log "$v: stopped"
  return 0
}

LLAMA_WAS_ACTIVE=0
systemctl is-active --quiet kent-llama && LLAMA_WAS_ACTIVE=1
finish() {
  docker inspect strata >/dev/null 2>&1 && stop_strata last
  "$B/stratanet.sh" down >/dev/null 2>&1
  log "sink: $(grep -vc '"kind": "start"' "$RUN/sink.jsonl" 2>/dev/null || echo 0) outbound attempts logged ($RUN/sink.jsonl)"
  "$B/hostprofile.sh" check > "$RUN/hostprofile-end.txt" 2>&1 && log "host profile at end: OK" \
    || log "WARNING: host profile changed: $(grep MISMATCH "$RUN/hostprofile-end.txt" | tr '\n' ' ')"
  if ((LLAMA_WAS_ACTIVE)); then kent llama start >/dev/null 2>&1 && log "kent-llama started again" \
    || log "WARNING: kent llama start failed"; fi
  log "after: $(mem)"
  log "done: $RUN"
}

docker image inspect "$IMAGE" >/dev/null 2>&1 || { log "no image $IMAGE"; exit 1; }
docker inspect strata >/dev/null 2>&1 && { log "a container named strata exists already"; exit 1; }
trap finish EXIT
log "start: variants [${VARIANTS[*]}]"
for app in brave chrome chromium chromium-browse Discord; do
  pgrep -x "$app" >/dev/null && { pkill -x "$app"; log "closed $app (RAM and GPU memory)"; }
done
sleep 2
if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; fi
"$B/hostprofile.sh" check > "$RUN/hostprofile-start.txt" 2>&1 \
  || { log "refused: host profile mismatch: $(grep MISMATCH "$RUN/hostprofile-start.txt" | tr '\n' ' ')"; exit 1; }
log "before: $(mem); other GPU users: $(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader | tr '\n' ';')"
"$B/stratanet.sh" up "$RUN" > "$RUN/stratanet.txt" 2>&1 || { log "stratanet up failed: $(tr '\n' ' ' < "$RUN/stratanet.txt")"; exit 1; }
for v in "${VARIANTS[@]}"; do
  run_variant "$v" || log "$v: FAILED (see $RUN/$v.container.log)"
done
