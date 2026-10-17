"""System monitor for benchmark requests: what else could have moved a measurement.

  with Window(server_pid) as w:  ...one request...
  w.result  -> dict of what happened during the request, and w.result["flags"] (empty = clean)

Sampled once a second during the window: CPU clock (all cores, from /proc/cpuinfo), CPU temperature (k10temp
Tctl), the GPU's graphics clock, temperature and power (nvidia-smi). Counted over the window: CPU time used by
the server and by everything else (/proc/stat), swap-in/out (/proc/vmstat), the server's major page faults,
and the GPU's clock-reduction counters (power cap, thermal; microseconds, cumulative in the driver).
Flags: swap activity, server major faults, other processes > 0.4 core (5% of 8), a CPU core below MIN_MHZ,
GPU clock not at GPU_LOCK_MHZ while busy, any GPU clock-reduction counter increase.
CPU clock is judged on busy cores only (> 50% busy in that second): idle cores report dips in sleep states.
"""
import glob, os, statistics, subprocess, threading, time

MIN_MHZ = 3500            # boost off: every core should sit at the 3.6 GHz base with the performance governor
GPU_LOCK_MHZ = 1650       # nvidia-smi -lgc 1650,1650
OTHER_CORES_MAX = 0.4     # 5% of 8 cores
GPU_COUNTERS = ["clocks_event_reasons_counters.sw_power_cap", "clocks_event_reasons_counters.sw_thermal_slowdown",
                "clocks_event_reasons_counters.hw_thermal_slowdown", "clocks_event_reasons_counters.hw_power_brake_slowdown"]
TICK = os.sysconf("SC_CLK_TCK")


def _k10temp():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if open(f"{d}/name").read().strip() == "k10temp":
                return f"{d}/temp1_input"
        except OSError:
            pass
    return None


TEMP = _k10temp()


def cpu_mhz():
    """{cpu number: MHz} for online cores."""
    out, cur = {}, None
    for l in open("/proc/cpuinfo"):
        if l.startswith("processor"):
            cur = int(l.split(":")[1])
        elif l.startswith("cpu MHz"):
            out[cur] = float(l.split(":")[1])
    return out


def core_busy():
    """{cpu number: busy jiffies} from the per-cpu lines of /proc/stat."""
    out = {}
    for l in open("/proc/stat"):
        if l.startswith("cpu") and l[3].isdigit():
            f = l.split()
            v = [int(x) for x in f[1:]]
            out[int(f[0][3:])] = sum(v) - v[3] - v[4]
    return out


def cpu_temp():
    return int(open(TEMP).read()) / 1000 if TEMP else None


def busy_jiffies():
    """(user incl. nice, system, irq + softirq) jiffies of the whole machine."""
    f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    return f[0] + f[1], f[2], f[5] + f[6]


def proc_stat(pid):
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return int(f[9]), (int(f[11]), int(f[12]))   # majflt, (utime, stime)


def vmstat():
    d = dict(l.split() for l in open("/proc/vmstat"))
    return int(d["pswpin"]), int(d["pswpout"])


