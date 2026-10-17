#!/usr/bin/env bash
# campaign.sh NAME "MODELS" "SUITES" TIER REPS — run bench suites on several models, unattended.
#   e.g. campaign.sh pilot-v1 "carnice occamy" "probes kent crews" pilot 1
# Start it detached (outlives this terminal) with submit.sh, never by hand:
#   tests/bench/submit.sh pilot campaign.sh pilot-v1 "..." "..." pilot 1
# Needs no password: kent-bench via the 93-kent-bench sudoers rule, kent llama via polkit.
# Always leaves the host as it found it: on exit (normal, error or stop) it stops the model server,
# purges bench Gents, turns bench mode off if it turned it on, and restarts kent-llama if it was running.
# FINAL_OFF=1 / FINAL_LLAMA=1 force "bench off" and "kent llama start" at the end even when the campaign
# found them already on/off (e.g. started from an interactive bench session).
# KEEP_BENCH=1 leaves bench mode on and kent-llama stopped at the end (more runs queued behind this one);
# the model server is still stopped and bench Gents purged.
# ONLY=id,id limits every suite to those item ids (run.py --only), e.g. re-asking items whose prompt changed.
# GRANT=rw|ro|none grants every Kent/safety item's share that way (run.py --grant); default: the item's own.
# THINKS="off 2048 8192" runs the probes suite once per thinking mode (run.py --think), interleaved by repeat
# (rep 0 in every mode, then rep 1, ...) so a run cut short still has paired results; one RUN_DIR per mode:
# probes-MODEL-think<mode>. Other suites ignore THINKS.
# DEADLINE=HH:MM (a time already past = tomorrow): no new (repeat, mode) block starts after it; the campaign
# then cleans up as usual. bench/runs/NO-DEADLINE present = ignore it.
# Every suite resumes where it stopped (run.py skips finished items), so rerunning the same command
# after an interruption continues the campaign. Progress: RUNS/NAME/status.json and campaign.log.
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
B="$(cd "$(dirname "$0")" && pwd)"
NAME=$1 MODELS=$2 SUITES=$3 TIER=$4 REPS=$5
RUNS=$HOME/Documents/repo/bench/runs/$NAME
KB=/opt/kent-bench/bin/kent-bench
mkdir -p "$RUNS"
exec >>"$RUNS/campaign.log" 2>&1
log() { echo "$(date '+%F %T') campaign: $*"; }
status() {   # phase, model, suite
  printf '{"campaign": "%s", "phase": "%s", "model": "%s", "suite": "%s", "updated": "%s", "pid": %d}\n' \
    "$NAME" "$1" "${2:-}" "${3:-}" "$(date -Is)" $$ > "$RUNS/status.json"
}

WE_ON=${FINAL_OFF:-0}
LLAMA_WAS_ACTIVE=0
systemctl is-active --quiet kent-llama && LLAMA_WAS_ACTIVE=1
RESTART_LLAMA=$(( LLAMA_WAS_ACTIVE || ${FINAL_LLAMA:-0} ))

purge_bench_gents() {
  kent gents 2>/dev/null | awk '$2=="active" && $NF ~ /^bench-/ {print $1}' | while read -r g; do
    log "purging leftover bench Gent $g"; kent gent destroy "$g" --purge >/dev/null 2>&1 || true
  done
}

