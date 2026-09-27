---
title: Kent — Agentic Stack Architecture
version: 3.0.0
date: 2026-09-28
authors:
  - Keith Nielsen <keith-nielsen@github>
status: Release candidate (v0.1.0-rc) — describes the installed, conformance-tested system
license: Apache-2.0
changelog:
  - version: 3.0.0
    date: 2026-09-28
    summary: >
      Rewritten to describe the live harness (v0.1.0-rc). Ollama replaced by an
      operator-run llama.cpp server; Claude Opus 5.5 for smart/frontier (no
      DeepSeek); a single identity-authenticated LiteLLM gateway with
      classify-then-route; Gents local-only; Kent as a Hermes profile running as
      the operator; one system account per service; manifest-driven install
      modules with verified rollback; Grafana Alloy instead of Promtail; own Squid
      instance for Gent egress; circuit breaker, pause/resume and archive-first
      lifecycle. Unbuilt items are marked Planned. Section numbers kept from 2.3.0.
  - version: 2.3.0
    date: 2026-05-17
    summary: >
      Kent/Gent naming, UUID stack identity, seed-from archive, secrets via
      mounted files, user/group matrix, platform notes, storage budget.
  - version: 2.0.0 – 2.2.0
    date: 2026-05-16
    summary: >
      Hermes + CrewAI integration, tiered inference, gateway abstraction,
      directional access control, knowledge sharing, egress control, observability.
---

# Kent — Agentic Stack Architecture

## Executive Summary

Kent is a self-hosted agentic AI system for a single operator ("the Human") on
their own hardware. A persistent agent (**Kent**) talks with the operator,
supervises the estate and delegates project work to disposable, isolated project
teams (**Gents**: a CEO loop driving CrewAI workers in a locked-down container).
Every model call from every entity goes through one gateway, which first asks a
small local router model how hard the request is and then sends it to the
matching tier: the local model for routine work, Claude Opus 5.5 for smart and
frontier work, with automatic fallback to the local model when the cloud fails.

Gents only ever use the local model. When they need more, they escalate to Kent,
who answers on the frontier tier. Gents publish what they learned; Kent judges
each learning and commits the valuable ones to a shared template, so every future
Gent starts smarter. Everything is observable (Prometheus, Loki, Grafana),
audited (HMAC-chained log anchored in the journal) and reversible (each component
is an install module whose manifest drives an exact uninstall).

**Purpose: a reference template.** Kent is meant to show what a well-designed,
fully integrated agentic stack looks like when it has to satisfy enterprise
stability, supportability, control and audit requirements, the kind demanded
for PII-bearing data or banking/medical-class operations. Minimum privilege
everywhere, every action logged and measured, industry-standard use of Hermes,
local models and frontier models, and formal, repeatable validation. What is in
place and what a regulated deployment would still need is tracked control by
control in [`controls.md`](controls.md); current results in
[`conformance.md`](conformance.md). **This release candidate is a reference
implementation, not a certified system.**

**Local-first is the end-state, not today's balance.** Cloud intelligence does the
heavy lifting while local models mature; the gateway makes shifting that balance
a configuration change.

Named after the Earl of Kent in Shakespeare's *King Lear*: loyal, competent,
honest, and willing to tell the operator when they are wrong.

Status markers used below: **Live** = installed and covered by the conformance
check (`docs/conformance.md`); **Planned** = designed, not built yet.

---

## Table of Contents

