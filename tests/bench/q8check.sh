#!/usr/bin/env bash
# q8check.sh — is stock UD-Q8_K_XL worth chasing over UD-Q6_K_XL? One pass of the pruned v1 tier.
#   1. wait for the kent-q8-download unit, check the SHA-256, move the file into the models folder (0444)
#   2. stock-q8: probes, kent, crews at --tier v1, 1 repeat, in runs/q8-check
#   3. stock-q6 and stock: re-ask only h02,h03 (their prompts gained the CSV header after the pilot)
# Bench mode stays on and kent-llama stays stopped afterwards (KEEP_BENCH=1), as after pilot-v1b.
# Start detached:  tests/bench/submit.sh q8 q8check.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"
LOG=$HOME/Documents/repo/bench/runs/q8-check/q8check.log
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
log() { echo "$(date '+%F %T') q8check: $*"; }
SRC=/media/administrator/DATA/models/.dl/Qwen3.6-35B-A3B-UD-Q8_K_XL.gguf
DST=/media/administrator/DATA/models/Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL.gguf
SHA=6c6b816537abad90b250a0972b345466028d861ddfe316d5f0de31ca6440f781
export KEEP_BENCH=1

if [[ ! -f $DST ]]; then
  log "waiting for kent-q8-download"
  while systemctl --user is-active --quiet kent-q8-download; do sleep 60; done
  [[ -f $SRC ]] || { log "download missing: $SRC"; exit 1; }
  log "checking SHA-256"
  echo "$SHA  $SRC" | sha256sum -c --quiet - || { log "hash mismatch"; exit 1; }
  mv "$SRC" "$DST" && chmod 0444 "$DST" || { log "move failed"; exit 1; }
  log "model in place: $DST"
fi
"$B/campaign.sh" q8-check stock-q8 "probes kent crews" v1 1
ONLY=h02,h03 "$B/campaign.sh" q8-check "stock-q6 stock" probes v1 1
log "done"
