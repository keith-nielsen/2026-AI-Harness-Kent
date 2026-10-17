# What normal looks like — Kent on the reference machine

Compare against this after every change (upgrade, reboot, config switch) before calling anything a regression.
Each number names its source. Update it when a change is accepted, with the date and the record.

## Conformance
- **149 PASS / 0 FAIL / 15 INFO** (2026-10-16, bench mode off, sim-routed gateway).
  Record: bench/runs/prod-switch-v06-10160324/conformance.log.
- Bench mode ON changes results by design (SearXNG stopped, fixture server on :8888, dev gateway): run
  conformance with bench mode off, or read FAILs against bench mode first.

## `kent -q` (one-shot, operator)
Gateway records (`journalctl -u kent-litellm`, kent_event llm_call): a router call (~4.3K prompt tokens) then the
answer on `fast` (~11.3K: Hermes system prompt + tools).

| state | router | fast | total wall | when |
|---|---:|---:|---:|---|
| cold (first call after `kent llama start` or a change to Kent's prompt/state) | ~9–10 s | ~35–38 s | ~45–47 s | 2026-10-14 23:03 (old server), 2026-10-16 03:34 (new) |
| warm (prompt cached in the server) | 0.65 s | 1.1 s | ~8.8 s | 2026-10-16, v0.6.0 server |
| warm, old fork server (history) | 3.2 s | 5.8 s | 13.6 s | 2026-10-14 23:04 |

The remaining ~7 s of a warm `kent -q` is Hermes start-up (Python + sandbox), not the model.
Measure cold and warm as separate runs; never compare a cold run with a warm number.

## Local model server (v0.6.0, Q4 MTP, 128k, K q8_0 / V q4_0, MTP 2 p0)
Record: bench/runs/prod-switch-v06-10160324.
- VRAM: ~5.8 GB idle; peak 5,827 MiB with both slots on 16K prompts (8 GB card).
- Decode, one stream: ~30 tok/s prose, ~40 tok/s copy-like; both slots together ~40 tok/s.
- While one slot reads a long prompt the other decodes at ~4–5 tok/s (continuous batching; architecture 3.2.8).
- Prompt reading: ~450–490 tok/s at 8K, ~400 at 64K.
- CPU during decode: ~450% (five threads, -t 5); idle 0%.
- MTP acceptance: ~0.9 on copy-like text, ~0.6 on prose.
