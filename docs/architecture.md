---
title: Kent — Enterprise Agentic Stack Architecture Blueprint
version: 2.3.0
date: 2026-05-17
authors:
  - Keith Nielsen <keith-nielsen@github>
status: Draft
license: Apache-2.0
changelog:
  - version: 2.3.0
    date: 2026-05-17
    summary: >
      Kent/Gent naming throughout. UUID-based stack identity with seed-from
      archive restore. Full secrets management via mounted files and systemd
      LoadCredential. Unix user/group matrix. Platform compatibility notes
      (Ubuntu, Mint, Fedora/RHEL). Storage budget. Firewall rules. Log and
      systemd hardening. Post-install human checklist. Version control header.
      Executive summary and glossary. Installation Appendix with all supporting
      scripts and config files.
  - version: 2.2.0
    date: 2026-05-16
    summary: >
      Four-tier brain architecture. Inference gateway abstraction. Directional
      access control. Knowledge sharing protocol. Egress control. Observability
      stack. Host/Docker partitioning. Installation script plan (9 phases).
  - version: 2.1.0
    date: 2026-05-16
    summary: >
      Added frontier tier (DeepSeek V4 Pro / Opus 4.7). Renamed Consultant
      to Frontier. Gateway failover config.
  - version: 2.0.0
    date: 2026-05-16
    summary: >
      Major rewrite. Hermes Agent + CrewAI integration. Three-tier inference.
      Fractal stack architecture.
---

# Kent — Enterprise Agentic Stack Architecture Blueprint

## Executive Summary

Kent is a self-hosted agentic AI system designed for a single operator ("the Human") running on local hardware. It combines a persistent helper agent (Kent), disposable project-scoped agents (Gents), tiered local/cloud inference, and enterprise-grade security and observability — all under the operator's control with data staying local by default.

The system addresses three problems: (1) most AI agent frameworks require cloud dependency and surrender data sovereignty; (2) single-model architectures waste resources on simple tasks and lack capability for hard ones; (3) project knowledge is lost when agents are torn down.

Kent solves these through a four-tier inference architecture that right-sizes brain to task (95% local, 5% cloud frontier), a fractal delegation model where project stacks are isolated but share learnings upward, and a seed-from archive mechanism that preserves domain knowledge across project lifecycles while inheriting global improvements.

The target platform is AMD Strix Halo (128GB unified RAM, 96GB VRAM) running Ubuntu 24.04 LTS, with a dev-compatible path for any Linux box with 8GB+ VRAM. The architecture is cluster-ready via gateway abstraction — adding a second node requires only a config change.

Named after the Earl of Kent in Shakespeare's *King Lear*: loyal, competent, honest, and willing to push back when the operator is wrong.

---

## Table of Contents

