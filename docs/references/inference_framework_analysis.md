# Inference Framework Analysis — 3-Target Hardware Comparison

> **Ticket:** #3013  
> **Date:** 2026-05-26  
> **Author:** Keith Nielsen

## Hardware Targets

| Spec | Dev Machine (current) | Minisforum MS-S1 MAX | M5 MAX Studio |
|------|----------------------|---------------------|----------------|
| CPU | AMD Ryzen 3700 (8C/16T) | AMD Ryzen AI Max+ 395 (16C/32T) | Apple M5 MAX (16C) |
| RAM | 64 GB DDR4 | 128 GB LPDDR5X **unified** | 128 GB **unified** |
| GPU | RTX 2060 SUPER 8GB VRAM | Radeon 800M iGPU (up to 96 GB shared) | M5 MAX GPU (up to 128 GB shared) |
| GPU API | CUDA 7.5 | ROCm / Vulkan | Metal / MLX |
| Disk | 2 TB SSD + 8 TB HDD | TBD | TBD |
| OS | Linux Mint (Ubuntu) | Linux / Windows | macOS |

---

## Framework Comparison

### 1. llama.cpp (via Ollama or direct)

| Criterion | RTX 2060 (8GB) | MS-S1 (Strix Halo) | M5 MAX |
|-----------|----------------|---------------------|--------|
| **Backend** | CUDA (mature) | ROCm (improving) / Vulkan (backup) | Metal (mature) |
| **Quantizations** | GGUF: Q2–Q8, IQ1–IQ4 | GGUF: all | GGUF: all |
| **Max model (Q4_KM)** | 7B (~5.5 GB) | 70B (~41 GB) or larger | 70B+ (~41 GB) |
| **KV cache** | ~1.5 GB for 32K ctx (7B) | ~12 GB for 128K ctx (70B) | ~12 GB for 128K ctx (70B) |
| **Speed (7B Q4)** | ~30-40 t/s | ~40-60 t/s (ROCm) | ~50-80 t/s (Metal) |
| **Speed (70B Q4)** | ❌ Won't fit | ~8-15 t/s | ~12-20 t/s |
| **Setup** | `apt install ollama` | `ollama serve` + ROCm drivers | `brew install ollama` |
| **Pros** | ✅ Battle-tested, easiest setup, huge community, flash attention 2 | ✅ Works across all 3 targets via different backends, most portable | ✅ Metal backend is mature, excellent perf |
| **Cons** | ❌ 8GB VRAM limits model size severely, IQ quantization helps but loses quality | ❌ ROCm on Strix Halo is cutting-edge — may need bleeding-edge ROCm builds | ❌ No EXL2, Metal can't match CUDA peak for prompt processing |

**Verdict: Best universal fallback. Runs on all three targets with minimal config changes.**

---

### 2. TabbyAPI (EXL2, exllamav2 backend)

| Criterion | RTX 2060 (8GB) | MS-S1 (Strix Halo) | M5 MAX |
|-----------|----------------|---------------------|--------|
| **Backend** | CUDA only | ❌ Not available | ❌ Not available |
| **Quantizations** | EXL2 (2.0–8.0 bpw) | — | — |
| **Max model (4.0 bpw)** | 7B (~4.5 GB) + 1.5 GB KV cache = 6 GB ✅ | ❌ Cannot run | ❌ Cannot run |
| **Speed (7B 4.0 bpw)** | ~45-60 t/s (fastest single-batch option) | — | — |
| **Setup** | `pip install tabbyapi` + download EXL2 model | — | — |
| **Pros** | ✅ Fastest single-batch inference on CUDA, excellent memory management (load/unload models dynamically), OpenAI-compatible API built in | 🚫 Not viable | 🚫 Not viable |
| **Cons** | ❌ CUDA-only — no AMD or Apple support, smaller model ecosystem for EXL2 vs GGUF | ❌ No AMD backend | ❌ No Apple backend |

**Verdict: Best single-batch perf on the RTX 2060. Worth investigating for the dev machine only. Cannot run on target machines.**

---

### 3. exllamav2 (standalone, no TabbyAPI)

| Criterion | RTX 2060 (8GB) | MS-S1 (Strix Halo) | M5 MAX |
|-----------|----------------|---------------------|--------|
| **Backend** | CUDA only | ❌ Not available | ❌ Not available |
| **Comparison to TabbyAPI** | TabbyAPI IS exllamav2 under the hood. Standalone exllamav2 is a Python library; TabbyAPI wraps it as a service. TabbyAPI is the recommended path for the gateway use case. | — | — |
| **Verdict** | Same as TabbyAPI — use TabbyAPI instead. | 🚫 | 🚫 |

---

### 4. Aphrodite Engine

