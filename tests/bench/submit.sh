#!/usr/bin/env bash
# submit.sh NAME JOB [ARGS...] — the only way to start a detached bench job.
#   JOB is a script in tests/bench (campaign.sh, followup.sh, q8check.sh, ...), never an inline command.
#   It runs as the user unit kent-bench-NAME, queued behind any kent-bench-* unit already active
#   (one bench job at a time: they share the model server, bench mode and Kent's state).
#   Environment for the job: pass it as VAR=value before submit.sh; only KEEP_BENCH, FINAL_OFF,
#   FINAL_LLAMA, ONLY, GRANT, KV_CTX, DEADLINE, NP, KEEP_APPS and THINKS are forwarded.
#   MEMMAX=40G caps the job's memory (cgroup MemoryMax, OOMPolicy=continue): an overshoot kills the offending
#   process inside the job, never the rest of the host.
#   Every submission is recorded in runs/jobs.log. Watch everything with watch.sh.
#   e.g.  KEEP_BENCH=1 tests/bench/submit.sh q6ctl campaign.sh q8-ctl stock-q6 "probes kent crews" v1 1
set -euo pipefail
B="$(cd "$(dirname "$0")" && pwd)"
RUNS=$HOME/Documents/repo/bench/runs
name=${1:?usage: submit.sh NAME JOB [ARGS...]}; job=${2:?usage: submit.sh NAME JOB [ARGS...]}; shift 2
[[ $name =~ ^[a-z0-9-]+$ ]] || { echo "submit: NAME must be [a-z0-9-]" >&2; exit 2; }
[[ $job != */* && -x $B/$job ]] || { echo "submit: $job is not an executable script in $B" >&2; exit 2; }
unit=kent-bench-$name
systemctl --user is-active --quiet "$unit" && { echo "submit: $unit is already running" >&2; exit 1; }
# a queued job waits on unit names: reusing a name a waiting job still waits for deadlocks the queue (2026-10-14)
for u in $(systemctl --user list-units --type=service --state=active --plain --no-legend 'kent-bench-*' | awk '{print $1}'); do
  systemctl --user show -p ExecStart --value "$u" | grep -qw -- "$unit" \
    && { echo "submit: $u is waiting for $unit; use another NAME" >&2; exit 1; }
done
ahead=$(systemctl --user list-units --type=service --state=active --plain --no-legend 'kent-bench-*' \
        | awk '{print $1}' | sed 's/\.service$//' | tr '\n' ' ')
envs=()
for v in KEEP_BENCH FINAL_OFF FINAL_LLAMA ONLY GRANT KV_CTX DEADLINE NP KEEP_APPS THINKS; do [[ -n ${!v:-} ]] && envs+=(-E "$v=${!v}"); done
props=()
[[ -n ${MEMMAX:-} ]] && props+=(-p "MemoryMax=$MEMMAX" -p OOMPolicy=continue)
mkdir -p "$RUNS"
# the unit waits for every job that was active at submission, then runs the job script
systemd-run --user --unit "$unit" --collect -p WorkingDirectory="$B" "${props[@]}" "${envs[@]}" \
  /usr/bin/bash -c 'for u in $0; do while systemctl --user is-active --quiet "$u"; do sleep 60; done; done; exec "$@"' \
  "$ahead" "$B/$job" "$@"
printf '%s submit: %s  %s %s  env[%s]  after[%s]\n' "$(date '+%F %T')" "$unit" "$job" "$*" \
  "${envs[*]:-} ${props[*]:-}" "$ahead" >> "$RUNS/jobs.log"
echo "submit: $unit started${ahead:+ (queued after: $ahead)}"