1. [Entity Hierarchy & Authority Model](#1-entity-hierarchy--authority-model)
2. [Four-Tier Brain Architecture](#2-four-tier-brain-architecture)
3. [Inference Gateway](#3-inference-gateway)
4. [Arch Router](#4-arch-router)
5. [Frontier Tier](#5-frontier-tier)
6. [CrewAI Integration](#6-crewai-integration)
7. [Kent & Gent Agent Integration](#7-kent--gent-agent-integration)
8. [Governance Protocol](#8-governance-protocol)
9. [Data Architecture](#9-data-architecture)
10. [Knowledge Sharing & Template Evolution](#10-knowledge-sharing--template-evolution)
11. [Stack Lifecycle & Seed-From Archive](#11-stack-lifecycle--seed-from-archive)
12. [Security Architecture](#12-security-architecture)
13. [Secrets Management](#13-secrets-management)
14. [Unix Users & Groups](#14-unix-users--groups)
15. [Egress Control](#15-egress-control)
16. [Firewall Rules](#16-firewall-rules)
17. [Observability Stack](#17-observability-stack)
18. [Log Management & Hardening](#18-log-management--hardening)
19. [Systemd Service Hardening](#19-systemd-service-hardening)
20. [Host/Docker Partitioning](#20-hostdocker-partitioning)
21. [Platform Compatibility](#21-platform-compatibility)
22. [Storage Budget](#22-storage-budget)
23. [Phase 2 Cluster Readiness](#23-phase-2-cluster-readiness)
24. [Risks & Mitigations](#24-risks--mitigations)
25. [Post-Install Human Checklist](#25-post-install-human-checklist)

Appendices:
- [A: Installation Scripts](#appendix-a-installation-scripts)
- [B: Environment File Templates](#appendix-b-environment-file-templates)
- [C: Configuration Files](#appendix-c-configuration-files)
- [D: SQL Schemas](#appendix-d-sql-schemas)
- [E: Operational Scripts](#appendix-e-operational-scripts)

---

## 1. Entity Hierarchy & Authority Model

### 1.1 Entities

```
HUMAN (enterprise)
  │  Top authority. Lives on host OS. Interacts via CLI, web UI, Gitea.
  │
  ▼
KENT (persistent agent — host OS, systemd)
  │  Runs as unix user 'kent'. Owns: kent.db, template repo, stack spawning,
  │  frontier access. Can: route, classify, call all tiers, spawn/destroy Gents.
  │
  │  "This needs its own stack" → spawns Gent from template
  │
  ▼
GENT (gent-<uuid8> — project-scoped, Docker)
  ├── CEO agent (Hermes-derived, dedicated to this stack)
  │   │  Owns: stack.db, project repo in Gitea.
  │   │  Can: call fast + smart, manage workers, share learnings upward.
  │   │  Cannot: call frontier directly, access kent.db, access other Gents.
  │   │
  │   ├── CrewAI Worker 1 (role from YAML config)
  │   ├── CrewAI Worker 2
  │   ├── CrewAI Worker 3
  │   └── CrewAI Worker 4
  │
  └── stack.db + Litestream sidecar
```

### 1.2 Directional Access Rules

| Entity | Inference Tiers | Data Access | Network Egress | Can Spawn |
|---|---|---|---|---|
| Human | All (via admin key) | All DBs | Unrestricted | Asks Kent |
| Kent | router, fast, smart, frontier | kent.db (R/W), orchestrator_core.db (R/W), all Gents' `shared_learnings` (R) | Unrestricted (host process) | Gents |
| Gent CEO | fast, smart | Own stack.db (R/W) | Via egress proxy (GET allowed, POST allowlisted) | CrewAI workers within own stack |
| CrewAI Worker | fast, smart (via CEO's key) | Own stack.db (via CEO delegation) | Via egress proxy | Nothing |

### 1.3 Frontier Escalation Path

```
Worker flags low confidence
  → CEO reviews, confirms escalation needed
    → CEO writes escalation request to shared_learnings
      → Kent picks up, calls frontier under KENT_KEY
        → Kent returns result downward to CEO
```

---

## 2. Four-Tier Brain Architecture

### 2.1 Design Principle

Every inference call passes through the cheapest viable brain. Escalation is upward-only and explicit. The local stack handles ~95% of all work; the frontier handles ~5% at high cost.

### 2.2 Tier Summary

| Tier | Model | VRAM | Speed | Use Case |
|---|---|---|---|---|
| **T0 Router** | Gemma 4 4B Q8_0 | ~4 GB | <50ms | Classify, route, tag |
| **T1 Fast** | Gemma 4 26B-A4B Q8_0 | ~8 GB | ~85 t/s | Formatting, extraction, translation, summarization |
| **T2 Smart** | Qwen3.6-27B Q6_K | ~18 GB | ~35 t/s | Planning, coding, reasoning, multi-step, review |
| **T3 Frontier** | DeepSeek V4 Pro / Opus 4.7 | Cloud | Network-bound | Escalations, hard problems, scheduled QA audits |

Always-resident local: T0 + T1 + T2 = ~30 GB. Leaves ~66 GB for KV cache.

### 2.3 Dev Environment Override

For testing on limited VRAM (e.g., 8GB NVIDIA), swap to smaller models via gateway config only:

| Tier | Production Model | Dev Standin | VRAM |
|---|---|---|---|
| Router | Gemma 4 4B Q8_0 | Gemma 3 1B Q8_0 | ~1.2 GB |
| Fast | Gemma 4 26B-A4B Q8_0 | Qwen3 1.7B Q4_K_M | ~1.5 GB |
| Smart | Qwen3.6-27B Q6_K | Qwen3 4B Q4_K_M | ~3 GB |
| **Total** | | | **~5.7 GB** |

No code changes required — the gateway resolves logical model names to physical endpoints.

### 2.4 Why Qwen3.6-27B as Smart

Gents serve diverse domains (scientific research, marketing, coding). Qwen3.6-27B is the strongest cross-domain model at this VRAM tier: GPQA Diamond 87.8, SWE-bench Verified 77.2, natively multimodal, three inference modes (Auto/Thinking/Fast), dense 27B with no MoE routing overhead.

### 2.5 VRAM Budget (Production)

| Slot | Model | Quant | VRAM |
|---|---|---|---|
| T0 Router | Gemma 4 4B | Q8_0 | ~4 GB |
| T1 Fast | Gemma 4 26B-A4B | Q8_0 | ~8 GB |
| T2 Smart | Qwen3.6-27B | Q6_K | ~18 GB |
| KV cache | — | — | ~66 GB |
| T3 Frontier | Cloud | — | 0 GB |
| **Total** | | | **~96 GB** |

---

## 3. Inference Gateway

### 3.1 Purpose

All inference consumers hit `http://localhost:4000` with a logical model name. The gateway resolves to a physical endpoint. This abstracts model location, enabling cluster migration via config change only.

### 3.2 Scoped API Keys

| Key | Holder | Allowed Models | Rate Limit |
|---|---|---|---|
| `ADMIN_KEY` | Human / CLI | all | none |
| `KENT_KEY` | Kent agent | router, fast, smart, frontier | token budget/day |
| `GENT_KEY_<id>` | Gent CEO (unique per stack) | fast, smart | token budget/task |
| `AUDIT_KEY` | Nightly QA cron | frontier only | fixed batch budget |

Keys created via LiteLLM API on first boot. Stored as mounted files in `/run/secrets/` or `/run/credentials/`, never as environment variables. 30-day rotation schedule; immediate revocation on compromise.

### 3.3 Consumer Pattern

```python
GATEWAY_URL = "http://localhost:4000"

def query_model(tier: str, system: str, user: str,
                json_mode: bool = False) -> str:
    payload = {
        "model": tier,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 8192,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = requests.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
```

All entities use this function. No direct Ollama URLs anywhere in application code.

---

## 4. Arch Router

### 4.1 Classification Schema

```python
class RouteDecision(BaseModel):
    tier: Literal["fast", "smart", "frontier"]
    review_required: bool
    confidence: float       # 0.0-1.0
    escalation_reason: str  # empty if tier != frontier
```

### 4.2 Routing Rules

| Signal | → Tier | review_required |
|---|---|---|
| Formatting, extraction, summarization, translation | T1 fast | False |
| Code generation, multi-step reasoning, planning | T2 smart | True |
| Ambiguous / confidence < 0.7 | T2 smart | True |
| External APIs / financial data / regulatory | T2 smart | True |
| Retry after T2 failure | T3 frontier | True |
| Explicitly flagged "beyond local capability" | T3 frontier | True |
| Confidence < 0.4 on any classification | T3 frontier | True |
| Novel domain with no prior task history | T2 smart (first), T3 if low confidence | True |

### 4.3 Implementation

```python
ROUTER_SYSTEM = """Classify the task. Respond ONLY with JSON:
{"tier":"fast"|"smart"|"frontier",
 "review_required":true|false,
 "confidence":0.0-1.0,
 "escalation_reason":"...or empty"}

Rules:
- Rote formatting/extraction/translation → fast, review=false
- Code, planning, reasoning, multi-step → smart, review=true
- Exceeds local capability → frontier, review=true
- Confidence < 0.7 → smart, review=true
- Confidence < 0.4 → frontier, review=true
- When unsure, default smart + review=true"""

def route(task: str) -> RouteDecision:
    raw = query_model(
        tier="router", system=ROUTER_SYSTEM,
        user=task, json_mode=True,
    )
    d = RouteDecision.model_validate_json(raw)
    if d.confidence < 0.4:
        d.tier = "frontier"
        d.review_required = True
    elif d.confidence < 0.7:
        d.tier = "smart"
        d.review_required = True
    return d
```

---

## 5. Frontier Tier

### 5.1 Two Modes of Operation

| Mode | Trigger | Latency | Budget |
|---|---|---|---|
| **On-demand escalation** | T2 flags low confidence, validation failure, or CEO escalation via Kent | Synchronous | Per-task token budget |
| **Scheduled QA audit** | Cron 02:00 UTC | Async batch (Opus Batch API, 50% savings) | Fixed daily token budget |

### 5.2 Provider Selection

| Factor | DeepSeek V4 Pro (primary) | Opus 4.7 (fallback) |
|---|---|---|
| Input price | ~$0.44/M tok | $5.00/M tok |
| Output price | ~$0.87/M tok | $25.00/M tok |
| Context window | 1M tokens | 1M tokens |
| Batch API | No | Yes (50% discount) |
| Data sovereignty | China-based API | US/Global routing |

Default: DeepSeek for cost-sensitive on-demand escalations. Opus for quality-critical or data-sensitive escalations and nightly batch QA audits.

### 5.3 Smart Model Self-Assessment

All T2 Smart calls include a system prompt suffix requiring a confidence block:

```python
SMART_SYSTEM_SUFFIX = """
After completing the task, append a JSON confidence block:
<!--CONFIDENCE:{"score":0.0-1.0,"uncertain_areas":["..."],"needs_frontier":true|false}-->
"""
```

Output gate parses this block. If `needs_frontier=true` or `score < 0.6`, escalation flows upward through Kent.

### 5.4 Estimated Cost

| Usage | Provider | Tokens/day (est.) | Daily cost |
|---|---|---|---|
| On-demand escalation (~15/day) | DeepSeek V4 Pro | ~120K | ~$0.07 |
| Nightly QA audit (20 tasks) | Opus 4.7 Batch | ~50K | ~$0.94 |
| **Total** | | | **~$1.01/day** |

---

## 6. CrewAI Integration (v1.14.3)

### 6.1 Flow with Four-Tier Routing

```python
from crewai import Agent, Crew, Task, Flow, LLM
from crewai.flow import start, listen, router

gateway_smart = LLM(model="smart", base_url="http://localhost:4000", api_key=GENT_KEY)
gateway_fast = LLM(model="fast", base_url="http://localhost:4000", api_key=GENT_KEY)

class ProjectFlow(Flow):
    @start()
    def intake(self):
        decision = route(self.state["objective"])
        self.state["tier"] = decision.tier
        self.state["review_required"] = decision.review_required

    @router(intake)
    def select_tier(self):
        if self.state["tier"] == "frontier":
            return "smart"  # Gent cannot call frontier; tries smart first
        return self.state["tier"]

    @listen("fast")
    def fast_execution(self):
        crew = Crew(
            agents=[fast_worker],
            tasks=[Task(description=self.state["objective"])],
            process="sequential",
        )
        self.state["output"] = crew.kickoff().raw

    @listen("smart")
    def smart_execution(self):
        crew = Crew(
            agents=[smart_planner, smart_coder],
            tasks=[Task(description=self.state["objective"])],
            process="hierarchical",
        )
        self.state["output"] = crew.kickoff().raw

    @listen(fast_execution, smart_execution)
    def output_gate(self):
        needs_review = self.state["review_required"] or random.random() < 0.10
        if needs_review:
            valid, critique, conf = validate_with_smart(
                self.state["objective"], self.state["output"]
            )
            if not valid or conf < 0.6:
                self.escalate_to_kent(critique)
                return
        return self.state["output"]

    def escalate_to_kent(self, reason):
        db.execute("""INSERT INTO shared_learnings
            (timestamp, category, summary, confidence, applied_locally,
             reviewed, adopted)
            VALUES (?, 'escalation_request', ?, ?, 0, 0, NULL)""",
            (now(), f"ESCALATION: {reason}", self.state.get("confidence", 0)))
```

### 6.2 Worker Role Configuration

Workers are defined in YAML, customised per project at spawn time:

```yaml
# config/agents.yaml (example: solar magnetics project)
research_analyst:
  role: "Solar Physics Research Analyst"
  goal: "Find, validate, and synthesize solar magnetic field data"
  backstory: "Expert in heliophysics literature and data interpretation"

data_processor:
  role: "Scientific Data Processor"
  goal: "Clean, transform, and validate datasets for statistical analysis"
  backstory: "Specialist in time-series analysis and correlation methods"

scientific_writer:
  role: "Scientific Paper Writer"
  goal: "Draft publication-quality prose following journal style guides"
  backstory: "Experienced academic writer with earth sciences background"

citation_checker:
  role: "Citation and Reference Validator"
  goal: "Verify all citations, cross-references, and data attributions"
  backstory: "Meticulous fact-checker with access to research databases"
```

Kent can customise these at spawn time based on the project description, or the Gent CEO can reconfigure its own crew dynamically as the project evolves.

---

## 7. Kent & Gent Agent Integration

### 7.1 Two Roles

| Role | Instance | Runs On | Persistence | Key |
|---|---|---|---|---|
| **Kent** | Singleton, always running | Host OS (systemd) | `kent.db` | `KENT_KEY` |
| **Gent CEO** | One per stack | Docker container | `stack.db` | `GENT_KEY_<id>` |

### 7.2 Kent Responsibilities

- Human conversation partner (persistent, always available)
- Arch Router invocation (classifies all inbound tasks)
- Gent spawning/destruction (from template, via Gitea + Docker)
- Frontier escalation relay (only entity with local frontier access)
- `shared_learnings` polling and cross-pollination
- Template auto-commits (high-confidence, logged to Gitea)
- Daily digest generation for Human
- Access request evaluation (presents to Human for approval)
- Nightly QA audit orchestration

### 7.3 Gent CEO Responsibilities

- Project-scoped task management via kanban
- CrewAI worker orchestration (N sub-agents from YAML config)
- Output gate validation (smart model self-review)
- Escalation requests upward to Kent
- Learning publication to `shared_learnings` table
- Project-scoped Gitea commits under own PAT

### 7.4 Hermes v0.13 Features Used

| Feature | Usage |
|---|---|
| `/goal` (Ralph loop) | Persistent goal tracking across turns |
| Kanban board | Durable task board with heartbeat, reclaim, zombie detection |
| Hallucination gate | Output validation before downstream consumption |
| Curated memory | Per-agent memory with FTS5 search |
| `notify_on_complete` | Webhook to Gitea for issue creation on circuit break |
| Redaction (default ON) | PII stripped from all logs |
| MCP OAuth 2.1 PKCE | Standards-compliant auth for external tools |
| OSV scanning | MCP extension packages scanned for malware |
| Post-write delta lint | Syntax-check agent output |
| SSRF protection | Cloud-metadata URL blocking |

---

## 8. Governance Protocol

### 8.1 Escalation Flow

```
Task arrives at Kent
  │
  ▼
[T0 Router] → tier + review_required + confidence
  │
  ├─► T1 FAST → Gemma 4 26B executes (in Gent)
  │       review_required or sampled (10%)?
  │       ├─ No → OUTPUT
  │       └─ Yes → T2 Smart reviews
  │                  ├─ PASS → OUTPUT
  │                  └─ FAIL → retry on T2 → FAIL → Gent escalates to Kent → T3
  │
  ├─► T2 SMART → Qwen3.6-27B executes (in Gent)
  │       Self-review (confidence check)
  │       ├─ PASS + high confidence → OUTPUT
  │       ├─ PASS + low confidence → Gent escalates to Kent → T3
  │       └─ FAIL → retry → FAIL → Gent escalates to Kent → T3
  │
  └─► T3 FRONTIER (Kent-mediated) → Cloud API
               ├─ RESOLVED → Kent returns to Gent → OUTPUT
               └─ UNRESOLVED → CIRCUIT BREAKER
                                 Gitea issue created
                                 Container halted
                                 CrewAI checkpoint preserved
```

### 8.2 Circuit Breaker

1. T1 fails validation → one-shot retry on T2
2. T2 fails validation → one-shot retry on T2, then escalate via Kent to T3
3. T3 fails → **CIRCUIT BREAKER**: Gitea issue, container halts, checkpoint preserved
4. Human can resume from checkpoint after fixing root cause

### 8.3 Nightly QA Audit

- Cron 02:00 UTC
- Samples 20 tasks (stratified by stack and tier)
- Sent to Opus 4.7 via Batch API (50% savings, 24h turnaround)
- Findings logged to `qa_audit_log` in `orchestrator_core.db`
- Critical findings auto-create Gitea issues
- Routing heuristic feedback: systematic misroutes trigger Router prompt tuning

---

## 9. Data Architecture

### 9.1 Database Layout

```
/home/enterprise/
├── kent/
│   └── kent.db                    ← Human + Kent ONLY
│       ├── conversations           (Human↔Kent history)
│       ├── memory                  (curated memory, FTS5)
│       ├── goals                   (/goal Ralph loop)
│       ├── stack_registry          (all Gents: active, archived, destroyed)
│       └── routing_log             (Arch Router decisions)
│
├── orchestrator_core.db            ← Gateway metrics, QA audit, billing
│       ├── inference_telemetry
│       ├── frontier_log
│       ├── qa_audit_log
│       ├── egress_telemetry
│       └── stack_telemetry
│
├── stacks/
│   ├── <uuid8>/
│   │   └── data/
│   │       └── stack.db            ← Gent CEO + Workers for this project
│   │           ├── kanban
│   │           ├── crew_checkpoints
│   │           ├── project_memory
│   │           ├── system_change_log
│   │           ├── shared_learnings  ← CEO writes, Kent reads
│   │           └── seed_context      ← imported from archived Gent
│   └── ...
```

### 9.2 Data Access Matrix

| Entity | `kent.db` | `orchestrator_core.db` | Own `stack.db` | Other Gents' `shared_learnings` | Other Gents' internal tables |
|---|---|---|---|---|---|
| Human | R/W | R/W | R/W | R | R |
| Kent | R/W | R/W | — | **R only** | **No access** |
| Gent CEO | No | No | R/W | No | No |
| CrewAI Worker | No | No | Via CEO | No | No |

### 9.3 Stack Registry (kent.db)

```sql
CREATE TABLE IF NOT EXISTS stack_registry (
    stack_id TEXT PRIMARY KEY,          -- truncated UUID (8 hex chars), immutable
    display_name TEXT NOT NULL,         -- human-readable, editable
    status TEXT NOT NULL DEFAULT 'active',  -- active, paused, archived, destroyed
    created_at TEXT NOT NULL,
    destroyed_at TEXT,
    archived_at TEXT,
    unix_user TEXT NOT NULL,            -- 'gent-<uuid8>'
    unix_uid INTEGER NOT NULL,          -- captured at creation, immutable
    gitea_repo TEXT NOT NULL,           -- 'gent-<uuid8>'
    gateway_key_hash TEXT,              -- SHA-256 of the Gent's API key
    project_description TEXT,
    template_version TEXT,              -- git commit hash of template at spawn time
    archive_path TEXT,                  -- NAS path to archived DB + artifacts
    seed_source_id TEXT                 -- references another stack_registry.stack_id
);
```

---

## 10. Knowledge Sharing & Template Evolution

### 10.1 Learning Flow

```
Gent CEO completes kanban item
  │
  ▼
CEO self-reflects: "Was anything here transferable?"
  │
  ├─ No → continue
  └─ Yes → write to shared_learnings (reviewed=0, adopted=null)
        │
        ▼
Kent polls shared_learnings (scheduled + event trigger)
  │
  ▼
Kent evaluates learning
  │
  ├─ Adopt → sets reviewed=1, adopted=1, writes review_notes
  │   ├─ Cross-pollinate → sends to relevant Gent CEOs via A2A
  │   ├─ Edit template → commits to template repo in Gitea
  │   └─ Both
  │
  └─ Discard → sets reviewed=1, adopted=0, writes review_notes
```

### 10.2 Template Management

- Stack template lives in Gitea (`stack-template` repo).
- Kent auto-commits high-confidence template changes with: source Gent, learning ID, reasoning.
- All commits logged and auditable via Gitea history.
- New Gents spawn from latest template. Existing Gents are immutable unless CEO opts in.
- Human receives daily summary of template changes. Can revert any commit via Gitea.

---

## 11. Stack Lifecycle & Seed-From Archive

### 11.1 Lifecycle States

| Action | What happens | Stack ID reused? |
|---|---|---|
| **Spawn** | New UUID, user, repo, DB, container, key | New ID always |
| **Pause** | Stop container, keep everything else | No |
| **Resume** | Start container from existing state | No |
| **Archive** | Stop container, compress to NAS, revoke key, `status=archived` | No — ID preserved |
| **Destroy** | Archive first, then delete container + user | No — ID preserved in registry |

The `stack_registry` row is never deleted. The ID remains permanently as a foreign key for telemetry, audit logs, routing history, and adopted learnings.

### 11.2 Seed-From Mechanism

When resuming work on an archived project's domain:

```bash
kent spawn-gent "Solar Magnetics Phase 2" --seed-from a3f7c291
```

This creates a completely new Gent with the latest template, then selectively imports domain-specific knowledge from the archived Gent:

| Source | Imported? | Rationale |
|---|---|---|
| Domain learnings (`applied_locally=1`, not adopted globally) | **Yes** | Unique project knowledge |
| Project memory (Hermes curated) | **Yes** | CEO's accumulated domain expertise |
| Kanban summary (synthesized by Kent) | **Yes** | What was done, what worked, what failed |
| Global learnings (`adopted=1`) | **No** | Already in the template |
| Rejected learnings (`adopted=0, reviewed=1`) | **No** | Explicitly discarded |
| Raw kanban tasks | **No** | Too granular, stale |
| CrewAI checkpoints | **No** | Tied to old container state |
| Old credentials / escalation requests | **No** | Security-sensitive, expired |

Imported content is stored in the new Gent's `seed_context` table:

```sql
CREATE TABLE IF NOT EXISTS seed_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_stack_id TEXT NOT NULL,
    source_display_name TEXT,
    source_archived_at TEXT,
    imported_at TEXT NOT NULL,
    import_type TEXT NOT NULL,  -- 'domain_learning', 'project_memory', 'kanban_summary'
    content TEXT NOT NULL,
    relevance_notes TEXT,       -- Kent's assessment of why this was imported
    stale_flag INTEGER DEFAULT 0
);
```

### 11.3 Lineage Tracking

`stack_registry.seed_source_id` creates a lineage chain:
`Phase 3 (c91d...) ← Phase 2 (b82e...) ← Phase 1 (a3f7...)`.

---

## 12. Security Architecture

### 12.1 Principles

| Principle | Implementation |
|---|---|
| Least privilege | Scoped gateway keys, per-Gent Unix UIDs, read-only rootfs, per-entity egress rules |
| Defense in depth | Gateway auth + filesystem perms + network isolation + egress proxy |
| Assume breach containment | Compromised Gent cannot: reach other Gents, read kent.db, call frontier, POST to internet |
| Audit everything | HMAC-chained append-only log, all gateway/proxy/gitea events captured |
| Rotate and revoke | 30-day key rotation, immediate revocation capability |

### 12.2 Inference Endpoint Security

- **Ollama binds to Unix socket only** — no TCP listener. Only the gateway process can reach it.
- **LiteLLM Gateway** validates API key on every request, checks key→model permission matrix, enforces per-key token budgets.
- **Docker network isolation** — Gent containers reach the gateway via bridge gateway IP. No direct Ollama access.
- **Frontier calls require `KENT_KEY` or `AUDIT_KEY`** — `GENT_KEY` gets HTTP 403 on `model: "frontier"`.

### 12.3 Container Security

```yaml
security_opt:
  - no-new-privileges
read_only: true
tmpfs:
  - /tmp:size=100M
cap_drop:
  - ALL
```

---

## 13. Secrets Management

### 13.1 Why Not Environment Variables

Environment variables leak via `/proc/<pid>/environ`, `docker inspect`, child process inheritance, crash dumps, and `ps eww`. Secrets are distributed via file mounts instead.

### 13.2 Docker Secrets (Gent Containers)

```yaml
# docker-compose.gent.yml
services:
  gent:
    secrets:
      - gent_key
      - gitea_pat
    environment:
      GATEWAY_URL: "http://host.docker.internal:4000"  # non-secret config is fine
secrets:
  gent_key:
    file: /home/enterprise/stacks/<id>/secrets/gent_key.txt
  gitea_pat:
    file: /home/enterprise/stacks/<id>/secrets/gitea_pat.txt
```

Application reads `/run/secrets/gent_key` at startup.

### 13.3 Systemd LoadCredential (Host Services)

```ini
# /etc/systemd/system/kent.service
[Service]
LoadCredential=kent_key:/home/enterprise/secrets/kent_key.txt
LoadCredential=gitea_pat:/home/enterprise/secrets/kent_gitea_pat.txt
```

Systemd copies to `/run/credentials/kent.service/kent_key` (tmpfs, 0400, owned by service user). Application reads via `$CREDENTIALS_DIRECTORY`.

### 13.4 LiteLLM Pragmatic Compromise

LiteLLM reads API keys via `os.environ/KEY_NAME` syntax. We use systemd `EnvironmentFile` pointing to a root-owned 0600 file — env vars exist only within the LiteLLM process namespace:

```ini
# /etc/systemd/system/litellm.service
[Service]
EnvironmentFile=/home/enterprise/secrets/gateway.env
```

### 13.5 Host-Side File Layout

```
/home/enterprise/secrets/                  0700 root:root
├── gateway.env                            0600 root:root
├── litellm_admin_key.txt                  0600 root:root
├── deepseek_api_key.txt                   0600 root:root
├── anthropic_api_key.txt                  0600 root:root
├── kent_key.txt                           0600 root:root
├── kent_gitea_pat.txt                     0600 root:root
└── audit_key.txt                          0600 root:root

/home/enterprise/stacks/<uuid8>/secrets/   0700 root:root
├── gent_key.txt                           0600 root:root
└── gitea_pat.txt                          0600 root:root
```

Root owns everything. Systemd `LoadCredential` and Docker `secrets:` handle runtime handoff. No service user reads originals directly.

---

## 14. Unix Users & Groups

### 14.1 System Users

| User | Group | Additional Groups | Purpose |
|---|---|---|---|
| `enterprise` | `enterprise` | `docker`, `agentic-logs` | Human operator |
| `kent` | `kent` | `ollama` | Kent agent process |
| `ollama` | `ollama` | — | Ollama inference engine |
| `litellm` | `litellm` | `ollama` | Gateway process (needs socket access) |
| `gitea` | `gitea` | — | Gitea service |
| `prometheus` | `prometheus` | — | Metrics collection |
| `loki` | `loki` | — | Log storage |
| `promtail` | `promtail` | `agentic-logs` | Log shipping (reads all logs) |
| `grafana` | `grafana` | — | Dashboard |

### 14.2 Per-Gent Users (Dynamic)

Created by `spawn-gent.sh`, destroyed by `destroy-gent.sh`:

| User | Naming | Purpose |
|---|---|---|
| `gent-<uuid8>` | e.g. `gent-a3f7c291` | Container runs as this UID. Owns stack DB directory. |

### 14.3 Functional Groups

| Group | Members | Grants |
|---|---|---|
| `agentic-logs` | `promtail`, `enterprise` | Read access to all log directories |
| `ollama` | `ollama`, `litellm`, `kent` | Read/write to Ollama Unix socket |
| `docker` | `enterprise` | Docker CLI access |

### 14.4 Directory Ownership

```
/home/enterprise/                          enterprise:enterprise  0755
├── secrets/                               root:root              0700
├── kent/
│   ├── kent.db                            kent:kent              0600
│   └── ...
├── orchestrator_core.db                   litellm:litellm        0640
├── stacks/
│   ├── a3f7c291/
│   │   ├── data/
│   │   │   └── stack.db                   gent-a3f7c291:gent-a3f7c291 0600
│   │   └── secrets/                       root:root              0700
├── logs/
│   ├── gateway/                           litellm:agentic-logs   0750
│   ├── kent/                              kent:agentic-logs      0750
│   ├── squid/                             proxy:agentic-logs     0750
│   └── hmac_chain.log                     root:agentic-logs      0640 +chattr +a
```

---

## 15. Egress Control

### 15.1 Architecture

Single Squid proxy on host. All Gent container HTTP(S) traffic routes through it.

### 15.2 Default Rules

| Target | Methods Allowed | Holder |
|---|---|---|
| Any domain | GET, HEAD | All Gent entities |
| Search engine APIs | POST | All Gent entities |
| Gateway (`localhost:4000`) | POST | All (key-scoped) |
| Gitea (`localhost:3000`) | POST, PUT | Kent + Gent CEOs (own repos via PAT) |
| Gent-specific approved endpoints | As granted | Per-entity, time-bounded |
| Everything else | **DENIED** | — |

### 15.3 Payload Anomaly Detection

For allowed POST requests, flag (log + alert, not block) if outbound payloads contain: API key patterns, Base64 blobs > 1KB, PII patterns, kent.db schema fragments, gateway keys, or GET query strings > 2KB. Start log-and-alert mode; move to hold-for-review after tuning false positives.

### 15.4 Access Request Flow

1. Gent CEO writes `access_request` to `shared_learnings`
2. Kent evaluates risk and blast radius
3. Kent presents assessment to Human
4. Human approves → Kent adds scoped proxy rule (per-entity, time-bounded, auto-expiry)
5. All grants tracked with `human_approved`, `grant_expiry`, `proxy_rule_id`

---

## 16. Firewall Rules

### 16.1 Policy

Default deny inbound. No services exposed to LAN unless explicitly configured. Outbound unrestricted at network layer (application-layer filtering via egress proxy).

### 16.2 UFW (Ubuntu / Linux Mint)

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh                          # or custom port
ufw allow from 127.0.0.1 to any port 3000   # Gitea
ufw allow from 127.0.0.1 to any port 3001   # Grafana
ufw allow from 127.0.0.1 to any port 4000   # Gateway
ufw allow from 127.0.0.1 to any port 9090   # Prometheus
ufw allow from 127.0.0.1 to any port 3100   # Loki
ufw allow from 127.0.0.1 to any port 3128   # Squid
# Optional: LAN access to Grafana only
# ufw allow from 192.168.1.0/24 to any port 3001
ufw enable
```

### 16.3 firewalld (Fedora / RHEL)

```bash
firewall-cmd --set-default-zone=drop
firewall-cmd --permanent --add-service=ssh
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="127.0.0.1" port port="3000" protocol="tcp" accept'
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="127.0.0.1" port port="3001" protocol="tcp" accept'
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="127.0.0.1" port port="4000" protocol="tcp" accept'
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="127.0.0.1" port port="9090" protocol="tcp" accept'
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="127.0.0.1" port port="3100" protocol="tcp" accept'
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="127.0.0.1" port port="3128" protocol="tcp" accept'
firewall-cmd --reload
```

---

## 17. Observability Stack

### 17.1 Components

| Component | Role | RAM |
|---|---|---|
| Prometheus | Time-series metrics | ~200MB |
| Loki | Log aggregation | ~150MB |
| Promtail | Log shipper | ~50MB |
| Grafana | Dashboard + alerting | ~150MB |
| node_exporter | Host metrics | ~20MB |
| ollama_exporter | VRAM, models, contexts | ~10MB |
| squid_exporter | Proxy metrics | ~10MB |
| SQLite exporter | Telemetry tables | ~20MB |
| **Total** | | **~610MB** |

### 17.2 Alerting Routes

- **Critical** (auth failures, HMAC chain break, circuit breaker) → Gitea issue + Human notification
- **Medium** (egress anomaly, elevated frontier spend, Gent idle) → Kent triage → Human summary
- **Info** (key rotation due, disk threshold) → Dashboard only

### 17.3 Health Checks

| Component | Method | Interval | Failure Action |
|---|---|---|---|
| Ollama | `GET /api/tags` + test inference | 30s | Alert Kent → Human if 3 consecutive |
| Gateway | `GET /health` | 15s | Systemd restart + alert |
| Per-model | Test prompt each tier | 60s | Log degraded state |
| Gent CEO heartbeat | Kanban heartbeat | 60s | Alert if 3 missed |
| Litestream lag | Generation check | 300s | Alert if > 10 minutes |
| Disk usage | `df` | 60s | Warn 70%, critical 85%, halt spawning 90% |
| VRAM usage | `rocm-smi` / `nvidia-smi` | 30s | Warn 85% |
| Host temperature | `sensors` | 60s | Throttle 85°C, shutdown 95°C |

---

## 18. Log Management & Hardening

### 18.1 Rotation Policy

| Log Source | Rotation | Hot Retention | Cold (NAS) |
|---|---|---|---|
| Gateway (LiteLLM) | 50MB per file | 30 days | 90 days compressed |
| Egress Proxy | Daily | 90 days | 1 year |
| Ollama | 25MB per file | 7 days | — |
| Kent agent | Daily | 30 days | 90 days |
| Gent agents | Daily | 30 days | — |
| CrewAI Flows | Daily | 30 days | — |
| Gitea | Weekly | 1 year | — |
| Docker containers | 10MB, max 3 files | Rolling | — |
| HMAC audit chain | **Never rotated** | Permanent | Permanent |
| System (journald) | 500MB max | 30 days | — |

### 18.2 Log Hardening

- **Dedicated `agentic-logs` group**: all log directories owned by service user, group-readable by `agentic-logs`.
- **Human (`enterprise`)** is a member of `agentic-logs` for read access.
- **Gent containers cannot read host logs** — no bind mount to host log directories.
- **HMAC chain file**: append-only via `chattr +a` (ext4/btrfs), owned by root, group-readable by `agentic-logs`.
- **Logrotate configs** per service with explicit permissions `create 0640 <user> agentic-logs`.
- **Promtail** runs as `agentic-logs` group member — can read all logs, write to none.
- **Log file permissions audited** in AIDE baseline.

### 18.3 HMAC Audit Chain

Append-only log with chained HMAC for tamper detection. Each entry: event type, timestamp, entity, action, outcome, HMAC chained with previous entry's HMAC. Daily integrity check (04:30 UTC): validate full chain. Any break = immediate Human alert.

### 18.4 Security Audit Schedule

| Check | Frequency | Tool |
|---|---|---|
| Dependency CVE scan | Daily 03:00 | `pip-audit` + Hermes OSV |
| Container image scan | On build + weekly | `trivy` |
| Proxy rule expiry | Hourly | Custom script |
| Gateway key age | Daily | Custom script |
| Gitea PAT age | Daily | Gitea API query |
| Failed auth attempts | Continuous | Gateway log monitor |
| File integrity | Daily 04:00 | AIDE |
| HMAC chain integrity | Daily 04:30 | Custom validator |

---

## 19. Systemd Service Hardening

All host-level systemd services include these directives:

```ini
[Service]
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictNamespaces=true
RestrictRealtime=true
SystemCallFilter=@system-service
```

Service-specific additions:

| Service | ReadWritePaths | Additional | Exceptions |
|---|---|---|---|
| `ollama` | `/var/lib/ollama`, `/run/ollama` | `DeviceAllow=/dev/kfd rw` (ROCm) or `/dev/dri` (NVIDIA) | `MemoryDenyWriteExecute=false` (GPU JIT) |
| `litellm` | `/home/enterprise/litellm`, `/home/enterprise/orchestrator_core.db` | — | — |
| `kent` | `/home/enterprise/kent` | `ReadOnlyPaths=/home/enterprise/stacks` | — |
| `gitea` | `/home/gitea` | — | — |
| `grafana` | `/var/lib/grafana` | — | — |
| `prometheus` | `/var/lib/prometheus` | — | — |
| `loki` | `/var/lib/loki` | — | — |
| `promtail` | — | `ReadOnlyPaths=/home/enterprise/logs` | — |
| `squid` | `/var/spool/squid`, `/var/log/squid` | — | — |

---

## 20. Host/Docker Partitioning

### 20.1 Host OS (Human's control plane)

| Component | Service Name | Rationale |
|---|---|---|
| Ollama (ROCm/CUDA) | `ollama.service` | Direct GPU access |
| LiteLLM Gateway | `litellm.service` | Central to all inference |
| Egress Proxy (Squid) | `squid.service` | Must intercept all container traffic |
| Kent | `kent.service` | Human's interface, survives Docker restarts |
| Gitea | `gitea.service` | Persistent across all Gent lifecycles |
| Grafana | `grafana-server.service` | Human's dashboard |
| Prometheus | `prometheus.service` | Metrics store |
| Loki | `loki.service` | Log aggregation |
| Promtail | `promtail.service` | Log shipping |
| node_exporter | `node-exporter.service` | Host metrics |
| AIDE | Cron | File integrity |

### 20.2 Docker (workload plane)

| Component | Container Name | Lifecycle |
|---|---|---|
| Gent (CEO + CrewAI) | `gent-<uuid8>` | Spawned by Kent, destroyed on completion |
| Litestream sidecar | `litestream-<uuid8>` | Tied to Gent lifecycle |
| SQLite→Prometheus exporter | `sqlite-exporter` | Singleton, restartable |

If Docker dies, Human + Kent keep working. If a Gent container crashes, everything else is unaffected.

---

## 21. Platform Compatibility

### 21.1 Primary Target

Ubuntu 24.04 LTS. All install scripts target `apt` and `ufw`.

### 21.2 Linux Mint

Ubuntu-based; packages are identical. Differences: Mint may ship older kernels (verify ROCm compatibility), `ufw` may need explicit install, and Mint's update manager is separate from `apt`. Install scripts work with minor adjustments flagged inline.

### 21.3 Fedora / RHEL Derivatives

Install scripts include a distro-detection function that sets package manager commands:

| Ubuntu/Mint | Fedora/RHEL |
|---|---|
| `apt install` | `dnf install` |
| `ufw` | `firewalld` / `firewall-cmd` |
| `systemctl` | `systemctl` (identical) |
| AppArmor (default) | SELinux (default) |
| `/etc/apt/sources.list.d/` | `/etc/yum.repos.d/` |

SELinux: Fedora ships with SELinux enforcing. Custom policies may be needed for Ollama Unix socket access and Docker volume mounts. Install scripts include `semanage` commands where required, with fallback to `audit2allow` for edge cases.

ROCm on Fedora: officially supported as of ROCm 6.x. Package names differ (`rocm-hip-runtime` vs Ubuntu's `rocm`). NVIDIA via RPM Fusion is well-established.

### 21.4 Distro Detection

```bash
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID" in
            ubuntu|linuxmint|pop) PKG_MGR="apt"; FW_MGR="ufw" ;;
            fedora|rhel|centos|rocky|alma) PKG_MGR="dnf"; FW_MGR="firewalld" ;;
            *) echo "Unsupported distro: $ID"; exit 1 ;;
        esac
    else
        echo "Cannot detect distro"; exit 1
    fi
}
```

---

## 22. Storage Budget

### 22.1 Estimates (90-day active use, 3 concurrent Gents)

| Category | Size | Notes |
|---|---|---|
| OS + base packages | ~5 GB | |
| Ollama binary + cache | ~1 GB | |
| Production models (3) | ~30 GB | Dev models: ~6 GB |
| Gitea + repos (90 days) | ~3 GB | Code, configs, commits |
| LiteLLM + Python venv | ~500 MB | |
| Observability binaries | ~1 GB | Grafana + Prometheus + Loki + exporters |
| Prometheus TSDB (90 days) | ~3 GB | 15s scrape, ~20 active series |
| Loki chunks (90 days) | ~4 GB | Moderate log volume |
| Docker images | ~2 GB | Base + fractal template |
| `orchestrator_core.db` | ~500 MB | Telemetry at ~1M tokens/day |
| `kent.db` | ~200 MB | Conversations, memory, goals |
| `stack.db` × 3 | ~300 MB | ~100 MB per active Gent |
| `gateway.db` | ~50 MB | LiteLLM key tracking |
| Litestream replicas (NAS) | ~1.5 GB | Compressed SQLite snapshots |
| Logs (hot, 90-day retention) | ~4 GB | Rotated, compressed |
| HMAC audit chain | ~100 MB | Append-only, permanent |
| Headroom / temp / builds | ~5 GB | |
| **Total (production)** | **~60 GB** | |
| **Total (dev, small models)** | **~35 GB** | |

### 22.2 Growth Management

SQLite text compresses well. Old conversation history in `kent.db` can be archived to NAS with `VACUUM` reclaiming space. Prometheus retention is configurable (default 90 days). Loki retention is configurable per tenant. Log rotation prevents unbounded growth.

---

## 23. Phase 2 Cluster Readiness

When the BD395i MAX (or comparable hardware) is available:

1. Smart model entry in gateway config gains a `primary` (cluster endpoint) and `fallback` (local Qwen3.6-27B, retained as warm standby).
2. Gateway health check monitors cluster node. On failure, automatic transparent fallback to local.
3. No application code changes. All consumers still call `query_model("smart", ...)`.
4. Parallel operation for N days, compare metrics, then cut over.
5. Interconnect options selected based on actual PCIe lane allocation (assessed at that time).

---

## 24. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Cloud API outage | Dual-provider failover (DeepSeek → Opus) |
| Frontier cost runaway | Hard daily token budget; `KENT_KEY` + `AUDIT_KEY` scoped; circuit breaker |
| Data leakage to cloud APIs | Mandatory redaction pipeline pre-send |
| Data exfiltration via compromised Gent | Egress proxy: POST denied by default, GET anomaly detection, WebSocket blocked |
| Qwen3.6-27B quality on edge cases | Frontier catches misses; nightly QA finds systematic gaps |
| Router misclassification | 10% random sampling + nightly audit; `routing_log` tracks escalation rate |
| Gent lateral movement | Docker isolation + filesystem permissions + per-Gent credentials |
| Log tampering | HMAC chained audit log + AIDE file integrity |
| Hermes rapid release cadence | Pin version; test upgrades on staging Gent first |
| Kent bottleneck | Load analysis: ~200K-350K tokens/day for 3 Gents is manageable at 35 t/s |
| Secret leakage | Mounted files (not env vars); per-Gent derived keys; 30-day rotation |
| Stale access grants | Auto-expiry on all grants; hourly check; monthly full review |

---

## 25. Post-Install Human Checklist

After completing all installation phases, verify manually:

1. **Grafana**: Open `http://localhost:3001` → all panels populated, no errors, all datasources green.
2. **Kent conversation**: Run `kent chat` → ask a question → confirm response arrives via gateway.
3. **Gent spawn**: Ask Kent to spawn a test Gent → confirm container appears in `docker ps`.
4. **Gitea**: Open `http://localhost:3000` → confirm template repo exists, Gent project repo created.
5. **Egress blocking**: From inside a Gent container, attempt `curl -X POST https://httpbin.org/post` → confirm blocked (403).
6. **Proxy logs**: Check `/var/log/squid/` → confirm both allowed GET and denied POST entries visible.
7. **AIDE baseline**: Run `aide --check` → confirm clean (no unexpected changes since baseline).
8. **Cron jobs**: Run `crontab -l` → confirm QA audit (02:00) and daily digest (07:00) present.
9. **Daily digest**: Run `daily-digest.sh` manually → read output, confirm sensible.
10. **NAS backup**: Verify NAS mount is accessible and Litestream replicas are landing.
11. **Frontier connectivity**: Confirm Kent can reach DeepSeek and Anthropic APIs (tested in Phase 3).
12. **Key scoping**: Confirm a `GENT_KEY` gets 403 on `model: "frontier"` (tested in Phase 3).
13. **HMAC chain**: Run `validate-hmac-chain.sh` → confirm chain integrity.
14. **Gent destruction**: Destroy the test Gent → confirm container removed, user deleted, registry updated.