| Criterion | RTX 2060 (8GB) | MS-S1 (Strix Halo) | M5 MAX |
|-----------|----------------|---------------------|--------|
| **Backend** | CUDA only | ❌ | ❌ |
| **Quantizations** | GPTQ, AWQ, FP8 | — | — |
| **7B model (AWQ)** | ~5.5 GB + KV cache → tight fit (~7 GB total) | — | — |
| **Speed** | ~35-50 t/s (good, fused kernels) | — | — |
| **Pros** | ✅ Multiple quant support, fused attention kernels | 🚫 | 🚫 |
| **Cons** | ❌ CUDA-only, smaller community than llama.cpp, less actively maintained recently | 🚫 | 🚫 |

**Verdict: CUDA-only, smaller community. Lower priority than TabbyAPI or llama.cpp.**

---

### 5. vLLM

| Criterion | RTX 2060 (8GB) | MS-S1 (Strix Halo) | M5 MAX |
|-----------|----------------|---------------------|--------|
| **Backend** | CUDA | ROCm (beta) | ❌ Not available |
| **Quantizations** | AWQ, GPTQ, FP8 | AWQ, GPTQ | — |
| **7B model (AWQ)** | ~5.5 GB + PagedAttention overhead → marginal fit | ✅ Would work | — |
| **Speed** | ~30-45 t/s | ~25-40 t/s (ROCm) | — |
| **Pros** | ✅ PagedAttention maximizes VRAM efficiency, continuous batching, production-grade API, OpenAI-compatible | ✅ ROCm support exists (preview) | 🚫 |
| **Cons** | ❌ Overhead for single-user use case — designed for multi-user serving, heavier memory footprint than TabbyAPI or llama.cpp | ❌ ROCm support is beta quality | ❌ No macOS support at all |

**Verdict: Overspecified for the dev machine (single user), but the ROCm support makes it viable on the MS-S1. No macOS path.**

---

### 6. TensorRT-LLM

| Criterion | RTX 2060 (8GB) | MS-S1 (Strix Halo) | M5 MAX |
|-----------|----------------|---------------------|--------|
| **Backend** | CUDA only (requires modern NVIDIA) | ❌ | ❌ |
| **Support** | ❌ RTX 2060 is SM 7.5 — TensorRT-LLM requires SM 8.0+ (RTX 30/40 series) for most features | 🚫 | 🚫 |

**Verdict: Not viable. Requires RTX 30xx or newer. The 2060 SUPER (Turing/SM 7.5) is excluded.**

---

## Recommended Model Configurations

### Dev Machine — RTX 2060 SUPER 8GB

**Router (CPU, RAM only):**
| Model | Size | Framework | RAM | Speed |
|-------|------|-----------|-----|-------|
| Qwen2.5-0.5B Q8 | 0.5 GB | llama.cpp (CPU only) | ~700 MB | ~50 ms |
| SmolLM2-360M Q8 | 0.4 GB | llama.cpp (CPU only) | ~500 MB | ~30 ms |
| **Arch 1B** (if available) | ~1 GB | llama.cpp (CPU only) | ~1.2 GB | ~80 ms |

**Workhorse (single model for fast+smart):**
| Model | Quant | VRAM | Framework | Notes |
|-------|-------|------|-----------|-------|
| Qwen2.5-7B | EXL2 4.0 bpw | ~5.0 GB | TabbyAPI or exllamav2 | **Recommended** — fast, strong reasoning, 32K context fits |
| Qwen2.5-7B | GGUF Q4_K_M | ~5.5 GB | llama.cpp | Good fallback, slightly lower quality than EXL2 |
| Gemma 3 12B | GGUF IQ4_XS | ~7.0 GB | llama.cpp | Tight fit, better quality, less KV cache room |
| Llama 3.2 8B | EXL2 4.0 bpw | ~5.5 GB | TabbyAPI | More general knowledge but weaker at code than Qwen |

**Recommended dev config (YAML concept):**
```yaml
model_list:
  - model_name: "router"
    litellm_params:
      model: "ollama/qwen2.5:0.5b-q8_0"     # CPU, <100ms
      api_base: "http://localhost:11434"

  - model_name: "fast"                     # both hit the same
    litellm_params:
      model: "openai/qwen2.5-7b-exl2"       # via TabbyAPI at :5000
      api_base: "http://localhost:5000"

  - model_name: "smart"                    # physical model
    litellm_params:
      model: "openai/qwen2.5-7b-exl2"
      api_base: "http://localhost:5000"
```

### Minisforum MS-S1 MAX AI (Strix Halo, 128GB unified)

The Strix Halo changes everything — 96 GB of the 128 GB can be used as VRAM via the Radeon 800M iGPU. This means models up to 70B+ fit easily.

