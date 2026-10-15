#!/usr/bin/python3 -I
"""kent-llama-launch — start llama-server for kent-llama.service from /etc/kent/llama/llama.env.

Ported from the operator's measured launcher (llamaserver-qwen36-optimum.sh, 2026-09-06);
the reasoning behind every number is in that script's header. Two slots with a unified KV cache
(2026-09-30 tuning pass): two requests run at once, each can use the whole context, and a single
stream keeps ~97% of its one-slot speed; total decode is capped at ~35 tok/s by the CPU experts,
so more slots only split it. Batch threads follow -t (5): -tb 4 was removed on 2026-10-05 (tests/bench/tbcheck.sh:
-8% when both slots decode, no gain anywhere). The measurements assumed SMT off and CPU boost off; those host settings
are not changed by Kent (no service touches them since 2026-10-05): the operator sets them by hand with
tests/bench/hostprofile.sh.

  kent-llama-launch            exec llama-server
  kent-llama-launch --print    print the command line instead
  kent-llama-launch --files    print the model files the settings select (for hash checks)

Production (operator decision 2026-10-16; upstream llama.cpp v0.6.0): Q4 MTP model, 128k context, K q8_0 / V q4_0,
MTP 2 drafts with no threshold (p_min 0), draft cache f16 (llama.cpp default: a quantized draft cache costs more
VRAM), -ub 1024, every expert layer on the CPU (-ncmoe 40). Measured: tests/bench/kvcheck.sh, runs kvcheck-10061119
(peak 5861 MiB) and kv-quality (probes same as q8/q8); MTP 2 p0 won depth runs depthcheck-10060026/-10060548.
256k and turbo3 V (the TheTom fork) are retired. v0.6.0 has no --no-mmap: -lm none loads the weights into memory.

Settings (environment, from llama.env): MODEL_SET mtp|uc, CTX 128k, UB, NCMOE, THINK 0|1,
ALIAS, PIN 0|1, CORES. Models are read from MODELDIR (the read-only bind mount).
The uc set has no MTP head (no drafts) and was not measured on v0.6.0.
"""
from __future__ import annotations

import os
import sys

BIN = "/opt/kent-llama/bin/llama-server"
MODELDIR = "/srv/kent/models"
HOST, PORT = "127.0.0.1", "8080"

MODEL_SETS = {  # model, mmproj, -ncmoe at 128k (mtp: as measured on v0.6.0; uc: fork-era fit probe)
    "mtp": ("Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf", "Qwen3.6-35B-A3B-MTP-UD-F16-mmproj.gguf", 40),
    "uc": ("Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf",
           "Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-F16-mmproj.gguf", 36),
}
CONTEXTS = {  # n_ctx, K cache, V cache, default -ub
    "128k": (131072, "q8_0", "q4_0", 1024),
}
# MTP: 2 drafts, no acceptance threshold; the draft cache stays f16 (no -ctkd/-ctvd).
SPEC = {"mtp": ["--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--spec-draft-p-min", "0"],
        "uc": ["--spec-type", "none"]}
SAMPLING = {  # Qwen's official per-mode values; non-thinking is pinned for harness work
    "0": ["-rea", "off", "--temp", "0.7", "--top-p", "0.80", "--top-k", "20", "--min-p", "0.00",
          "--presence-penalty", "0.1", "--repeat-penalty", "1.0"],
    "1": ["-rea", "on", "--temp", "1.0", "--top-p", "0.95", "--top-k", "20", "--min-p", "0.00",
          "--presence-penalty", "1.5", "--repeat-penalty", "1.0"],
}


class ConfigError(ValueError):
    pass


def _int(env: dict, key: str, default: int) -> int:
    v = env.get(key, "") or str(default)
    if not v.isdigit():
        raise ConfigError(f"{key} must be a whole number, got {v!r}")
    return int(v)


def model_files(env: dict) -> tuple[str, str]:
    ms = env.get("MODEL_SET", "mtp") or "mtp"
    if ms not in MODEL_SETS:
        raise ConfigError(f"unknown MODEL_SET {ms!r} (expected: {' | '.join(MODEL_SETS)})")
    model, mmproj, _ = MODEL_SETS[ms]
    d = env.get("MODELDIR", MODELDIR)
    return f"{d}/{model}", f"{d}/{mmproj}"


def build_argv(env: dict) -> list[str]:
    ms = env.get("MODEL_SET", "mtp") or "mtp"
    model, mmproj = model_files(env)
    ncmoe = MODEL_SETS[ms][2]
    ctx = env.get("CTX", "128k") or "128k"
    if ctx not in CONTEXTS:
        raise ConfigError(f"unknown CTX {ctx!r} (expected: {' | '.join(CONTEXTS)})")
    nctx, ctk, ctv, def_ub = CONTEXTS[ctx]
    ncmoe = _int(env, "NCMOE", ncmoe)
    ub = _int(env, "UB", def_ub)
    think = env.get("THINK", "0") or "0"
    if think not in SAMPLING:
        raise ConfigError(f"THINK must be 0 or 1, got {think!r}")
    alias = env.get("ALIAS", "locally-run-model") or "locally-run-model"
    argv = [BIN,
            "-m", model, "--alias", alias,
            "-mm", mmproj, "--no-mmproj-offload",
            "-ngl", "99", "-ncmoe", str(ncmoe), "-c", str(nctx),
            # No -tb: batch threads default to -t. The former -tb 4 was 8% slower when both slots decode
            # (2-token batches) and no faster anywhere (tbcheck, 2026-10-05).
            "-t", "5", "-ub", str(ub),
            "--jinja", "-lm", "none", "--parallel", "2", "-kvu",
            "-fa", "on", "-ctk", ctk, "-ctv", ctv,
            *SPEC[ms],
            *SAMPLING[think],
            "--host", HOST, "--port", PORT,
            "-lv", "1"]
    if (env.get("PIN", "0") or "0") == "1":
        cores = env.get("CORES", "2,4,5,6,7") or "2,4,5,6,7"
        if not all(c.isdigit() for c in cores.replace("-", ",").split(",")):
            raise ConfigError(f"CORES must be a CPU list like 2,4-7, got {cores!r}")
        argv = ["/usr/bin/taskset", "-c", cores, *argv]
    return argv


def main(args: list[str]) -> int:
    env = dict(os.environ)
    try:
        if args[:1] == ["--files"]:
            print("\n".join(model_files(env)))
            return 0
        argv = build_argv(env)
    except ConfigError as e:
        print(f"kent-llama-launch: {e}", file=sys.stderr)
        return 2
    if args[:1] == ["--print"]:
        print(" ".join(argv))
        return 0
    for f in (argv[argv.index("-m") + 1], argv[argv.index("-mm") + 1]):
        if not os.path.isfile(f):
            print(f"kent-llama-launch: missing {f}", file=sys.stderr)
            return 2
    os.execv(argv[0], argv)
    return 1  # not reached


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
