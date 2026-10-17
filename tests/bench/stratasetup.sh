#!/usr/bin/env bash
# stratasetup.sh [KV] [VISION] [DRAFT_VOCAB] [open|locked] — Strata's one-time setup pass for the Codacus replication, in the hardened container; never
# starts the model (kent-llama can stay up). Needs: image strata:82f46a8 (stratabuild.sh), the IQ3_XXS shards in
# DATA/strata-data/models/IQ3_XXS (flashdl.sh), uid 64010 write ACLs on strata-data's folders.
#   Network: this is the only pass with internet access (Hugging Face: a size check of the shards and the ~5 GB MTP
#   tensors from Qwen/Qwen3.8-Flash-Next at Strata's pinned revision, sha256-checked by mtp_fetch.py). No port.
#   Settings = the author's strata-1gpu entry: IQ3_XXS, 128k context, KV int8, vision on the GPU, one card; LOW_RAM off
#   (setup would otherwise judge from the RAM free now, with kent-llama loaded). setup.py itself writes
#   --spec 4 --spec-min-p 0.5 --mtp --expert-cache auto --prefill auto.
#   The config lands in strata-data/config/strata-iq3_xxs.json (what docker-entrypoint.sh copies there too).
#   API key: generated once, kept in strata-data/config/api-key (0600); the server only listens on loopback anyway.
#   Defaults int8 yes (cjk): the author's settings. 2026-10-07 (operator, after the 8 GB VRAM failure): k8v4 no en.
#   NET locked: the pass runs on stratanet.sh's network (no internet, attempts logged to RUN/sink.jsonl); a rerun
#   should need nothing from outside. Default open (the first pass fetched the MTP layer).
#   A rerun reuses the downloaded shards and MTP layer (no network needed for them) and rewrites the config.
# Start detached:  tests/bench/submit.sh stratasetup stratasetup.sh k8v4 no en
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
BENCH=$HOME/Documents/repo/bench
RUN=$BENCH/runs/stratasetup-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/stratasetup.log" 2>&1
log() { echo "$(date '+%F %T') stratasetup: $*"; }
IMAGE=strata:82f46a8
DATA=/media/administrator/DATA/strata-data
KEYF=$DATA/config/api-key
KV=${1:-int8} VISION=${2:-yes} DVOCAB=${3:-} NETMODE=${4:-open}
B="$(cd "$(dirname "$0")" && pwd)"
NETARGS=()
if [[ $NETMODE == locked ]]; then
  "$B/stratanet.sh" up "$RUN" > "$RUN/stratanet.txt" 2>&1 || { log "stratanet up failed"; exit 1; }
  trap '"$B/stratanet.sh" down >/dev/null 2>&1' EXIT
  NETARGS=(--network strata-net --ip 172.30.99.10 -v "$BENCH/strata-resolv.conf":/etc/resolv.conf:ro)
fi
[[ $KV =~ ^(int8|q4_0|k8v4)$ && $VISION =~ ^(yes|no|cpu|gpu)$ && $DVOCAB =~ ^(|en|cjk|cyrillic|fr)$ ]] \
  || { log "usage: stratasetup.sh [int8|q4_0|k8v4] [yes|no|cpu|gpu] [en|cjk|cyrillic|fr]"; exit 2; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || { log "no image $IMAGE (run stratabuild.sh)"; exit 1; }
for s in 00001 00002; do
  [[ -f $DATA/models/IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-$s-of-00002.gguf ]] || { log "shard $s missing"; exit 1; }
done
# the folder's default ACL would override a umask: strip it, owner-only, then read access for the container user
[[ -s $KEYF ]] || { head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$KEYF"; setfacl -b "$KEYF"; chmod 0600 "$KEYF"
                    setfacl -m u:64010:r "$KEYF"; }
log "start: kv $KV, vision $VISION, draft vocab ${DVOCAB:-default}; $IMAGE, data $DATA, free $(df -h --output=avail "$DATA" | tail -1 | tr -d ' ')"
docker run --rm --name strata-setup "${NETARGS[@]}" --gpus device=0 \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 4096 \
  -v "$DATA":/data -e STRATA_EXECV=0 -e KV="$KV" -e VISION="$VISION" -e DVOCAB="$DVOCAB" \
  --entrypoint sh "$IMAGE" -c '
    set -e
    .venv/bin/python setup.py --setup --yes --family qwen --model IQ3_XXS --context 131072 --vision "$VISION" \
      --kv "$KV" ${DVOCAB:+--draft-vocab "$DVOCAB"} --gpu 0 --low-ram off --data-dir /data --host 0.0.0.0 --port 8080 \
      --api-key "$(cat /data/config/api-key)" --no-start
    cp -f /opt/strata/strata-iq3_xxs.json /data/config/strata-iq3_xxs.json
    chmod 0600 /data/config/strata-iq3_xxs.json   # it holds the API key
    echo "config args:"; .venv/bin/python -c "import json; print(\" \".join(json.load(open(\"/data/config/strata-iq3_xxs.json\"))[\"args\"]))"
  ' > "$RUN/setup.out" 2>&1
rc=$?
log "setup exit $rc: $(grep -iE 'error|fail|warn' "$RUN/setup.out" | head -5 | tr '\n' ' ')"
log "config: $(tail -1 "$RUN/setup.out" | cut -c1-400)"
[[ $NETMODE == locked ]] && log "sink: $(grep -vc '"kind": "start"' "$RUN/sink.jsonl") outbound attempts logged"
log "done (rc $rc): $(du -sh "$DATA" | cut -f1) in strata-data, $(df -h --output=avail "$DATA" | tail -1 | tr -d ' ') free"
exit $rc
