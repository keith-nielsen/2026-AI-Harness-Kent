#!/usr/bin/env python3
"""watch.py — one event stream for every bench job (made for a single Monitor / terminal).

Prints, as they happen: bench units starting and ending (with their result), campaign and job log lines
(start, suite, done, errors), every crew result, each suite's "run: done" summary, any Traceback or
refusal, and sandbox containers running longer than 5 minutes. Exits once no kent-bench-* unit has been
active for two polls. Logs watched: runs/*/campaign.log, *check.log, followup.log, kld.log, flashdl.log, stratabuild.log, stratasetup.log, stratarun.log, mcqcheck.log, pdcuda.log, llama-builds/pdbuildcuda.log.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

RUNS = Path.home() / "Documents/repo/bench/runs"
KEEP = re.compile(r"campaign:|followup:|q8check:|speccheck:|llamaprep:|mtpcheck:|lifecyclecheck:|tbcheck:|depthcheck:|edge0check:|buildv06:|kvcheck:|kld:|flashdl:|stratabuild:|stratasetup:|stratarun:|mcqcheck:|pdcuda:|pdbuildcuda:|cachecheck:|triagecheck:|run: done|Traceback|[Ee]rror|refus|FAIL.*timed out|^ g\d\d ")
POLL = 15


def units() -> dict[str, str]:
    out = subprocess.run(["systemctl", "--user", "list-units", "--all", "--type=service", "--plain",
                          "--no-legend", "kent-bench-*"], capture_output=True, text=True).stdout
    return {l.split()[0].removesuffix(".service"): l.split()[2] for l in out.splitlines() if l.strip()}


def stuck_sandboxes() -> list[str]:
    out = subprocess.run(["docker", "ps", "--filter", "name=kent-bench-sandbox-", "--format",
                          "{{.Names}} {{.RunningFor}}"], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if re.search(r"\d+ (minutes|hours|days)", l)
            and not re.search(r"^\S+ [1-4] minutes", l)]


def logs() -> list[Path]:
    return sorted({*RUNS.glob("*/campaign.log"), *RUNS.glob("*/*check.log"), *RUNS.glob("*/followup.log"), *RUNS.glob("*/kld.log"), *RUNS.glob("*/flashdl.log"), *RUNS.glob("*/stratabuild.log"), *RUNS.glob("*/stratasetup.log"), *RUNS.glob("*/stratarun.log"), *RUNS.glob("*/mcqcheck.log"), *RUNS.glob("*/pdcuda.log"), *RUNS.glob("*/cachecheck.log"), *RUNS.parent.glob("llama-builds/pdbuildcuda.log")})


def emit(s: str):
    print(s, flush=True)


def main():
    offset = {p: p.stat().st_size for p in logs()}      # only new lines from now on
    seen = units()
    for u, st in seen.items():
        emit(f"watch: {u} {st}")
    idle = 0
    warned = set()
    while True:
        time.sleep(POLL)
        for p in logs():
            size = p.stat().st_size
            if size < offset.get(p, 0):
                offset[p] = 0
            if size > offset.get(p, 0):
                with p.open() as f:
                    f.seek(offset.get(p, 0))
                    new = f.read()
                offset[p] = size
                for line in new.splitlines():
                    if KEEP.search(line):
                        emit(f"[{p.parent.name}] {line.rstrip()}")
        now = units()
        for u in now.keys() | seen.keys():
            if now.get(u) != seen.get(u):
                res = subprocess.run(["systemctl", "--user", "show", u, "-p", "Result", "--value"],
                                     capture_output=True, text=True).stdout.strip()
                emit(f"watch: {u} {seen.get(u, 'new')} -> {now.get(u, 'gone')}{f' (result {res})' if res else ''}")
        seen = now
        for s in stuck_sandboxes():
            if s.split()[0] not in warned:
                warned.add(s.split()[0]); emit(f"watch: sandbox container running long: {s}")
        idle = 0 if any(st == "active" for st in now.values()) else idle + 1
        if idle >= 2:
            emit("watch: no bench job active; exiting")
            return


if __name__ == "__main__":
    sys.exit(main())