1. [Entity Hierarchy & Authority Model](#1-entity-hierarchy--authority-model)
2. [Tiered Brain Architecture](#2-tiered-brain-architecture)
3. [Inference Gateway](#3-inference-gateway)
4. [Router](#4-router)
5. [Frontier Tier & Fallback](#5-frontier-tier--fallback)
6. [CrewAI Integration](#6-crewai-integration)
7. [Kent & Gent Agent Integration](#7-kent--gent-agent-integration)
8. [Governance Protocol](#8-governance-protocol)
9. [Data Architecture](#9-data-architecture)
10. [Knowledge Sharing & Template Evolution](#10-knowledge-sharing--template-evolution)
11. [Stack Lifecycle](#11-stack-lifecycle)
12. [Security Architecture](#12-security-architecture)
13. [Secrets Management](#13-secrets-management)
14. [Unix Users & Groups](#14-unix-users--groups)
15. [Egress Control](#15-egress-control)
16. [Firewall](#16-firewall)
17. [Observability & Alerting](#17-observability--alerting)
18. [Logs & Audit Chain](#18-logs--audit-chain)
19. [systemd Hardening](#19-systemd-hardening)
20. [Host / Docker Partitioning](#20-host--docker-partitioning)
21. [Platform Compatibility](#21-platform-compatibility)
22. [Storage & Resources](#22-storage--resources)
23. [Scaling Path](#23-scaling-path)
24. [Risks & Mitigations](#24-risks--mitigations)
25. [Post-Install Checklist](#25-post-install-checklist)

---

## 1. Entity Hierarchy & Authority Model

### 1.1 Entities

```
HUMAN (operator)
  │  Top authority. Talks to Kent (CLI via Hermes), reads Grafana and Gitea,
  │  approves anything outside the defaults.
  ▼
KENT (persistent agent — the operator's Hermes "kent" profile)
  │  Runs as the operator. Owns kent.db, the stack template, Gent spawning,
  │  frontier access. Calls every tier. Supervises, judges, assesses.
  ▼
GENT (gent-<id8> — one per project, Docker, own Unix user)
  ├── CEO loop (templates/app/gent/main.py): kanban, escalation, validation
  └── CrewAI workers (roles from the project's agents.yaml)
      Local tiers only. Own stack.db. Reads the web through the egress proxy.
```

### 1.2 Directional Access Rules — Live

| Entity | Inference tiers | Data access | Network | Can spawn |
|---|---|---|---|---|
| Human (`operator` key) | all | everything | unrestricted | asks Kent (or runs `kent-gent`) |
| Kent (`kent` key) | router, fast, smart, frontier, auto | kent.db (R/W); every Gent's stack.db (**read-only**) | unrestricted (host process) | Gents |
| Gent CEO + workers (`gent-<id>` key) | router, fast | own stack.db (R/W) | gateway + egress proxy only | nothing |
| Prometheus (`metrics` key) | none | gateway `/metrics/` only | — | — |

Enforced by `kent_gateway.py` (§3) and by filesystem ownership (§9, §14).

### 1.3 Frontier Escalation Path — Live

```
CEO decides a task needs more than the local model
  (task flagged escalate: true, router judges it expert-level, or validation failed after one retry)
  → CEO writes an escalation_request row to its shared_learnings and blocks the task
    (later tasks in tasks.yaml order wait behind it)
      → kent-poll-learnings (every minute) calls frontier with Kent's key
        → answer written to the Gent's inbox (mounted read-only)
          → CEO resumes the task with the expert guidance in context
```

---

## 2. Tiered Brain Architecture

### 2.1 Design Principle

Every request is classified and sent to the cheapest tier that can do it well.
Escalation is upward and explicit. Today the cloud tiers carry the hard work;
the ratio moves toward local as local models improve.

### 2.2 Tiers — Live

| Logical name | Prod target | Dev / current reference machine | Used for |
|---|---|---|---|
| `router` | local llama.cpp model | same local model | classifying each `auto` request |
| `fast` | local llama.cpp model | Qwen3.6-35B-A3B via `llama-server` on 127.0.0.1:8080 | routine, mechanical work; all Gent work |
| `smart` | Claude Opus 5.5 | local model (dev config) | ordinary reasoning and coding |
| `frontier` | Claude Opus 5.5 | local model (dev config) | hard problems, escalations, assessments, QA |
| `auto` | router → fast / smart / frontier | same | the default for Kent and the operator |

smart and frontier point at the same model today; they will be differentiated later.

### 2.3 Gateway Configurations

| Config | File | smart / frontier go to | Use |
|---|---|---|---|
| prod | `configs/litellm_config.yaml` | `anthropic/claude-opus-5-5` | normal operation (needs an Anthropic key) |
| dev | `configs/litellm_config.dev.yaml` | the local model | local-only testing |
| sim | `configs/litellm_config.sim.yaml` | the oracle fixture (`tests/sim/oracle.py`) | end-to-end simulations without a key |

Select with `install/services/litellm/install.sh --config dev|prod|sim`. No
application code changes: every consumer uses logical names.

### 2.4 Local Model

The local model is a `llama-server` (llama.cpp) the operator starts
(e.g. `~/.local/bin/llamaserver-qwen36-optimum.sh`), serving one model under the
alias `locally-run-model` at `http://127.0.0.1:8080/v1`. It is not yet a managed
service (Planned: own account and unit, like the other services).

### 2.5 Reference Hardware

| | Reference machine (dev) | Target |
|---|---|---|
| GPU | RTX 2060 SUPER, 8 GB | Strix Halo class, 96+ GB unified memory |
| RAM | 64 GB | 128 GB |
| Implication | one local model serves router+fast; ~17–35 tok/s; keep Gent tasks small | separate router/fast models; more work local |

---

## 3. Inference Gateway — Live

### 3.1 Purpose

All inference goes to LiteLLM 1.100.1 at `http://127.0.0.1:4000/v1` with a
logical model name. One gateway means one place to see, meter and control every
call (gateway logs → Loki, metrics → Prometheus).

### 3.2 Identity-Based Keys

There is no LiteLLM database and no virtual keys. `kent_gateway.py`
(`install/services/litellm/`) is LiteLLM's `custom_auth`: it maps each key to an
identity and enforces a policy table.

| Identity | Key source | Models | Routes |
|---|---|---|---|
| `operator` | systemd credential `operator_key` | all | all |
| `kent` | systemd credential `kent_key` | all | all |
| `gent` | systemd credential `gent_key` (tests) | router, fast | chat/completions, models |
| `gent-<id>` | `/etc/kent/litellm/gent-keys/<id>.key` (written at spawn, hot-reloaded) | router, fast | chat/completions, models |
| `metrics` | systemd credential `metrics_key` | none | `/metrics/` |

Restricted identities get a deny-by-default request shape: no query string, no
`x-litellm-model` header, only standard OpenAI chat fields, GET/HEAD/POST only.
(LiteLLM reads the target model from many places; a test showed `?model=frontier`
reaching a cloud tier before this rule.) Both LiteLLM switches
`custom_auth_run_common_checks` and `enable_post_custom_auth_checks` are set, and
`kent_gateway.py` enforces the model list itself as well. Keys are compared in
constant time. Every call and every denial is logged as JSON
(`kent_event=llm_call|access_denied`, with identity, tier and routing decision).

### 3.3 Consumer Pattern

```python
requests.post("http://127.0.0.1:4000/v1/chat/completions",
              headers={"Authorization": f"Bearer {key}"},
              json={"model": "auto", "messages": [...]})
```

Kent (Hermes profile `kent`) uses provider `custom`, base URL
`http://127.0.0.1:4000/v1`, model `auto`. Gents reach the gateway at
`http://172.30.0.1:4000/v1` through a socket bridge (§15).

---

## 4. Router — Live

`auto` is LiteLLM's `complexity_router` with an LLM classifier on the `router`
tier. Tier definitions:

| Tier | Definition (abridged) |
|---|---|
| fast | routine, mechanical requests with an obvious answer: formatting, extraction, short lookups, single well-specified commands |
| smart | ordinary engineering and knowledge work: writing/editing code, scoped debugging, explanations needing judgement |
| frontier | hard or high-stakes work: open-ended design, subtle debugging, novel problems, long reasoning |

An unclassifiable request goes to frontier ("when in doubt, escalate");
classification times out after 15 s. Every decision is logged
(`routed_tier`, `routing_cause`) and shown in the digest and on Grafana.

Measured (live probe, `tests/live/router_probe.py`): easy prompts → fast 8/8;
hard prompts → smart/frontier 8/8; prompt injections telling the router to
downgrade or upgrade changed no decision.

---

## 5. Frontier Tier & Fallback

### 5.1 Uses — Live

- **On demand:** Kent's own hard requests (via `auto`), Gent escalation answers,
  Gent assessments (`kent-gent assess`).
- **Judgement:** learning reviews run on smart.
- **Scheduled:** nightly QA audit at 02:00 (up to 20 sampled completed tasks).

### 5.2 Provider

Claude Opus 5.5 (`claude-opus-5-5`) through LiteLLM's native Anthropic provider.
The key is a systemd credential (`anthropic_api_key`), set with
`install/services/litellm/install.sh --set-anthropic-key`. **Pending:** no key is
configured on the reference machine yet, so smart/frontier fall back to local.

### 5.3 Fallback — Live

If a smart, frontier or auto call fails, the gateway answers from the local `fast`
tier. `retry_policy` retries only where it helps (rate limits, provider 5xx) so
retries don't multiply the wait. How long a cloud call may take before falling
back is one variable, `FALLBACK_TIMEOUT` (`/etc/kent/litellm/litellm.env`;
`install.sh --set-fallback-timeout N`). **Set it to 120 once a key is
configured**; short values are for testing only (see the warning at the top of
`install/services/litellm/install.sh`).

### 5.4 Cost Controls

Only Kent and the operator can reach cloud tiers. Gent-driven frontier spend is
capped at 5 escalations per Gent per 24 h. Planned: per-key token budgets.

---

## 6. CrewAI Integration — Live

CrewAI 1.15.22 runs inside the Gent image (hash-locked, no LiteLLM package),
using CrewAI's native OpenAI provider pointed at the gateway's `fast` tier with
the Gent's own key. Telemetry is off (`OTEL_SDK_DISABLED`, `CREWAI_TRACING_ENABLED=false`).

Each kanban task becomes a one-task crew with the agent named in `tasks.yaml`.
The agent's backstory gets the inherited team knowledge (`LEARNINGS.md`, §10).
Tools (`templates/app/gent/tools.py`): Web Search and Read Web Page (through the
egress proxy), Read/Write/List Files and Run Script (confined to
`/data/workspace`). Effort is capped per project in `project.yaml`
(`limits: {max_iter, max_tokens}`; defaults 6 / 1500) so the local model stays responsive.

Project format (written by Kent, or by `skills/kent/crew-designer/scripts/generate_crew.py`):

```yaml
# project.yaml
name: "kernel-check"
goal: "What is delivered and for whom"
limits: {max_iter: 6, max_tokens: 1500}
# agents.yaml
developer: {role: "...", goal: "...", backstory: "..."}
# tasks.yaml (order = dependency order)
build:
  agent: developer
  description: "Concrete work; name the files to write"
  expected_output: "What done looks like"
  escalate: true                 # optional: ask Kent first
  question: "The expert question"   # optional
```

---

## 7. Kent & Gent Agent Integration

### 7.1 Two Roles

| Role | Instance | Runs as / on | Persistence | Key |
|---|---|---|---|---|
| Kent | singleton | operator, host (Hermes profile `kent` + user timers) | `~/.local/share/kent/kent.db` | `kent` |
| Gent CEO | one per project | `gent-<id>`, Docker container `kent-gent-<id>` | `/var/lib/kent-gent/stacks/<id>/data/stack.db` | `gent-<id>` |

### 7.2 Kent Responsibilities — Live

| Duty | How |
|---|---|
| Conversation partner | `kent` (= `hermes -p kent`); SOUL in `templates/app/SOUL.md`; `crew-designer` skill |
| Classification of all requests | model `auto` at the gateway |
| Gent spawn / supervise / retire | `kent-gent spawn|list|status|logs|wait|pause|resume|restart|destroy` |
| Escalation relay, learning review, circuit breaker | `kent-poll-learnings` (every minute) |
| Assessment of finished work | `kent-gent assess` (frontier) → `gent_assessments`, workspace published to Gitea |
| Template commits | confident adoptions appended to `LEARNINGS.md` in `kent/stack-template` |
| Daily digest | `kent-digest` (07:00) → `~/.local/share/kent/digests/` |
| Nightly QA | `kent-qa-audit` (02:00) → `qa_audit_log` |
| Audit | `kent-audit` (chain), ingest every 5 min, anchor 04:30 |

Planned: access-request evaluation (§15.4), seed-from (§11.2).

### 7.3 Gent CEO Responsibilities — Live

- Seed the kanban from `tasks.yaml`; run tasks in order; requeue interrupted work at start.
- Decide escalation (flag or router judgement); block and resume through the inbox.
- Validate each result on `fast` against the files actually written; one retry, then escalate.
- Heartbeat on every agent step.
- On completion: `REPORT.md`, 1–3 transferable learnings, exit.
- With failed tasks: halt (don't finalise) and wait for Kent/operator.

The CEO has no Gitea access; Kent publishes the workspace to `kent/gent-<id>`.

### 7.4 Hermes

Hermes Agent (v0.21.x) is a prerequisite installed and configured by the
operator. Kent is its `kent` profile; the Kent installer adds SOUL.md, the
`kent/crew-designer` skill and the gateway wiring, and moves the previous files
aside for restore. Kent needs no venv of its own and never touches the
operator's other Python environments.

---

## 8. Governance Protocol

### 8.1 Escalation Flow — Live

```
Task claimed by the Gent CEO
  ├─ flagged / router says expert-level ─► escalate to Kent ─► frontier answer ─► resume
  └─ crew runs on fast ─► validation on fast
        ├─ PASS ─► done
        └─ FAIL ─► one retry ─► FAIL ─► escalate to Kent ─► frontier answer ─► resume
                                         (budget: 5 answers per Gent per 24 h)
```

### 8.2 Circuit Breaker — Live

Checked every minute by `kent-poll-learnings`. A Gent is halted when:

- a task failed (errors on the retry too), or
- one task was escalated 3 times without resolution, or
- its escalation budget is exhausted while escalations are pending.

Halting stops the container (state kept), sets the registry to `paused`, opens a
Gitea issue on `kent/gent-<id>` with the reasons, and writes an audit event. After
the cause is fixed, `kent-gent resume <id>` drops a resume token in the inbox;
the CEO retries its failed tasks once per token.

### 8.3 Nightly QA Audit — Live

02:00: up to 20 completed tasks sampled across Gents, reviewed on frontier,
recorded in `qa_audit_log`. Planned: critical findings open Gitea issues;
systematic misroutes feed router tuning.

---

## 9. Data Architecture — Live

### 9.1 Layout

```
~/.local/share/kent/              (operator, 0700)
├── kent.db                       Kent's estate DB (schemas/kent.sql)
├── audit/hmac_chain.log          audit chain
└── digests/YYYY-MM-DD.md
~/.config/kent/                   (operator, 0700; files 0600)
├── kent.conf                     paths and URLs for Kent's tools
└── litellm_{operator,kent}_key, gitea_kent_token, gitea_admin_password,
    grafana_admin_password, audit_hmac_secret
/var/lib/kent-gent/
├── stacks/<id>/data/             gent-<id>:<operator group> 2750 (setgid)
│   ├── stack.db                  schemas/stack.sql
│   ├── project/                  project.yaml, agents.yaml, tasks.yaml, LEARNINGS.md
│   └── workspace/                deliverables, outputs/, REPORT.md
├── stacks/<id>/inbox/            <operator>:gent-<id> 0750 — Kent's answers, resume tokens
├── keys/<id>.key                 root:gent-<id> 0440 (mounted read-only)
├── registry/<id>                 root 0700 — what kent-destroy-gent may remove
└── archive/<id>/                 root:<operator group>, read-only copies of retired Gents
```

### 9.2 Access Matrix

| | kent.db | Gent stack.db / workspace | Gent inbox |
|---|---|---|---|
| Human | R/W | R | R/W |
| Kent (operator) | R/W | **R only** (group read, SQLite `mode=ro`) | W (answers, tokens) |
| Gent | none | R/W (own only) | R (mounted read-only) |

### 9.3 Main Tables

`kent.db`: `stack_registry` (every Gent ever; rows never deleted), `learning_reviews`
(Kent's verdict per learning — Kent never writes Gent databases),
`gent_assessments`, `qa_audit_log`, `routing_log`, `memory`, `goals`,
`conversations`, telemetry tables. `stack.db`: `kanban`, `shared_learnings`,
`crew_checkpoints`, `project_memory`, `system_change_log`, `seed_context`.

---

## 10. Knowledge Sharing & Template Evolution — Live

```
Gent finishes ─► publishes 1–3 learnings (technique / pitfall / pattern) in shared_learnings
  ─► kent-poll-learnings: smart-tier judge (learning text treated as untrusted data)
       ├─ discard ─► learning_reviews (verdict + reason)
       └─ adopt, confidence ≥ 0.7 ─► content filter ─► commit to LEARNINGS.md in
            kent/stack-template ("learning: adopt gent-<id>#<n>") ─► audit event
  ─► every new Gent copies LEARNINGS.md into its project and agent backstories
```

The content filter blocks commits that look like instructions to a model,
shell substitutions or pipe-to-shell, URLs, credentials or encoded blobs, even if
the judge adopted them: an adopted learning reaches every future Gent, so a
poisoned one must not get in. The digest lists each day's template commits; any
commit can be reverted in Gitea.

Planned: pushing learnings to already-running Gents; giving the judge its past
verdicts to avoid re-reviewing duplicates.

---

## 11. Stack Lifecycle

### 11.1 States — Live

| Action | Command | Effect |
|---|---|---|
| Spawn | `kent-gent spawn --name N --project DIR` | new id, user, dirs, key, Gitea repo, container; refused at ≥ 90% disk |
| Pause / resume | `kent-gent pause|resume ID` | stop/start container, state kept; resume retries failed tasks |
| Restart | `kent-gent restart ID` | recreate the container on the current image, state kept |
| Destroy | `kent-gent destroy ID` | revoke key, remove container and account; **data archived** read-only (`--purge` deletes) |

The registry row is never deleted; the Gitea repo `kent/gent-<id>` stays as the permanent record.

### 11.2 Seed-From — Planned

Spawning a new Gent that imports domain knowledge (unadopted local learnings,
project memory, a Kent-written kanban summary) from an archived one into
`seed_context`, with lineage in `stack_registry.seed_source_id`. Schema exists;
tooling not built.

---

## 12. Security Architecture — Live

### 12.1 Principles

| Principle | Implementation |
|---|---|
| Least privilege | identity keys per entity and per Gent; own account per service and per Gent; read-only container rootfs; Gents can't reach cloud tiers |
| Defence in depth | gateway policy + LiteLLM checks, filesystem ownership, internal network, egress proxy, content filters |
| Contain a compromised Gent | it cannot reach other Gents, kent.db, cloud tiers, host services (except gateway and proxy), the LAN, or POST to the internet |
| Audit everything | HMAC chain + journal anchors; gateway calls/denials, installs and sudo commands ingested |
| Reversible | every install recorded in a manifest; verified rollback to the pre-install state |

### 12.2 Gateway

No key or a forged key → 401; a Gent key on smart/frontier, admin routes, a
query-string model or a routing header → 403. Covered by 145 adversarial unit
tests and 46 live tests with a canary upstream.

### 12.3 Containers

`docker run` as `gent-<id>`, `--read-only`, tmpfs `/tmp`, `--cap-drop ALL`,
`no-new-privileges`, pids/memory/CPU limits, `--init`, journald logging, internal
network `kent-gent-net` only. Image: pinned base digest, hash-locked dependencies.
In-container self-test: 24 checks (workspace confinement, symlink escape, script
confinement, proxy policy).

Planned: key rotation, container image scanning.

---

## 13. Secrets Management — Live

- Service secrets live in `/etc/kent/<service>/credentials/` (root-owned) and
  reach processes through systemd `LoadCredential` (`$CREDENTIALS_DIRECTORY`),
  or Gitea's `*_URI` file settings; never environment variables or config files.
- The Anthropic key is exported only inside the gateway process by its start
  wrapper (LiteLLM's provider reads the environment).
- Operator copies needed by Kent's tools are in `~/.config/kent/` (0600).
- A Gent's key is a read-only bind mount at `/run/kent/gent_key`; its hash is in the registry.
- Nothing secret is committed; `.gitignore` excludes secrets and local settings.

---

## 14. Unix Users & Groups — Live

| Account | Created by | Runs |
|---|---|---|
| operator (e.g. `administrator`) | — | Kent (Hermes), Kent's user timers, llama-server |
| `litellm` | litellm module | gateway |
| `prometheus`, `node_exporter`, `loki` | their modules | metrics and logs |
| `alloy` | Alloy apt package (installed by Kent) | log shipping |
| `grafana` | Grafana package (pre-existing) | dashboards |
| `gitea` | gitea module | forge |
| `kent-squid` | gent module | Gent egress proxy |
| `gent-<id>` | `kent-spawn-gent`, removed by `kent-destroy-gent` | one Gent container |
| DynamicUser | systemd | the two Gent bridge socket proxies |

No shared account. The operator runs exactly two root tools without a password
(`/etc/sudoers.d/91-kent-gent`: `kent-spawn-gent`, `kent-destroy-gent`), both
root-owned and argument-validated. Full detail: `docs/privilege-map.md`.

---

## 15. Egress Control

### 15.1 Architecture — Live

```
Gent container ── kent-gent-net (internal, 172.30.0.0/24, bridge kent-gent0; no route out)
   ├─► 172.30.0.1:4000 ── kent-gent-gateway.socket ─► 127.0.0.1:4000 (gateway)
   └─► 172.30.0.1:3129 ── kent-gent-egress.socket  ─► 127.0.0.1:3129 (kent-squid) ─► internet
```

The bridges are systemd socket proxies (DynamicUser). `kent-squid` is Kent's own
Squid instance (own account and config); the vendor `squid.service` is untouched.

### 15.2 Rules — Live

| Target | Allowed |
|---|---|
| public web | GET, HEAD over http/https; CONNECT only to 443 |
| private, loopback, link-local, CGN, multicast addresses; `.localhost/.local/.internal/.lan/.home.arpa` | denied |
| any other method | denied |
| gateway | via its own bridge, key-scoped |
| Gitea, Grafana, Prometheus, Loki, LAN | unreachable |

### 15.3–15.4 POST Allowlist, Anomaly Detection, Access Requests — Planned

Per-Gent, time-bounded proxy grants approved by the Human through Kent; outbound
payload anomaly logging.

---

## 16. Firewall

Deferred by decision: the host firewall will be hardened later. Kent adds only
two UFW rules when UFW is active, allowing inbound on `kent-gent0` to
`172.30.0.1` ports 3129 and 4000 (recorded, removed on uninstall). Every Kent
service listens on 127.0.0.1 (or the Gent bridge address); the conformance
check verifies nothing is exposed beyond that.

---

## 17. Observability & Alerting — Live

| Component | Version | Listens | Notes |
|---|---|---|---|
| Prometheus | 3.15.0 | 127.0.0.1:9090 | 30 d / 5 GB retention; scrapes prometheus, node, litellm (`metrics` key), loki, alloy, grafana, gitea |
| node_exporter | 1.12.1 | 127.0.0.1:9100 | host metrics |
| Loki | 3.7.8 | 127.0.0.1:3100 | tsdb v13, 30 d retention |
| Grafana Alloy | 1.20.0 (apt) | 127.0.0.1:12345 | journald → Loki; labels `unit`, `identity`, `kent_event` |
| Grafana | 13.1.0 (vendor package) | 127.0.0.1:3001 | provisioned datasources; "Kent overview" dashboard (calls by identity, denials, router decisions, targets, CPU, memory, gateway log); no anonymous access |

Gent containers log to journald (`kent-gent-<id>`), so they reach Loki too.

Alerting: critical events (circuit breaker, audit-chain break) open Gitea issues
and write crit-level journal entries; the daily digest summarises gateway use,
router decisions, denials, targets, failed units, chain status, Gents, learning
reviews, assessments, template changes, alerts and stale Gent tasks (no
heartbeat for 15 min). Planned: Grafana alert rules and a notification channel.

---

## 18. Logs & Audit Chain

### 18.1 Retention — Live

Loki 30 days, Prometheus 30 days / 5 GB, journald system defaults. The audit chain is never rotated.

### 18.2 Audit Chain — Live

`~/.local/share/kent/audit/hmac_chain.log`: each entry
`timestamp|entity|event|detail|HMAC-SHA256(secret, previous_hmac|entry)`,
appended under a file lock with fsync. `kent-audit anchor` (daily 04:30 and at
every install) verifies the chain and writes `chain=<id> count=N last=<hmac>` to
the journal, so truncation or a rewrite is detected even though the file is
operator-owned. Each chain has its own id; a reinstall starts a new chain, and
verification reports the earlier ones. A failed scheduled check opens a Gitea issue.

`kent-audit-ingest` (every 5 min) pulls gateway denials, `kent-install` events
and sudo commands from Loki into the chain.

### 18.3 Security Audit Schedule

| Check | Status |
|---|---|
| Audit chain verify + anchor, daily 04:30 | Live |
| Gateway denials into the chain, every 5 min | Live |
| AIDE, pip-audit, trivy, key/PAT age | Planned (deferred hardening) |

---

## 19. systemd Hardening — Live

Every Kent unit uses: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
`PrivateTmp`, `PrivateDevices`, kernel/cgroup/clock/hostname protections,
`RestrictNamespaces`, `RestrictAddressFamilies`, `LockPersonality`,
`MemoryDenyWriteExecute`, an empty capability set, `SystemCallFilter=@system-service`
minus `@privileged`, `UMask=0077`, `StateDirectory`/`LogsDirectory` for writable state,
and credentials via `LoadCredential`. Vendor units (Alloy, Grafana) get the same
through drop-ins. Documented exceptions: node_exporter needs `adjtimex` and no
`ProtectClock` (timex collector); Squid needs its own `setuid`-family calls and
`capset`, which are harmless without capabilities.

`systemd-analyze security` exposure: litellm 1.5, prometheus 1.5, node_exporter
2.0, loki 1.5, alloy 1.7, grafana 2.9, gitea 1.5, kent-squid 1.5, bridges 1.2.

Planned: systemd-oomd `ManagedOOM*` policies for the heavy services.

---

## 20. Host / Docker Partitioning — Live

| Host (systemd) | Docker |
|---|---|
| `kent-litellm`, `kent-prometheus`, `kent-node-exporter`, `kent-loki`, `alloy`, `grafana-server`, `kent-gitea`, `kent-squid`, `kent-gent-{egress,gateway}.socket`; Kent's user timers; llama-server (operator) | `kent-gent-<id>` containers only |

If Docker stops, Kent and all services keep working; a crashed Gent affects nothing else.

---

## 21. Platform Compatibility

Tested: Ubuntu 24.04 LTS (systemd 255, Docker 29, Python 3.12). The modules use
`apt` for packaged components (Alloy, Squid) and upstream release tarballs
verified against pinned SHA-256 values (`install/services/versions.env`) for the
rest. Other distributions are untested; Fedora/RHEL would need `dnf`, firewalld
and SELinux policy for the Gent bind mounts.

---

## 22. Storage & Resources

| Item | Approx. size |
|---|---|
| Gateway venv (`/opt/kent-litellm`) | ~0.5 GB |
| Gent image (`kent-gent`) | ~1.4 GB |
| Prometheus TSDB | ≤ 5 GB (capped) |
| Loki chunks (30 d) | 1–4 GB |
| Gitea, kent.db, archives | small; grows with Gents |
| Local models | operator's model directory (not managed by Kent) |

Spawning is refused at ≥ 90% disk. Recommended host settings (not applied by the
installer): swap ≥ 16 GB, `vm.swappiness=10`; schedule `fstrim` for idle hours on
SSDs that stall writes during TRIM.

---

## 23. Scaling Path

1. Add the Anthropic key; tune `FALLBACK_TIMEOUT`.
2. Differentiate smart and frontier (e.g. a cheaper Claude model for smart).
3. On bigger hardware, serve router/fast/smart from separate local models; shift
   tiers from cloud to local by editing the gateway config only.
4. Additional nodes: add deployments to the same logical names; LiteLLM balances
   and falls back. No consumer changes.

---

## 24. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Cloud outage or slowness | automatic fallback to the local tier after `FALLBACK_TIMEOUT` |
| Frontier cost runaway | only Kent/operator reach cloud tiers; 5 escalations per Gent per day; circuit breaker |
| Gent compromise / exfiltration | internal network, read-only web through the proxy, no POST, private addresses denied, no host services |
| Prompt injection via Gent output | Gent text labelled untrusted; judge/assessor prompts treat it as data; template content filter; Kent never executes Gent code |
| Router misclassification | "when in doubt, frontier"; decisions logged and reviewed in the digest |
| Knowledge poisoning across Gents | judge threshold + content filter + audit + revertible Gitea commits |
| Log tampering | HMAC chain with journal anchors; chain break → Gitea issue |
| Stuck or crashed Gent | heartbeat, requeue on restart, circuit breaker, pause/resume |
| Install drift / leftovers | manifests; verified rollback to the pre-install snapshot |
| Slow local model | per-project effort caps; small tasks; cloud tiers for Kent |
| Hermes release cadence | Kent only adds a profile; the operator controls Hermes upgrades |

---

## 25. Post-Install Checklist

1. `python3 tests/conformance/conformance.py` → 0 FAIL.
2. `kent` → ask a question → answered through the gateway (`kent-digest` shows the call).
3. Grafana `http://127.0.0.1:3001` (password in `~/.config/kent/grafana_admin_password`) → "Kent overview" populated.
4. Gitea `http://127.0.0.1:3000` → `kent/stack-template` exists and is private.
5. `tests/gent/run_tools_selftest.sh` → 0 failures (isolation and proxy policy).
6. Spawn a tiny Gent (`kent-gent spawn`), `kent-gent wait`, `kent-gent assess`, `kent-gent destroy` → registry shows it archived.
7. `kent-audit verify` → chain valid.
8. `systemctl --user list-timers 'kent-*'` → 5 timers.
9. When an Anthropic key is added: `install.sh --config prod`, `--set-fallback-timeout 120`, re-run step 1.
