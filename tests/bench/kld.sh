#!/usr/bin/env bash
# kld.sh — how far do the stock Qwen3.6-35B-A3B quants drift from the best one? KL divergence of
# UD-Q6_K_XL and UD-Q4_K_XL against UD-Q8_K_XL (the reference: BF16, ~70 GB, does not fit in 62 GB RAM),
# with llama-perplexity from the operator's build (same llama.cpp build as kent-llama, b9971 c26cbdffc).
# Two corpora, built once into runs/kld/corpora (hash-checked or recorded):
#   wiki  — wikitext-2 test, the standard corpus (first 100 chunks of 512 tokens)
#   agent — Kent and crew traffic from the pilot (kld_corpus.py over stock-q6's pilot-v1 proxy log)
# Same offload as the bench (-ngl 99 -ncmoe 40, -fa on, -t 5, --no-mmap), f16 KV, 512-token chunks.
# Needs the GPU and RAM to itself: kent-llama stopped and no bench model server (submit.sh queues it).
# Results: runs/kld/<corpus>-<model>.log and runs/kld/summary.txt. The reference logits (~10 GB per
# corpus) are deleted at the end unless KEEP_LOGITS=1.
# Start detached:  tests/bench/submit.sh kld kld.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"
OUT=$HOME/Documents/repo/bench/runs/kld
mkdir -p "$OUT/corpora"
exec >>"$OUT/kld.log" 2>&1
log() { echo "$(date '+%F %T') kld: $*"; }
PPL=$HOME/Downloads/repo/llama-cpp-turboquant/build-cuda13/bin/llama-perplexity
M=/srv/kent/models
declare -A FILE=(   # same files and hashes as mutt.sh
  [q8]=Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL.gguf
  [q6]=Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf
  [q4]=Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf
)
declare -A SHA=(
  [q8]=6c6b816537abad90b250a0972b345466028d861ddfe316d5f0de31ca6440f781
  [q6]=35fce994cd36104a7dc1bd8a4bdf13778145664c00fdef6773aebc9246e5019c
  [q4]=55983c5a75a1ab969824077b3bb3de4146e82a9234072b48ad4e8f92ad3fe9f1
)
WIKI_SHA=173c87a53759e0201f33e0ccf978e510c2042d7f2cb78229d9a50d79b9e7dd08
AGENT_PROXY=$HOME/Documents/repo/bench/runs/pilot-v1/mutt-stock-q6-10020036/proxy
ARGS=(-c 512 -ngl 99 -ncmoe 40 -fa on -t 5 --no-mmap)
declare -A CHUNKS=([wiki]=100 [agent]=-1)

log "start"
systemctl is-active --quiet kent-llama && { log "kent-llama is running; refusing"; exit 1; }
ss -ltnH '( sport = :8080 or sport = :8081 )' | grep -q . && { log "a bench model server is up; refusing"; exit 1; }
[[ -x $PPL ]] || { log "missing $PPL"; exit 1; }

C=$OUT/corpora
if [[ ! -f $C/wiki.txt ]]; then
  curl -sfL -o "$C/wt.zip" https://huggingface.co/datasets/ggml-org/ci/resolve/main/wikitext-2-raw-v1.zip \
    && unzip -o -q -j "$C/wt.zip" wikitext-2-raw/wiki.test.raw -d "$C" && mv "$C/wiki.test.raw" "$C/wiki.txt" \
    && rm -f "$C/wt.zip" || { log "wikitext download failed"; exit 1; }
fi
echo "$WIKI_SHA  $C/wiki.txt" | sha256sum -c --quiet - || { log "wikitext hash mismatch"; exit 1; }
[[ -f $C/agent.txt ]] || /usr/bin/python3 "$B/kld_corpus.py" "$AGENT_PROXY" "$C/agent.txt" || exit 1
sha256sum "$C"/*.txt

for q in q8 q6 q4; do
  log "checking SHA-256 of ${FILE[$q]}"
  echo "${SHA[$q]}  $M/${FILE[$q]}" | sha256sum -c --quiet - || { log "hash mismatch: ${FILE[$q]}"; exit 1; }
done

for corpus in wiki agent; do
  base=$OUT/$corpus-q8.logits
  log "$corpus: reference q8"
  "$PPL" -m "$M/${FILE[q8]}" -f "$C/$corpus.txt" --chunks "${CHUNKS[$corpus]}" "${ARGS[@]}" \
    --kl-divergence-base "$base" > "$OUT/$corpus-q8.log" 2>&1 || { log "$corpus q8 failed"; tail -5 "$OUT/$corpus-q8.log"; exit 1; }
  for q in q6 q4; do
    log "$corpus: $q vs q8"
    "$PPL" -m "$M/${FILE[$q]}" --kl-divergence-base "$base" --kl-divergence "${ARGS[@]}" \
      > "$OUT/$corpus-$q.log" 2>&1 || { log "$corpus $q failed"; tail -5 "$OUT/$corpus-$q.log"; exit 1; }
  done
  ((${KEEP_LOGITS:-0})) || rm -f "$base"
done

{
  echo "KL divergence vs UD-Q8_K_XL ($(date '+%F %T'))"
  for corpus in wiki agent; do
    echo; echo "== $corpus: q8 reference: $(grep -E 'Final estimate' "$OUT/$corpus-q8.log" | tail -1 | sed 's/.*Final estimate: //')"
    for q in q6 q4; do
      echo "-- $q"
      sed -n '/====== Perplexity statistics ======/,$p' "$OUT/$corpus-$q.log" \
        | grep -E 'Mean PPL\(Q\)|Mean PPL\(base\)|PPL\(Q\)/PPL\(base\)|Mean +KLD|Maximum KLD|99\.9% +KLD|99\.0% +KLD|Median +KLD|Same top p|RMS Δp'
    done
  done
} > "$OUT/summary.txt"
log "done"; cat "$OUT/summary.txt"
