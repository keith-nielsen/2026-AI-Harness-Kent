#!/usr/bin/env bash
# followup.sh — queued behind kent-bench-campaign (pilot-v1). Question: is stock Q6 better than stock Q4?
#   1. wait until the kent-bench-campaign unit has ended
#   2. stock (UD-Q4_K_XL): all suites, 2 repeats, in pilot-v1b (pilot-v1's stock runs used the pre-fix bench)
#   3. stock-q6 (UD-Q6_K_XL): second repeat of every suite in its pilot-v1 folders (rep 0 already there)
#   4. occamy crews in pilot-v1b (its pilot-v1 crews hit the fixture outage), 1 repeat
# Both quantisations then have pass^2 and flips per item (report.py). Nemotron and Carnice: dropped.
# Bench mode stays on and kent-llama stays stopped afterwards (KEEP_BENCH=1, operator's choice).
# Start detached:  tests/bench/submit.sh followup followup.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"
LOG=$HOME/Documents/repo/bench/runs/pilot-v1b/followup.log
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
log() { echo "$(date '+%F %T') followup: $*"; }
export KEEP_BENCH=1

log "waiting for kent-bench-campaign to end"
while systemctl --user is-active --quiet kent-bench-campaign; do sleep 60; done
log "kent-bench-campaign ended"
"$B/campaign.sh" pilot-v1b stock "probes kent crews" pilot 2
"$B/campaign.sh" pilot-v1 stock-q6 "probes kent crews" pilot 2
"$B/campaign.sh" pilot-v1b occamy crews pilot 1
/usr/bin/python3 "$B/pick_best.py" >/dev/null   # first-pass ranking into the log
log "done"
