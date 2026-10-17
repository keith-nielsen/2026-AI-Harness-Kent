#!/usr/bin/env bash
# fittest.sh MODEL OUTDIR — does MODEL fit this box with the bench's serving settings?
# Starts the model with mutt.sh (all experts on the CPU, 256k context, 2 slots), then measures:
#   - VRAM and system RAM after load, and llama-server's own memory breakdown (from its log)
#   - a long-context request (~200k tokens of prompt): proves the KV cache and compute buffers fit at
#     real length, not just at load; records prefill speed
#   - a short decode for tokens per second
#   - the P3 tool-call checks (pretest_tools.py)
# Writes OUTDIR/fit.json and leaves the server stopped. Needs the GPU free (no campaign running).
set -uo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
m=$1 out=$2
mkdir -p "$out"
"$D/mutt.sh" stop >/dev/null 2>&1
LV=3 "$D/mutt.sh" start "$m" "$out/mutt" || { echo "fittest: $m did not start"; tail -30 "$out/mutt/llama.log"; exit 1; }
vram=$(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)
ram=$(free -m | awk '/^Mem/ {print $3 " MiB used, " $7 " MiB available"}')
/usr/bin/python3 - "$out" "$vram" "$ram" <<'EOF'
import json, sys, time, urllib.request
out, vram, ram = sys.argv[1:]
URL = "http://127.0.0.1:8081"
def post(path, body, timeout=3600):
    r = urllib.request.urlopen(urllib.request.Request(URL + path, json.dumps(body).encode(),
                               {"content-type": "application/json"}), timeout=timeout)
    return json.load(r)
res = {"vram_after_load": vram, "ram_after_load": ram}
filler = "\n".join(f"line {i}: sensor {i % 37} reported {(i * 7919) % 1000 / 10:.1f} C at step {i}" for i in range(60000))
n = len(post("/tokenize", {"content": filler})["tokens"])
target = 200_000
filler = filler[: int(len(filler) * target / n)]
t0 = time.time()
try:
    r = post("/v1/chat/completions", {"messages": [{"role": "user", "content": filler + "\n\nWhat was the last line number? Number only."}],
                                      "max_tokens": 16, "cache_prompt": False})
    t = r.get("timings") or {}
    res["long_context"] = {"ok": True, "prompt_tokens": r["usage"]["prompt_tokens"], "seconds": round(time.time() - t0),
                           "prefill_tok_s": round(t.get("prompt_per_second", 0), 1),
                           "answer": r["choices"][0]["message"].get("content", "")[:40]}
except Exception as e:  # noqa: BLE001
    res["long_context"] = {"ok": False, "error": str(e)[:300]}
r = post("/v1/chat/completions", {"messages": [{"role": "user", "content": "Write 200 words about thermal loggers."}],
                                  "max_tokens": 300, "cache_prompt": False})
res["decode_tok_s"] = round((r.get("timings") or {}).get("predicted_per_second", 0), 1)
json.dump(res, open(f"{out}/fit.json", "w"), indent=1)
print(json.dumps(res, indent=1))
EOF
vram_peak=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)
echo "vram after long context: $vram_peak" | tee -a "$out/fit.txt"
grep -E 'CUDA0 (model|KV|compute) buffer|CPU (model|KV)|offloaded|n_layer|ncmoe|failed|error' "$out/mutt/llama.log" | head -20 | tee -a "$out/fit.txt"
/usr/bin/python3 "$D/pretest_tools.py" | tee -a "$out/fit.txt"
"$D/mutt.sh" stop >/dev/null
