# Computer In Use Testing Plan

Proposed 2026-10-13. Status: **awaiting operator go** (Phase A first; Phase B optional; Phase C needs its own go).

**Status 2026-10-13 evening:**
- **C done** (daytime window): Q4 = Q6 on 5,184 paired questions (pooled Q4 +0.33 pt, 95% -0.15..+0.81, p 0.21;
  discordance 3.1% = batching-noise level). TruthfulQA dropped (RAM). Details in the resume card.
- **A4 done:** RSS = weights + ~2 GB per -np sequence (logits buffer), not mmap.
- **A6 done:** submit.sh MEMMAX/DEADLINE/NP/KEEP_APPS; mcqcheck.sh RSS logging, deadline, set-major order.
- **A1 superseded:** discordance measured directly (3.1%); a 1-point gap is now resolvable with ~5k questions.
- **Follow-up done (night 10-13/14):** Q4 KV cache vs f16, 5,184 paired q each: K q8/V q4 -0.48 pt, q8/q8 -0.17,
  q4/q4 -0.33; all within ~0.5 pt at 2k context (resume card).
- **A3 done:** low MMLU/ARC = cloze scoring (options never shown); valid for comparisons only.
- **A5 done:** b24/r40 grader fixes (`not_asserted`, r40 spec); c08/i45/c24/t60/c13 are genuine model failures.
- **B1 PASS:** patched server = stock, bit for bit, with decisions off, on, and running concurrently (CPU, Qwen3-0.6B). **A2 deferred** (operator: bigger discussion).

**Question:** Q6 scored below Q4 on the probes (54 vs 58 of 126), but KL divergence says Q6 is closer to BF16. Is Q6
really better, by how much, and is that worth −12..15% decode?

**Constraint:** the operator needs the machine for other work. The MCQ runs (`tests/bench/mcqcheck.sh`) stop
kent-llama and need the GPU plus 22–32 GB RAM; only ~19 GB is free while production runs (62 GB total, no swap,
production holds ~21 GB RAM + 6.1 of 8 GB VRAM). So: no-load work during the day, one overnight window for the
heavy runs.

## Phase A — no machine load (runs while the operator works)

| # | Test | Steps | Time | Likely outcome |
|---|---|---|---|---|
| A1 | Sample-size (power) analysis | Model the paired Q4/Q6 comparison: ~3% batching flips plus an assumed 3–6% real quant disagreement; compute questions needed per detectable gap. | ~20 min | ~1,600 questions to detect a 2-point gap, ~6,000 for 1 point. Sets the overnight size. |
| A2 | Published quant comparisons | Collect Unsloth/community KLD, top-1 agreement and benchmark data for Qwen3.6-35B-A3B UD-Q4_K_XL vs Q6_K_XL. | ~30 min | Probably Q6 within ~0.5–1 pt of BF16, Q4 within ~1–2: expect a gap of 2 points or less. If the published data settles it, the overnight run may shrink or be dropped. |
| A3 | Why MMLU / ARC-C score low | Read llama-perplexity's multiple-choice scoring (normalization, prompt format) and check our dataset files. | ~30 min | Either a known effect of this scoring method (fine for a Q4-vs-Q6 comparison) or a format problem to fix before the overnight run. |
| A4 | Where the 50 GB RSS went | Read pilot logs, memory figures and the loader code; predict Q4/Q6 RSS with `--no-mmap`. | ~30 min | Likely mmap'd file pages plus pinned host copies, so the model was held twice. Prediction ~22 GB (Q4), ~32 GB (Q6); C1 confirms. |
| A5 | Fix broken probes | Fix prompt/grader issues in c24, c13, t60, r40 (+ c08, i45); run probe unit tests (seconds of CPU). | ~1 h | 6 fewer always-failing probes; cleaner probe comparisons and later JEV calibration. |
| A6 | Make bench jobs safe | `submit.sh`: RAM limit (job killed, not host), lowest CPU priority, optional core pinning. `mcqcheck.sh`: `--no-mmap`, `-t 8`, verbose buffer sizes, peak-RSS logging, automatic abort over an RSS threshold. Check the limits with a trivial `sleep` job. | ~30 min | Safe settings in place; nothing heavy runs. |

**Checkpoint 1:** report A1–A4 to the operator; operator decides overnight size (or no run).

## Phase B — optional, light (2 cores, ~2 GB RAM, lowest priority)

| # | Test | Steps | Time | Likely outcome |
|---|---|---|---|---|
| B1 | JEV patch leaves normal output unchanged (JEV step 1) | Download a ~1 GB Qwen 0.6B GGUF; `llama-perplexity --kl-divergence` with the stock v0.6.0 CPU build vs the patched build, decision off and `--decision-seqs 8`. | ~20–30 min wall clock | Bit-identical output, or a specific diff to investigate. Says nothing about calibration on the real model. |

The JEV gate is parked, so this is only worth doing to have it ready for later. Recommendation: skip unless the
operator wants it.

## Phase C — one overnight window (machine to itself)

Before leaving, the operator closes browsers and Discord (the host profile check refuses otherwise). The job stops
kent-llama, so Kent is offline overnight, and restarts it at the end. Boost state gets checked after setup, not before.

| # | Step | Run time | Gate / likely outcome |
|---|---|---|---|
| C1 | Q4 memory check: 100 HellaSwag + 100 MMLU-val, `--no-mmap`, `-t 8` | ~5–10 min | Expect ~22 GB RSS; also measures `-t 8` speed (guess 1.0–1.2 s/question, down from ~1.5). Automatic abort above the RSS cap. |
| C2 | Q6 memory check, same questions | ~6–12 min | Expect ~32 GB RSS. Above ~45 GB the job stops here and Q6 does not run. |
| C3 | Main run: Q4, then Q6 | ~5–7 h | See below. |

**C3 question set:** about 7.5k questions in total. Full MMLU-val (1,531), ARC-C, ARC-E, Winogrande (1,267) and
TruthfulQA (817) come to ~4.5k; plus a fixed 3,000-question HellaSwag sample. Full sets (~15.5k questions) would
roughly halve the detectable gap but take ~10–14 h, i.e. two nights.

**Expected result from C3:** per-set accuracy with 95% ranges of about ±1–2.5 points, paired McNemar tests per set,
and a pooled result. **Most likely:** Q6 ahead by 0–2 points, probably not significant within a single set but
possibly significant pooled.

**Checkpoint 2:** recommendation from the report:
- **No real gap:** Q4 stays as production (faster, less VRAM); the probe gap was noise.
- **Q6 clearly ahead:** weigh its quality gain against −12..15% decode, −20% prefill and ~+0.4 GB VRAM for the
  production and JEV choice.

## Risks

- **Host OOM:** handled by the RAM cap plus the C1/C2 gates. There is still no swap, so the cap is what protects the host.
- **Kent offline overnight:** intended; the restart at the end is part of the script.
- **Inconclusive result** (gap under ~1 point): means the quants are equivalent for our purposes, which is still a
  usable decision.
- **Q6 slower than guessed** (CPU-bound, larger experts): C3 could spill past morning. C1/C2 measure the real speed;
  trim the HellaSwag sample before launch if needed.

## Total time

- Phase A: ~3.5 h of my time, no load on the machine.
- Phase B: optional, ~30 min, barely noticeable.
- Phase C: one night, ~6–7.5 h including the gates. Needs operator go to launch.
