#!/usr/bin/env bash
# hostprofile.sh show|check|apply|defaults — the host performance settings, set ONLY by hand (2026-10-05).
#
# No Kent service and no bench job changes these. kent-llama start/stop leaves them alone (the old
# kent-llama-tuning.service is retired); bench jobs only run `check` and refuse to measure on a mismatch.
# The operator sets a known state with `apply` (bench profile) or `defaults` (boot defaults), with sudo.
#
#   show       print every setting and the measured core clock               (no root)
#   check      compare with the bench profile below; exit 1 on any mismatch  (no root)
#   apply      set the bench profile                                         (sudo)
#   defaults   set the boot defaults: SMT on, boost on, schedutil, swap on, GPU clocks unlocked  (sudo)
#
# Settings survive until changed or until a reboot (a reboot returns every one of them to the boot defaults).
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

# --- bench profile (the state benchmarks expect) ---
SMT=off
BOOST=0
GOVERNOR=performance
SWAP=off
GPU_PERSISTENCE=Enabled
GPU_LOCK_MHZ=1650            # graphics clock locked (nvidia-smi -lgc); idle clock then reads this value
CLOCK_MIN_MHZ=3450           # measured core clock with boost off: 3.6 GHz base (3,578 measured 2026-10-05)
CLOCK_MAX_MHZ=3700           # above this the core is boosting

CPU=/sys/devices/system/cpu
HERE="$(cd "$(dirname "$0")" && pwd)"
CC_SRC="$HERE/../perf/clockcheck.c"; CC_BIN="${XDG_RUNTIME_DIR:-/tmp}/kent-clockcheck"

cpus() { local c; for d in "$CPU"/cpu[0-9]*; do [[ "$(cat "$d/online" 2>/dev/null || echo 1)" == 1 ]] && echo "${d##*cpu}"; done | sort -n; }
governors() { local c g=(); for c in $(cpus); do g+=("$(cat "$CPU/cpu$c/cpufreq/scaling_governor" 2>/dev/null)"); done; printf '%s\n' "${g[@]}" | sort -u | paste -sd,; }
swap_state() { [[ -n "$(swapon --noheadings --show 2>/dev/null)" ]] && echo on || echo off; }
gpu() { nvidia-smi --query-gpu="$1" --format=csv,noheader,nounits 2>/dev/null | head -1; }
clock_mhz() {  # measured on the last online core, which the model server's threads use least
    [[ -x "$CC_BIN" && "$CC_BIN" -nt "$CC_SRC" ]] || gcc -O1 -o "$CC_BIN" "$CC_SRC" 2>/dev/null || { echo "?"; return; }
    taskset -c "$(cpus | tail -1)" "$CC_BIN" | awk '{ print $1 }'
}
cstates() { local s out=(); for s in "$CPU"/cpu0/cpuidle/state*; do out+=("$(cat "$s/name")$([[ "$(cat "$s/disable")" == 1 ]] && echo '(off)')"); done; echo "${out[*]}"; }

state() {  # key=value lines, the snapshot benches record
    echo "smt=$(cat "$CPU/smt/control")"
    echo "boost=$(cat "$CPU/cpufreq/boost")"
    echo "governor=$(governors)"
    echo "swap=$(swap_state)"
    echo "gpu_persistence=$(gpu persistence_mode)"
    echo "gpu_idle_clock_mhz=$(gpu clocks.gr)"
    echo "core_clock_mhz=$(clock_mhz)"
    echo "cpu_temp_c=$(awk '{ printf "%.0f", $1 / 1000 }' "$(grep -l k10temp /sys/class/hwmon/hwmon*/name | head -1 | xargs dirname)/temp1_input" 2>/dev/null)"
    echo "gpu_temp_c=$(gpu temperature.gpu)"
    echo "cstates=$(cstates)"
    echo "thp=$(sed 's/.*\[\(.*\)\].*/\1/' /sys/kernel/mm/transparent_hugepage/enabled)"
    echo "kent_llama=$(systemctl is-active kent-llama 2>/dev/null)"
}

check() {
    local s bad=() v; s="$(state)"
    get() { sed -n "s/^$1=//p" <<< "$s"; }
    [[ "$(get smt)" == "$SMT" ]] || bad+=("smt=$(get smt) (want $SMT)")
    [[ "$(get boost)" == "$BOOST" ]] || bad+=("boost=$(get boost) (want $BOOST)")
    [[ "$(get governor)" == "$GOVERNOR" ]] || bad+=("governor=$(get governor) (want $GOVERNOR)")
    [[ "$(get swap)" == "$SWAP" ]] || bad+=("swap=$(get swap) (want $SWAP)")
    [[ "$(get gpu_persistence)" == "$GPU_PERSISTENCE" ]] || bad+=("gpu_persistence=$(get gpu_persistence) (want $GPU_PERSISTENCE)")
    [[ "$(get gpu_idle_clock_mhz)" == "$GPU_LOCK_MHZ" ]] || bad+=("gpu clock=$(get gpu_idle_clock_mhz) MHz (want locked at $GPU_LOCK_MHZ)")
    v="$(get core_clock_mhz)"
    [[ "$v" =~ ^[0-9]+$ && "$v" -ge "$CLOCK_MIN_MHZ" && "$v" -le "$CLOCK_MAX_MHZ" ]] \
        || bad+=("core clock=$v MHz (want $CLOCK_MIN_MHZ-$CLOCK_MAX_MHZ)")
    echo "$s"
    if ((${#bad[@]})); then printf 'MISMATCH: %s\n' "${bad[@]}"; return 1; fi
    echo "OK: bench profile"
}

need_root() { [[ $EUID -eq 0 ]] || { echo "hostprofile: $1 needs root: sudo $0 $1" >&2; exit 2; }; }

set_governor() { local c; for c in $(cpus); do echo "$1" > "$CPU/cpu$c/cpufreq/scaling_governor"; done; }

case "${1:-}" in
    show) state ;;
    check) check ;;
    apply)
        need_root apply
        echo "$SMT" > "$CPU/smt/control"
        echo "$BOOST" > "$CPU/cpufreq/boost"
        set_governor "$GOVERNOR"
        [[ "$SWAP" == off ]] && swapoff -a
        nvidia-smi -pm 1 >/dev/null && nvidia-smi -lgc "$GPU_LOCK_MHZ,$GPU_LOCK_MHZ" >/dev/null
        logger -t kent-hostprofile -- "human:${SUDO_USER:-root} apply (bench profile)"
        check ;;
    defaults)
        need_root defaults
        nvidia-smi -rgc >/dev/null; nvidia-smi -pm 0 >/dev/null
        swapon -a
        set_governor schedutil
        echo 1 > "$CPU/cpufreq/boost"
        echo on > "$CPU/smt/control"
        logger -t kent-hostprofile -- "human:${SUDO_USER:-root} defaults (boot defaults)"
        state ;;
    *) echo "usage: hostprofile.sh show|check|apply|defaults" >&2; exit 2 ;;
esac