| Model | Quant | Unified Mem | Framework | Notes |
|-------|-------|-------------|-----------|-------|
| Qwen3.6-27B | Q6_K | ~20 GB | llama.cpp (ROCm) | **Smart tier — matches production** |
| Llama 3.3 70B | Q4_K_M | ~41 GB | llama.cpp (ROCm) | Full frontier capability locally |
| DeepSeek V2 Lite 16B | Q8_0 | ~16 GB | llama.cpp (ROCm) | Fast tier |
| Gemma 4 4B | Q8_0 | ~4 GB | llama.cpp (ROCm) | Router |

Router still runs on CPU (negligible). The 96 GB shared memory means ALL three production tiers fit simultaneously with room for multiple concurrent Gents:

```
T0 Router:   4 GB  (Gemma 4 4B Q8_0)
T1 Fast:    16 GB (DeepSeek V2 Lite 16B Q8_0)
T2 Smart:   20 GB (Qwen3.6-27B Q6_K)
KV caches:  10 GB
Headroom:   46 GB ← for multiple Gent contexts, additional models
```

**ROCm caveat:** AMD's ROCm support for the Strix Halo iGPU (RDNA 3.5) will be very new at this hardware's launch. Expect to run the latest ROCm 6.x with potential instability. llama.cpp's Vulkan backend is a safe fallback (slower but works).

### M5 MAX Studio (128GB unified)

Apple Silicon's MLX framework is the standout here. Unlike the MS-S1 where ROCm is cutting-edge, Metal/MLX on Apple Silicon is mature and well-optimized.

| Model | Quant | Unified Mem | Framework | Notes |
|-------|-------|-------------|-----------|-------|
| Qwen3.6-27B | Q6_K | ~20 GB | MLX or llama.cpp (Metal) | **Smart tier** |
| Llama 3.3 70B | Q4_K_M | ~41 GB | MLX or llama.cpp (Metal) | Frontier — **best perf on this machine** |
| DeepSeek V2 Lite | Q8_0 | ~16 GB | MLX (fastest) or Metal | Fast tier |
| Gemma 4 4B | Q8_0 | ~4 GB | llama.cpp (Metal) | Router |

**MLX advantage:** Apple's MLX framework is significantly faster than llama.cpp's Metal backend for prompt processing (prefill) on Apple Silicon — sometimes 2-3x faster for large contexts. For the M5 MAX, MLX is the optimal choice for the 70B frontier model.

---

## Framework Recommendation Summary

| Target | Router | Fast+Smart | Frontier | Best Framework |
|--------|--------|------------|----------|----------------|
| **RTX 2060 (8GB)** | llama.cpp CPU | TabbyAPI (EXL2) | Cloud only | TabbyAPI for workhorse, llama.cpp for router + cloud fallback |
| **MS-S1 Strix Halo** | llama.cpp CPU | llama.cpp (ROCm) | llama.cpp (ROCm) or cloud | llama.cpp is the only option with ROCm support |
| **M5 MAX Studio** | llama.cpp CPU | MLX or llama.cpp (Metal) | MLX for 70B+ | MLX for optimal performance on large models |

### Dev Machine (RTX 2060) — Recommended Setup

```
TabbyAPI (port 5000)        llama.cpp / Ollama (port 11434)
EXL2 4.0 bpw, Qwen2.5-7B    Qwen2.5-0.5B Q8 (CPU)
     │                              │
     │  fast + smart                │  router
     ▼                              ▼
 ╔═══════════════════════════════════════════╗
 ║         LiteLLM Gateway (port 4000)        ║
 ║  ┌──────────┐  ┌──────────┐  ┌──────────┐  ║
 ║  │ router   │  │ fast     │  │ smart    │  ║
 ║  │→ Ollama  │  │→ TabbyAPI│  │→ TabbyAPI│  ║
 ║  └──────────┘  └──────────┘  └──────────┘  ║
 ╚═══════════════════════════════════════════╝
```

### Why TabbyAPI Wins on the RTX 2060

| Factor | TabbyAPI (EXL2) | llama.cpp (GGUF) | Margin |
|--------|----------------|-------------------|--------|
| VRAM for 7B @ 32K ctx | ~5.0 GB (4.0 bpw) | ~5.5 GB (Q4_K_M) | TabbyAPI uses ~10% less |
| Single-batch t/s | ~50-60 | ~30-40 | TabbyAPI ~50% faster |
| Model loading time | ~2 seconds | ~8 seconds | TabbyAPI faster swap |
| Memory management | Dynamic load/unload | Static allocation | TabbyAPI more flexible |
| API compatibility | OpenAI API built in | Needs Ollama or server wrapper | TabbyAPI simpler |

### Why llama.cpp Wins on MS-S1 and M5 MAX

For the two target machines, the EXL2-only restriction of TabbyAPI/exllamav2 makes them non-viable. llama.cpp's cross-platform backend support (CUDA/ROCm/Metal/Vulkan) means a single config file works across all three machines — just change the backend flag.