def gpu(fields):
    out = subprocess.run(["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    return [float(x) if x.replace(".", "", 1).isdigit() else None for x in out.split(", ")]


def static_state():
    """Settings that must hold for the whole run (checked once by the job and recorded)."""
    gov = set()
    for p in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"):
        try:
            gov.add(open(p).read().strip())
        except OSError:          # offline SMT siblings (SMT off) answer EBUSY
            pass
    return {"governor": sorted(gov), "smt": open("/sys/devices/system/cpu/smt/control").read().strip(),
            "boost": open("/sys/devices/system/cpu/cpufreq/boost").read().strip(),
            "gpu": subprocess.run(["nvidia-smi", "--query-gpu=persistence_mode,clocks.max.gr,clocks.applications.gr",
                                   "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()}


class Window:
    def __init__(self, pid):
        self.pid, self.samples, self._stop = pid, [], threading.Event()

    def _sample(self):
        prev = core_busy()
        while not self._stop.is_set():
            self._stop.wait(1.0)
            cur, mhz = core_busy(), cpu_mhz()
            # only cores busy > 50% of the last second: an idle core's reported clock dips in sleep states
            busy = [mhz[c] for c in mhz if c in cur and (cur[c] - prev.get(c, cur[c])) / TICK > 0.5]
            prev = cur
            mn = min(busy) if busy else None
            avg = sum(busy) / len(busy) if busy else None
            g = gpu(["clocks.gr", "temperature.gpu", "power.draw", "utilization.gpu"])
            self.samples.append({"cpu_min_mhz": mn, "cpu_avg_mhz": avg, "busy_cores": len(busy), "cpu_c": cpu_temp(),
                                 "gpu_mhz": g[0], "gpu_c": g[1], "gpu_w": g[2], "gpu_util": g[3]})

    def __enter__(self):
        self.t0 = time.time()
        self.busy0, (self.mf0, self.cpu0), self.vm0 = busy_jiffies(), proc_stat(self.pid), vmstat()
        self.gc0 = gpu(GPU_COUNTERS)
        self.th = threading.Thread(target=self._sample, daemon=True)
        self.th.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self.th.join()
        dt = time.time() - self.t0
        busy1, (mf1, cpu1), vm1, gc1 = busy_jiffies(), proc_stat(self.pid), vmstat(), gpu(GPU_COUNTERS)
        rate = lambda a, b: (a - b) / TICK / dt
        su, ss = rate(cpu1[0], self.cpu0[0]), rate(cpu1[1], self.cpu0[1])
        server = su + ss
        # Only other USER-space time is "other load" (background programs). Kernel and interrupt time not charged to
        # the server process is recorded but not flagged: on 2026-10-05 a Q6 run showed the same 0.74 cores in two
        # rounds, i.e. work done for the server (driver threads, GPU-transfer interrupts), not background noise.
        other_user = max(0.0, rate(busy1[0], self.busy0[0]) - su)
        other_kernel = max(0.0, rate(busy1[1], self.busy0[1]) - ss)
        irq = rate(busy1[2], self.busy0[2])
        other = other_user
        s = self.samples or [{}]
        r = {"secs": round(dt, 2), "server_cores": round(server, 2), "other_cores": round(other, 2),
             "other_user_cores": round(other_user, 2), "other_kernel_cores": round(other_kernel, 2),
             "irq_cores": round(irq, 2),
             "swap_in": vm1[0] - self.vm0[0], "swap_out": vm1[1] - self.vm0[1], "server_majflt": mf1 - self.mf0,
             "gpu_counter_us": [None if a is None or b is None else b - a for a, b in zip(self.gc0, gc1)],
             "cpu_min_mhz": min([x["cpu_min_mhz"] for x in s if x.get("cpu_min_mhz")] or [None], key=lambda v: v or 0),
             "cpu_avg_mhz": round(statistics.mean([x["cpu_avg_mhz"] for x in s if x.get("cpu_avg_mhz")] or [0])),
             "busy_cores": max([x.get("busy_cores", 0) for x in s] or [0]),
             "cpu_max_c": max((x.get("cpu_c") or 0) for x in s), "gpu_max_c": max((x.get("gpu_c") or 0) for x in s),
             "gpu_max_w": max((x.get("gpu_w") or 0) for x in s),
             "gpu_mhz_busy": sorted({x.get("gpu_mhz") for x in s if (x.get("gpu_util") or 0) > 0})}
        f = []
        if r["swap_in"] or r["swap_out"]: f.append("swap")
        if r["server_majflt"]: f.append("majflt")
        if other > OTHER_CORES_MAX: f.append(f"other_load {other:.2f} cores (user)")
        if r["cpu_min_mhz"] is not None and r["cpu_min_mhz"] < MIN_MHZ: f.append(f"cpu_mhz {r['cpu_min_mhz']:.0f}")
        if any(m not in (None, GPU_LOCK_MHZ) for m in r["gpu_mhz_busy"]): f.append(f"gpu_mhz {r['gpu_mhz_busy']}")
        if any(d for d in r["gpu_counter_us"] if d): f.append(f"gpu_counters {r['gpu_counter_us']}")
        r["flags"] = f
        self.result = r
        return False
