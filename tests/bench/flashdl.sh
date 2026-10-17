#!/usr/bin/env bash
# flashdl.sh — download Qwen3.8-Flash-Next GSQ-RCO IQ3_XXS (ISTA-DASLab, Apache-2.0) for the Strata replication
# (operator, 2026-10-07: replicate the Codacus video setup). No GPU, no model server, no host changes.
#   Revision: the one Strata 82f46a8's setup.py pins (HF_REVISIONS, 2026-09-29), so Strata finds exactly the files
#   it expects. Layout: <data>/models/IQ3_XXS/<shard> (Strata's data-folder layout; mounted into its container later).
#   Resumable: run it again after an interruption. Each shard is checked against its Hugging Face LFS SHA-256,
#   then made read-only (0444, like every model file).
# Start detached:  tests/bench/submit.sh flashdl flashdl.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
BENCH=$HOME/Documents/repo/bench
RUN=$BENCH/runs/flashdl-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/flashdl.log" 2>&1
log() { echo "$(date '+%F %T') flashdl: $*"; }
HF=$HOME/ai-env/bin/hf
REPO=ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF
REV=ed59f92082b1e93c0e96d60a8b11aab089b52f09
DATA=/media/administrator/DATA/strata-data
declare -A SHA=(
  [IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf]=219ea929900dfa9ef091f3aa473fdba6874b65fcb36526d7d851ac9e95856d15
  [IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00002-of-00002.gguf]=316b46f3a2dbd68c900f43136ab9449f9dcc3725dfd8c794847c204bc161e113
)
mkdir -p "$DATA/models"
need=$(( 76 * 1024 * 1024 ))   # KiB, ~76 GB
free=$(df -k --output=avail "$DATA" | tail -1)
((free > need)) || { log "refused: $((free / 1048576)) GiB free on DATA, need ~71 GiB"; exit 1; }
log "start: $REPO @ $REV -> $DATA/models ($((free / 1048576)) GiB free)"
rc=0
for f in "${!SHA[@]}"; do
  dest=$DATA/models/$f
  if [[ -f $dest && ! -w $dest ]]; then log "$f: already verified (read-only), skipped"; continue; fi
  log "$f: downloading"
  "$HF" download "$REPO" "$f" --revision "$REV" --local-dir "$DATA/models" >/dev/null \
    || { log "$f: download FAILED (run again to resume)"; rc=1; continue; }
  log "$f: checking SHA-256"
  if echo "${SHA[$f]}  $dest" | sha256sum -c --quiet -; then
    chmod 0444 "$dest"; log "$f: OK ($(du -h "$dest" | cut -f1)), read-only"
  else
    log "$f: SHA-256 MISMATCH"; rc=1
  fi
done
log "done (rc $rc): $(df -h --output=avail "$DATA" | tail -1 | tr -d ' ') free on DATA"
exit $rc
