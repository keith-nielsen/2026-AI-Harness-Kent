#!/usr/bin/env python3
"""GPU clock-lock dry run: a short, bounded full-load burst and what the clock, power and temperature did.

  ~/ai-env/bin/python3 gpu_lock_dryrun.py LABEL [SECONDS]      (default 10 s, at most 15)

Load: fp16 4096x4096 matrix multiplies in a loop (about 100 MB of VRAM; the same kind of load a game puts on the
card). Hard stops: SECONDS elapsed, GPU temperature >= 80 C, or power >= 165 W (the limit is 175 W) — whichever
comes first. Samples every 0.25 s: graphics clock, SM clock, temperature, power, utilisation; plus the driver's
clock-reduction counters before and after (power cap, thermal). Prints a summary line to compare unlocked
against locked (nvidia-smi -lgc 1650,1650).
"""
import subprocess, sys, threading, time
import torch

LABEL = sys.argv[1]
SECS = min(float(sys.argv[2]) if len(sys.argv) > 2 else 10.0, 15.0)
MAX_C, MAX_W = 80, 165
Q = "clocks.gr,clocks.sm,temperature.gpu,power.draw,utilization.gpu"
C = ("clocks_event_reasons_counters.sw_power_cap,clocks_event_reasons_counters.sw_thermal_slowdown,"
     "clocks_event_reasons_counters.hw_thermal_slowdown,clocks_event_reasons_counters.hw_power_brake_slowdown")


def q(fields):
    out = subprocess.run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=5).stdout.strip()
    return [float(x) for x in out.split(", ")]


stop, samples, why = threading.Event(), [], ["time"]


def sampler():
    while not stop.is_set():
        s = q(Q)
        samples.append(s)
        if s[2] >= MAX_C or s[3] >= MAX_W:
            why[0] = f"guard: {s[2]:.0f} C, {s[3]:.0f} W"
            stop.set()
        stop.wait(0.25)


before = q(C)
a = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
b = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
th = threading.Thread(target=sampler, daemon=True)
th.start()
t0, n = time.time(), 0
while not stop.is_set() and time.time() - t0 < SECS:
    for _ in range(20):
        c = a @ b
    torch.cuda.synchronize()
    n += 20
dt = time.time() - t0
stop.set()
th.join()
after = q(C)
del a, b, c
torch.cuda.empty_cache()

busy = [s for s in samples if s[4] >= 90] or samples
tflops = n * 2 * 4096 ** 3 / dt / 1e12
print(f"{LABEL}: {dt:.1f} s ({why[0]}), {tflops:.1f} TFLOP/s; busy samples {len(busy)}/{len(samples)}: "
      f"gr clock {min(s[0] for s in busy):.0f}-{max(s[0] for s in busy):.0f} MHz, "
      f"temp max {max(s[2] for s in samples):.0f} C, power max {max(s[3] for s in samples):.0f} W; "
      f"counter deltas us (power cap, sw thermal, hw thermal, power brake) "
      f"{[round(y - x) for x, y in zip(before, after)]}")
