#!/usr/bin/env bash
# stratacfg.sh — the Strata config variants for the 8 GB card (operator, 2026-10-07), derived from setup's own config
# by changing engine arguments only (no network, run in the image as uid 64010; every file 0600: it holds the API key).
#   setup  = setup pass 2 as written: --kv k8v4 --kv-resident 32768 (KV streaming). NEVER RUN: Strata #1188, k8v4 +
#            streaming degenerates into repetition, reproduced on an RTX 2060 SUPER 8 GB.
#   A      = --kv int8 --kv-resident 20480 (the 2060 SUPER owner's clean workaround; the target)
#   B      = --kv k8v4, no streaming (k8v4 alone is clean)
#   A and B also: --prefill 512 --no-prefill-borrow (#1009: an 8 GB card reads prompts ~40% slower with the default).
# Variants land in strata-data/config/variants/<name>.json; stratarun.sh copies one over strata-iq3_xxs.json.
set -euo pipefail
DATA=/media/administrator/DATA/strata-data
docker run -i --rm --network none --cap-drop ALL --security-opt no-new-privileges -v "$DATA":/data \
  --entrypoint .venv/bin/python strata:82f46a8 - <<'EOF'
import copy, json, os
d = "/data/config"; v = f"{d}/variants"; os.makedirs(v, exist_ok=True)
base_p = f"{v}/setup.json"
if not os.path.exists(base_p):                       # keep setup's own config once, as written
    base = json.load(open(f"{d}/strata-iq3_xxs.json"))
    json.dump(base, open(base_p, "w"), indent=1); os.chmod(base_p, 0o600)
base = json.load(open(base_p))

def drop(a, flag, n=1):
    if flag in a:
        i = a.index(flag); del a[i:i + 1 + n]

def setv(a, flag, val):
    if flag in a: a[a.index(flag) + 1] = val
    else: a += [flag, val]

def variant(name, kv, resident):
    c = copy.deepcopy(base); a = c["args"]
    setv(a, "--kv", kv)
    drop(a, "--kv-resident")
    if resident: a += ["--kv-resident", str(resident)]
    setv(a, "--prefill", "512")
    if "--no-prefill-borrow" not in a: a.append("--no-prefill-borrow")
    p = f"{v}/{name}.json"; json.dump(c, open(p, "w"), indent=1); os.chmod(p, 0o600)
    print(f"{name}: {' '.join(x for x in a if not x.startswith('/'))}")

variant("A", "int8", 20480)
variant("B", "k8v4", None)
EOF