cleanup() {
  local rc=$?
  status cleanup
  "$B/mutt.sh" stop >/dev/null 2>&1
  purge_bench_gents
  # grader sandboxes (checks.sandbox_run) that outlived their run
  docker ps -q --filter name=kent-bench-sandbox- | xargs -r docker kill >/dev/null 2>&1
  # a stopped run.py can leave its `kent -q` still running as kent; bench mode cannot switch until it ends
  for _ in $(seq 300); do pgrep -u kent -f hermes >/dev/null || break; sleep 1; done
  if ((${KEEP_BENCH:-0})); then log "KEEP_BENCH=1: leaving bench mode on and kent-llama stopped"
  elif ((WE_ON)); then sudo -n "$KB" off || log "WARNING: kent-bench off failed; run it by hand"; fi
  if ((RESTART_LLAMA)) && ! ((${KEEP_BENCH:-0})); then kent llama start >/dev/null 2>&1 || log "WARNING: kent llama start failed"; fi
  status "done (exit $rc)"
  log "finished, exit $rc"
}
trap cleanup EXIT
trap 'log "stopped by signal"; exit 143' TERM INT

END=0
if [[ -n ${DEADLINE:-} ]]; then
  END=$(date -d "$DEADLINE" +%s) || { log "bad DEADLINE $DEADLINE"; exit 2; }
  (( END <= $(date +%s) )) && [[ $DEADLINE =~ ^[0-9]{1,2}:[0-9]{2}$ ]] && END=$(date -d "tomorrow $DEADLINE" +%s)
  [[ -e $HOME/Documents/repo/bench/runs/NO-DEADLINE ]] && END=0
fi
past_deadline() { (( END > 0 && $(date +%s) >= END )); }
log "start: models=[$MODELS] suites=[$SUITES] tier=$TIER reps=$REPS thinks=[${THINKS:-}] deadline=${DEADLINE:-none}"
status starting
"$B/mutt.sh" stop >/dev/null 2>&1        # a model server left from earlier work
if ((LLAMA_WAS_ACTIVE)); then kent llama stop >/dev/null 2>&1 || { log "cannot stop kent-llama"; exit 1; }; fi
if ! sudo -n "$KB" status | grep -q '^bench on'; then
  sudo -n "$KB" on || { log "kent-bench on failed"; WE_ON=0; exit 1; }
  WE_ON=1
fi

for m in $MODELS; do
  status "serving" "$m"
  started=0
  for attempt in 1 2; do
    if "$B/mutt.sh" start "$m" "$RUNS/mutt-$m-$(date +%m%d%H%M)"; then started=1; break; fi
    log "model server for $m failed to start (attempt $attempt)"; "$B/mutt.sh" stop >/dev/null 2>&1; sleep 30
  done
  ((started)) || { log "skipping $m: server would not start"; continue; }
  for s in $SUITES; do
    # blocks: "reps think" pairs; without THINKS one block runs every repeat as before
    blocks=("$REPS off")
    if [[ $s == probes && -n ${THINKS:-} ]]; then
      blocks=(); for ((r = 1; r <= REPS; r++)); do for t in $THINKS; do blocks+=("$r $t"); done; done
    fi
    for blk in "${blocks[@]}"; do
      read -r reps think <<<"$blk"
      out="$RUNS/$s-$m"; [[ $s == probes && -n ${THINKS:-} ]] && out="$RUNS/$s-$m-think$think"
      if past_deadline; then log "DEADLINE $DEADLINE reached: not starting $m / $s reps<=$reps think=$think"; break 3; fi
      for attempt in 1 2 3; do
        status "running" "$m" "$s"
        log "$m / $s reps<=$reps think=$think (attempt $attempt)"
        /usr/bin/python3 "$B/run.py" "$s" --model "$m" --tier "$TIER" --reps "$reps" --out "$out" --think "$think" ${ONLY:+--only "$ONLY"} ${GRANT:+--grant "$GRANT"} && break
        log "$m / $s exited with an error; retrying after cleanup"
        purge_bench_gents
        if ! "$B/mutt.sh" status >/dev/null 2>&1; then
          "$B/mutt.sh" stop >/dev/null 2>&1; "$B/mutt.sh" start "$m" "$RUNS/mutt-$m-$(date +%m%d%H%M)" || break
        fi
        sleep 30
      done
    done
  done
  "$B/mutt.sh" stop >/dev/null 2>&1
done
log "all models done"
