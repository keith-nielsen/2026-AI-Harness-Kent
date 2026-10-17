#!/usr/bin/env bash
# mcqkv-night.sh — Q4 K/V-cache quality permutations (operator, 2026-10-13), like for like with the f16 runs
# mcqcheck-10131216 (-np 4: mmlu-val, winogrande, hellaswag:1500) and mcqcheck-10131605 (-np 5: ARC).
# Priority order, one model per job pair, so a deadline cut loses the least important arm: q4kv84 (K q8_0/V q4_0,
# the asked-for run), q4kv88 (q8_0/q8_0), q4kv44 (q4_0/q4_0, lower bracket). Each job waits for the one before it.
set -euo pipefail
B="$(cd "$(dirname "$0")" && pwd)"
DL=${DEADLINE:-"2026-10-14 08:45"}
for m in q4kv84 q4kv88 q4kv44; do
  KEEP_APPS=1 NP=4 DEADLINE=$DL MEMMAX=46G "$B/submit.sh" "mcq-$m-np4" mcqcheck.sh "$m" "mmlu-val winogrande hellaswag:1500"
  KEEP_APPS=1 NP=5 DEADLINE=$DL MEMMAX=46G "$B/submit.sh" "mcq-$m-arc" mcqcheck.sh "$m" "arc-c arc-e"
done
