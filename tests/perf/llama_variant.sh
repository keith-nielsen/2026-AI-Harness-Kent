#!/usr/bin/env bash
# llama_variant.sh NAME NP KVU(0|1) LV T TB — start llama-server on :8081 with production args except these,
# run llama_bench.py, stop the server. Logs and JSON land next to this script.
set -uo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
name=$1 np=$2 kvu=$3 lv=$4 t=$5 tb=$6
M=/srv/kent/models; MODEL="${MODEL:-Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf}"
kv=$([[ $kvu == 1 ]] && echo -kvu || echo -no-kvu)
export LD_LIBRARY_PATH=/opt/kent-llama/lib
nice -n 5 /opt/kent-llama/bin/llama-server -m "$M/$MODEL" --alias locally-run-model \
  -mm $M/Qwen3.6-35B-A3B-MTP-UD-F16-mmproj.gguf --no-mmproj-offload -ngl 99 -ncmoe 40 -c 262144 \
  -t "$t" -tb "$tb" -ub "${UB:-1024}" --jinja --no-mmap --parallel "$np" $kv -fa on -ctk q8_0 -ctv turbo3 \
  --spec-type none -rea off --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.00 --presence-penalty 0.1 \
  --repeat-penalty 1.0 --host 127.0.0.1 --port 8081 -lv "$lv" > "$D/$name.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; wait $pid 2>/dev/null' EXIT
for _ in $(seq 600); do
  curl -sf 127.0.0.1:8081/health >/dev/null && break
  kill -0 $pid 2>/dev/null || { echo "$name: server died"; tail -20 "$D/$name.log"; exit 1; }
  sleep 1
done
echo "$name: vram $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
python3 "$D/llama_bench.py" http://127.0.0.1:8081 "$name" "$np" "$D/$name.json"
