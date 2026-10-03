---
title: Kent — Agentic Stack Architecture
version: 3.2.7
date: 2026-10-03
authors:
  - Keith Nielsen <keith-nielsen@github>
status: Release candidate (v0.1.0-rc) — describes the installed, conformance-tested system
license: Apache-2.0
changelog:
  - version: 3.2.7
    date: 2026-10-03
    summary: >
      Kent's Hermes runs in a bubblewrap sandbox (§12.4): only its Hermes home, its scratch
      directory and folders the operator grants per session (`--grant`, `--grant-rw`) are
      writable or visible; kent.db, the audit chain and Kent's credentials are not mounted.
      Kent's own tools (kent-gent, kent-audit, kent-digest, kent-qa-audit, kent-poll-learnings,
      the notices hook) run outside through an allowlisting broker (kent-broker.socket), which
      audits every request and fixes the audit entity to `kent`. Design:
      docs/design/kent-sandbox.md.
  - version: 3.2.6
    date: 2026-10-02
    summary: >
      The gateway repairs earlier tool calls whose arguments were cut off at the token
      limit (invalid JSON): the local model server failed every later request with 500
      "Failed to parse tool call arguments", so the agent looped until the task died.
      They are replaced by a small valid marker and a `tool_history_repaired` event is
      logged (unit and live tests). Two kent.conf settings for evaluation runs:
      `TEMPLATE_COMMITS=0` (learnings are reviewed but never committed to the template)
      and `NOTICE_LOOKBACK_HOURS` (how far back a new chat session's notices reach; 0 =
      none from before the session).
  - version: 3.2.5
    date: 2026-09-30
    summary: >
      The local model server runs two slots with a unified KV cache (`--parallel 2
      -kvu`, was one slot) and logs errors only (`-lv 1`). Measured on the reference
      machine: total decode stays at ~35 tok/s whatever the slot count (every expert
      layer runs on the CPU), so two slots let Kent and a Gent, or two Gent workers, run
      at once instead of queueing, each with the full 256k context, while a single
      stream keeps 31 of 32 tok/s. Four slots added nothing (~9 tok/s each). A long
      prompt still slows the other slot to ~5 tok/s while it prefills; smaller `-ub`
      made that worse and cut prefill (1024 stays). `-t 6` was no faster than `-t 5`.
      Re-installing the llama module while the models mount is up no longer fails
      (it re-permissioned the read-only mount point).
  - version: 3.2.4
    date: 2026-09-30
    summary: >
      Gent tasks succeed more often on the local model. A task can name its one
      deliverable (tasks.yaml `output:`); when the agent puts it in its final answer
      instead of calling Write File, the Gent CEO saves that answer as the file. A
      retry is told why the first attempt was rejected (it used to repeat the same
      instructions). The default reply limit is 4000 tokens (was 1500, which cut off
      answers) and a task can raise it (`max_tokens:`, capped at 16000). Workers and the
      validator are told today's date (UTC), and the validator sees Kent's expert answer
      when a task resumed from an escalation (a correct 2026 date was rejected as future). Found by the
      first four-role research Gent: its findings came back as text, not a file.
      The same run found the Gent could not read Kent's escalation answers (group kent,
      since Kent's own account in 3.1.0): the inbox is now setgid to the Gent's group and
      answers are 0640. Kent now reports stuck Gents (§8.2): an answer not picked up, a
      repeating supervisor error, a silent supervisor, a task without agent steps.
      Gents report to Kent and Kent to the operators (§7.5): the CEO records events
      in stack.db; Kent copies them, logs each (Loki), assesses finished projects
      automatically, and turns the important ones into notices, shown by `kent`
      (chat start, status, `kent notices`) and in Kent's chat through a pre-turn
      Hermes hook. Notices carry structured fields only, never Gent free text.
  - version: 3.2.3
    date: 2026-09-29
    summary: >
      Only the model files the settings select (MODEL_SET) are hashed and
      recorded, not every GGUF in the models directory: Kent loads one set, and
      hashing all of them read 185 GB on every install, rehash and profile switch.
      Selecting another set (kent-admin llama set MODEL_SET=...) hashes it first
      and leaves the setting unchanged if a file is missing; the start-time check
      is unchanged. install.sh shows each module's elapsed time and latest step
      while it runs. The models directory is recorded by filesystem (UUID and path
      inside it, /etc/kent/llama/models.source) so a failed `kent llama start` names
      a moved, missing or unmounted drive; a desktop automount (udisks, not in
      fstab) is a warning in lab and refused in hardened. The read-only view is
      released before udisks stops and unmounts lazily, so a busy view never stalls
      shutdown. /etc/kent/llama is 0751 so operators can check the models against
      the record (sha256sum -c). Found by the first reboot with Kent installed: an
      empty /media/administrator/DATA was left behind and the drive came back as DATA1.
  - version: 3.2.2
    date: 2026-09-29
    summary: >
      Grafana and Alloy are pinned like every other third-party piece: the tested
      versions (grafana 13.2.2, alloy 1.20.0-1) are installed from their .deb
      files, checked against SHA-256 values in versions.env, with no apt source
      added (so system updates cannot move them); a repository added by an earlier
      install is retired. Kent installs the grafana package itself instead of
      requiring one. Every pinned download (binaries, .deb and Python packages) is
      kept in the installer's download cache /var/cache/kent-install and reused
      after the same hash check; install.sh --no-cache bypasses it, a plain
      uninstall keeps it and --purge removes it. The installer accepts Ubuntu 24.04
      derivatives (Linux Mint 22). Found by the first end-to-end run of install.sh.
  - version: 3.2.1
    date: 2026-09-29
    summary: >
      Model file handling follows the profile: lab leaves the operator's files
      as they are (only write bits removed; uninstall leaves them), hardened keeps
      root:kent-models 0750/0440 with record and restore. The hash check and the
      read-only mount apply in both. A rehash logs one audit line per added,
      changed or removed model with the operator's name and keeps the previous
      record. Uninstall gains a pre-flight check, stops at the first failure with
      a likely cause and a resume command, and verifies afterwards that no Kent
      component is left (uninstall.sh --check / --verify). Found by the first full
      uninstall to a never-had-Kent state.
  - version: 3.2.0
    date: 2026-09-29
    summary: >
      The local model becomes a managed service: llama-server runs on demand as
      kent-llama.service under its own no-login kent-llama account (no sudo),
      from a root-owned copy of the build in /opt/kent-llama. Models are
      root:kent-models (directory 0750, files 0440), reached only through a
      read-only bind mount (/srv/kent/models, ro,nodev,nosuid,noexec), and each
      is SHA-256-checked against an install-time record before every load. A
      polkit rule lets kent-operators start/stop/restart that one unit
      (`kent llama`, audited); a root oneshot applies and restores the CPU tuning
      (SMT, boost). Addresses audit finding F-09 (model file permissions and
      integrity).
  - version: 3.1.0
    date: 2026-09-28
    summary: >
      Kent gets its own no-login `kent` account and its own commit-pinned Hermes
      install (/opt/kent-hermes) with an administrator-owned managed policy (lab or
      hardened profile). Humans use the `kent` command as themselves (group
      kent-operators; sudo only to kent-exec, validated and audited); files cross
      by stream, never shared directories. Kent's timers become system units; Gent
      containers are reached only through root brokers (spawn, destroy, ctl).
      SearXNG added for Kent and Gents. Top-level install.sh / uninstall.sh /
      kent-admin. tirith command scanner pinned; containers run as registered non-root
      accounts. Partly addresses audit findings F-01 (Kent no longer root-equivalent;
      the development sudo grant and lab shell remain) and F-06 (each human's use of
      Kent is attributed in the audit chain). Same day: web calls never fall back to
      outside vendors (keyless_fallback off); failed gateway calls log their cause;
      "Kent routing" dashboard; Alloy drops scrape noise and offers opt-in live
      debugging; sim-routed gateway flavour (prod routing, oracle with tool calls).
  - version: 3.0.0
    date: 2026-09-28
    summary: >
      Rewritten to describe the live harness (v0.1.0-rc). Ollama replaced by an
      operator-run llama.cpp server; Claude Opus 5.5 for smart/frontier (no
      DeepSeek); a single identity-authenticated LiteLLM gateway with
      classify-then-route; Gents local-only; Kent as a Hermes profile running as
      the operator (superseded in 3.1.0); one system account per service; manifest-driven install
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
  │  Top authority. Talks to Kent with the `kent` command (as themselves,
  │  member of kent-operators), reads Grafana and Gitea, approves anything
  │  outside the defaults.
  ▼
KENT (persistent agent — its own pinned Hermes, as the `kent` account)
  │  No login, no docker/sudo group. Owns kent.db, the stack template, Gent
  │  spawning (through root brokers), frontier access. Calls every tier.
  │  Supervises, judges, assesses.
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
| sim | `configs/litellm_config.sim.yaml` | the oracle fixture (`tests/sim/oracle.py`) | end-to-end simulations without a key; `auto` serves every tier locally |
| sim-routed | `configs/litellm_config.sim-routed.yaml` | the oracle fixture, prod routing | try the tiers as they will behave with a key: `auto` sends smart/frontier to the oracle, which can answer with tool calls |

Select with `install/services/litellm/install.sh --config dev|prod|sim`. No
application code changes: every consumer uses logical names.

### 2.4 Local Model

The local model is llama.cpp's `llama-server`, run on demand as `kent-llama.service`
(llama module) and serving one model under the alias `locally-run-model` at
`http://127.0.0.1:8080/v1`. Operators start and stop it with `kent llama start|stop`
(a polkit rule allows exactly that unit and those verbs to `kent-operators`; the `kent`
account cannot). It runs as `kent-llama` from a root-owned copy of the build in
`/opt/kent-llama`, with settings in `/etc/kent/llama/llama.env` (the measured optimum:
Qwen3.6-35B-A3B MTP, 256k context, `-ncmoe 40`, non-thinking; change with
`kent-admin llama set`). It serves two requests at once (`--parallel 2` with a unified KV
cache, so each can use the whole context); the slot count is fixed in the launcher. Ownership of the model files follows the profile: in **lab** they
stay the operator's (Kent only removes their write bits, so they must be world-readable, and
uninstall leaves them); in **hardened** they are `root:kent-models` (0750/0440), the originals
are recorded and restored on uninstall (`kent-admin profile` switches and re-applies). In both,
the server reads them through a read-only bind mount at `/srv/kent/models`
(`ro,nodev,nosuid,noexec`) and each start refuses a model whose SHA-256 differs from the
recorded one (`/etc/kent/llama/models.sha256`). Only the files the settings select
(`MODEL_SET`) are recorded; selecting another set with `kent-admin llama set MODEL_SET=...`
hashes it before the setting is written. The record is readable by operators (`/etc/kent/llama`
is 0751: files by name, no listing), so `sha256sum -c /etc/kent/llama/models.sha256` in the models
directory checks them by hand. `/etc/kent/llama/models.source` records the directory's filesystem
(UUID and path inside it); when a start fails, `kent llama start` prints the likely cause (drive
not connected, not mounted, or mounted under another name such as `DATA1` because an unclean
shutdown left an empty `DATA` behind; Kent never removes that directory itself). Models on a
desktop automount (udisks, not in `/etc/fstab`) work in lab once the operator has logged in (the
installer warns); hardened refuses them and needs a system mount. The read-only view is ordered
after `udisks2.service` (released first at shutdown) and unmounted lazily, so a shell inside it
never stalls shutdown. Known limit: in lab the operator owns the model files, so a file could be
swapped between the start-time hash check and the server opening it (hardened: root only).
`kent-admin models rehash` after replacing a model logs one audit line per added, changed or removed file with the operator's
name (`human:<name>`) and keeps the previous record as `models.sha256.<UTC time>`. SMT and
CPU boost are switched off by a root oneshot while the server runs and restored after.

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

Kent's Hermes uses provider `custom`, base URL `http://127.0.0.1:4000/v1`, model
`auto`, key from `KENT_GATEWAY_KEY` in its managed `.env` (§7.3). Gents reach the gateway at
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

A reply cut off at its token limit in the middle of a tool call leaves invalid JSON in the
call's arguments. The local model server cannot render such a history and fails every later
request with 500 "Failed to parse tool call arguments", so the agent never sees its own tool
error and the task dies. Before each call the gateway replaces any earlier tool-call arguments
that are not a JSON object with a small valid marker (the client's tool error message is kept)
and logs a `tool_history_repaired` event.

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
(`limits: {max_iter, max_tokens}`; defaults 6 / 4000) so the local model stays responsive;
a task can raise `max_tokens` (cap 16000). A task's `output:` names its deliverable: the
CEO asks for it by name and, if the agent gives it as its final answer instead of writing
it, saves that answer as the file. A retry is told the validator's critique of the failed
attempt.

Project format (written by Kent, or by `skills/kent/crew-designer/scripts/generate_crew.py`):

```yaml
# project.yaml
name: "kernel-check"
goal: "What is delivered and for whom"
limits: {max_iter: 6, max_tokens: 4000}
# agents.yaml
developer: {role: "...", goal: "...", backstory: "..."}
# tasks.yaml (order = dependency order)
build:
  agent: developer
  description: "Concrete work; name the files to write"
  expected_output: "What done looks like"
  escalate: true                 # optional: ask Kent first
  question: "The expert question"   # optional
  output: result.md              # optional: the deliverable, in /data/workspace
  max_tokens: 6000               # optional: longer replies for this task
```

---

## 7. Kent & Gent Agent Integration

### 7.1 Two Roles

| Role | Instance | Runs as / on | Persistence | Key |
|---|---|---|---|---|
| Kent | singleton | `kent` account, host (own Hermes + system timers) | `/var/lib/kent/kent.db` | `kent` |
| Gent CEO | one per project | `gent-<id>`, Docker container `kent-gent-<id>` | `/var/lib/kent-gent/stacks/<id>/data/stack.db` | `gent-<id>` |

### 7.2 Kent Responsibilities — Live

| Duty | How |
|---|---|
| Conversation partner | `kent` command → `kent-exec` → Kent's Hermes; SOUL in `templates/app/SOUL.md`; `crew-designer` skill; web search via SearXNG (search only: no page-fetch backend yet; `web.keyless_fallback: false` so failed web calls never retry through outside vendors) |
| Classification of all requests | model `auto` at the gateway |
| Gent spawn / supervise / retire | `kent-gent spawn|list|status|logs|wait|pause|resume|restart|destroy` |
| Escalation relay, learning review, circuit breaker | `kent-poll-learnings` (every minute) |
| Assessment of finished work | `kent-gent assess` (frontier) → `gent_assessments`, workspace published to Gitea |
| Template commits | confident adoptions appended to `LEARNINGS.md` in `kent/stack-template` |
| Daily digest | `kent-digest` (07:00) → `/var/lib/kent/digests/`; operators read it with `kent digest` |
| Nightly QA | `kent-qa-audit` (02:00) → `qa_audit_log` |
| Audit | `kent-audit` (chain), ingest every 5 min, anchor 04:30 |

Planned: access-request evaluation (§15.4), seed-from (§11.2).

### 7.3 Gent CEO Responsibilities — Live

- Seed the kanban from `tasks.yaml`; run tasks in order; requeue interrupted work at start.
- Decide escalation (flag or router judgement); block and resume through the inbox.
- Validate each result on `fast` against the files actually written; one retry (told the
  critique), then escalate.
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

### 7.5 Reporting: worker → Gent → Kent → operator — Live

- **Worker → Gent**: the CEO runs each worker's task itself and gets its result directly
  (kanban status, `outputs/<task>.md`, the validator's verdict).
- **Gent → Kent**: the CEO records events in its `stack.db` (`events`: `task_done` with the files
  written, `task_failed`, `escalated`, `resumed`, `project_complete`, `halted`). The channel is
  the same one-way path as learnings: the Gent writes its own database, Kent reads it (every
  minute, `kent-poll-learnings`) and copies new events into `kent.db` (`gent_events`). Each event
  is a journal line (`gent_event stack=… kind=… task=…`, in Loki under
  `kent-poll-learnings.service`).
- **Kent acts**: on `project_complete` Kent runs a frontier review (`kent-gent assess`, one frontier
  call; `AUTO_ASSESS=0` in kent.conf turns it off): for each task, does the deliverable exist and
  meet its description and expected output (pass / partial / fail and a one-line reason), the
  main issues, an overall verdict and usefulness. The reply is validated (known task ids, fixed
  result words, single capped lines) and stored in `gent_assessments.details`; `kent-gent status`
  shows the latest review.
- **The team's own verdict**: a task marked `verdict: true` (the project's review task) must end
  with `Overall: PASS` or `Overall: FAIL`; the CEO reports only that word (`review` in the
  `project_complete` event), and Kent passes it on only if it is exactly PASS or FAIL.
- **Kent → operator**: project complete (with the team's verdict and Kent's per-task frontier review), task failed, halted (by the Gent
  or the circuit breaker) and stuck become notices (`kent.db` `notices`, read state per reader).
  `kent` shows unread notices before a chat starts, `kent status` counts them, `kent notices
  [--all]` lists them. Kent's own chat gets them through a Hermes `pre_llm_call` shell hook
  (`kent_notices_hook.py`, managed policy; consent is an allowlist entry for exactly that command,
  not `hooks_auto_accept`): unread notices are added to that turn's user message, once per chat
  session, a new session starting with the last 24 hours (`NOTICE_LOOKBACK_HOURS` in kent.conf;
  0 = only notices from after the session started, used by evaluation runs).
- **Injection boundary**: notices reach Kent's model, so they are composed by Kent from
  structured fields only: event kind, a validated task id, plain file names (others are dropped
  and counted), the Gent's registry name and Kent's own assessment verdict. Gent summaries,
  details and error texts stay in `gent_events` and the logs.
- Not yet: notifications while nobody is at the terminal (desktop, phone via Hermes' messaging
  gateway).

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

**Stuck Gents** (same minute check; alert, no halt): a Gent can stop progressing without
failing a task. Kent reports, once per new reason, when a delivered escalation answer has not
been picked up after 2 minutes, when the CEO's loop has repeated the same error for 5 minutes
or has not run for 10 minutes while no task is running (the CEO writes `/data/CEO_STATUS`
each pass: time, last error, since when, how often), or when a running task has had no agent
step for 20 minutes. Each report is a journal line (`STUCK:`, in Loki under
`kent-poll-learnings.service`), an audit event `gent_stuck` (`gent_unstuck` on recovery) and a
Gitea issue; `kent gent status` shows the same reasons. Nothing is halted: a stuck Gent uses no
resources, and the cause may be Kent's (2026-09-30: the Gent could not read Kent's answer).
The Gent's own log is in the journal under `kent-gent-<id>` (Docker's journald driver; the
tag is set by Kent at spawn, so Gent output cannot pose as a source audit ingest trusts).

### 8.3 Nightly QA Audit — Live

02:00: up to 20 completed tasks sampled across Gents, reviewed on frontier,
recorded in `qa_audit_log`. Planned: critical findings open Gitea issues;
systematic misroutes feed router tuning.

---

## 9. Data Architecture — Live

### 9.1 Layout

```
/var/lib/kent/                    (kent, 0750)
├── kent.db                       Kent's estate DB (schemas/kent.sql), 0600
├── audit/hmac_chain.log          audit chain (0700 dir)
├── digests/YYYY-MM-DD.md
├── inbox/<operator>/<time>/      projects handed over by `kent new` (0700)
├── hermes/                       Kent's Hermes home: sessions, memory, skills, SOUL
└── work/                         Kent's working directory
/etc/kent/kent/                   (root:kent 0750; files 0640)
├── kent.conf                     paths and URLs for Kent's tools
└── credentials/                  litellm_kent_key, gitea_kent_token, audit_hmac_secret
/etc/kent/hermes/managed/         (root:kent) Hermes managed scope: config.yaml, .env
~<operator>/.config/kent/         (operator, 0700; files 0600) the operator's own
                                  litellm_operator_key, gitea/grafana admin passwords
/var/lib/kent-gent/
├── stacks/<id>/data/             gent-<id>:kent 2750 (setgid)
│   ├── stack.db                  schemas/stack.sql
│   ├── project/                  project.yaml, agents.yaml, tasks.yaml, LEARNINGS.md
│   └── workspace/                deliverables, outputs/, REPORT.md
├── stacks/<id>/inbox/            kent:gent-<id> 0750 — Kent's answers, resume tokens
├── keys/<id>.key                 root:gent-<id> 0440 (mounted read-only)
├── registry/<id>                 root 0700 — what kent-destroy-gent may remove
└── archive/<id>/                 root:kent, read-only copies of retired Gents
```

### 9.2 Access Matrix

| | kent.db | Gent stack.db / workspace | Gent inbox |
|---|---|---|---|
| Human | through `kent` only | through `kent gent export` (a copy, owned by the human) | none |
| Kent (`kent`) | R/W | **R only** (group read, SQLite `mode=ro`) | W (answers, tokens) |
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
commit can be reverted in Gitea. `TEMPLATE_COMMITS=0` in kent.conf keeps the review but
commits nothing (the verdict notes say so); evaluation runs use it so the template stays fixed.

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

### 12.4 Kent's sandbox

Kent's Hermes (and every shell, script and hook it starts) runs under `bwrap` from the
`kent-hermes` launcher: own mount, PID, IPC and UTS namespaces, host network, all capabilities
dropped, dies with its launcher. Read-only: `/usr`, `/etc` (Kent's credentials directory masked),
`/opt/kent-hermes`, `/opt/kent-core`. Writable: `/var/lib/kent/hermes` and `/var/lib/kent/work`;
the rest of Kent's home is a throwaway tmpfs, so kent.db, the audit chain, digests and inbox are
absent. The operator's folders are absent unless granted for the session: `kent --grant DIR`
(read-only) or `--grant-rw DIR`. The `kent` command opens each folder as the operator and hands
it over as an open descriptor (`sudo -C`, sudoers `closefrom_override` for kent-exec only), so
folders inside a home work; `kent-exec` accepts only directories the calling operator owns,
outside system and Kent trees and not a whole home, audits each grant and closes every other
descriptor; the launcher mounts them by descriptor and closes the descriptors inside.

`sudo` cannot work inside (`no_new_privs`), and Kent's own tools need it and Kent's private
state. They run outside through `kent-broker` (`/run/kent-broker.sock`, kent 0600, one process per
request): inside, a client stands in for each tool name; the broker accepts only the listed
tools and argument shapes, stages Gent project files without following symlinks, audits every
request (`broker`) and refusal (`broker_refused`), and writes audit entries only as `kent`.
The approval gate stays; for read-write grants it is still the only check (staged writes are
the planned fix). Containment tests: operator-run (TODO.md).

---

## 13. Secrets Management — Live

- Service secrets live in `/etc/kent/<service>/credentials/` (root-owned) and
  reach processes through systemd `LoadCredential` (`$CREDENTIALS_DIRECTORY`),
  or Gitea's `*_URI` file settings; never environment variables or config files.
- The Anthropic key is exported only inside the gateway process by its start
  wrapper (LiteLLM's provider reads the environment).
- Kent's own credentials (gateway key, Gitea token, audit HMAC secret) are in
  `/etc/kent/kent/credentials/` (root:kent 0640); Hermes gets the gateway key through
  its managed `.env` (root:kent 0640), which Kent cannot modify.
- An operator's own credentials (operator gateway key, admin passwords) are in
  `~/.config/kent/` (0600). No Kent credential is in an operator's home.
- A Gent's key is a read-only bind mount at `/run/kent/gent_key`; its hash is in the registry.
- Nothing secret is committed; `.gitignore` excludes secrets and local settings.

---

## 14. Unix Users & Groups — Live

| Account | Created by | Runs |
|---|---|---|
| operator (e.g. `administrator`), in `kent-operators` (and `kent-models` in the hardened profile) | — | the `kent` command (incl. `kent llama start\|stop`) |
| `kent` | hermes module | Kent (its own Hermes), Kent's 5 system timers |
| `litellm` | litellm module | gateway |
| `prometheus`, `node_exporter`, `loki` | their modules | metrics and logs |
| `alloy` | Alloy apt package (installed by Kent) | log shipping |
| `grafana` | Grafana package (pre-existing) | dashboards |
| `gitea` | gitea module | forge |
| `kent-squid` | gent module | Gent egress proxy |
| `kent-searxng` | searxng module | SearXNG container (`--user`, never root) |
| `kent-llama` | llama module | local model server; reads models through group `kent-models` (unit `SupplementaryGroups`) |
| `gent-<id>` | `kent-spawn-gent`, removed by `kent-destroy-gent` | one Gent container |
| DynamicUser | systemd | the three Gent bridge socket proxies |

No shared account, and no one logs in as `kent`. Two sudo rules make up the whole
privilege surface: `%kent-operators ALL=(kent) NOPASSWD: kent-exec` (the only way
into Kent; validates arguments, records `human:<name>` in the audit chain), and
`kent ALL=(root) NOPASSWD:` the root-owned, argument-validated brokers
`kent-spawn-gent`, `kent-destroy-gent`, `kent-gent-ctl`. Kent's Hermes cannot use that rule
itself (sandboxed, §12.4): its Gent tools reach it through `kent-broker`, outside the sandbox. One polkit rule adds that
`kent-operators` may start, stop and restart `kent-llama.service` (no other unit or verb). Full detail:
`docs/privilege-map.md`.

### 14.1 Profiles

`/etc/kent/profile` selects Kent's Hermes policy, rendered from
`configs/hermes/base.yaml` + `profile-<name>.yaml` into the managed scope:

| | lab (development) | hardened (deployment) |
|---|---|---|
| Toolsets | incl. terminal, code execution, delegation | no code execution, no delegation |
| Shell | general shell as `kent` (approval for dangerous commands) | plus denies for sudo, curl/wget, `python -c`, nc |
| Both | tirith pre-exec scanning (pinned binary; fails open in lab, **closed** in hardened); one-shot and cron runs deny dangerous commands; yolo/oneshot re-entry, docker, managed-scope and credential paths denied; update checks off; secrets redacted | |

Switch with `sudo ./kent-admin profile lab|hardened`.

---

## 15. Egress Control

### 15.1 Architecture — Live

```
Gent container ── kent-gent-net (internal, 172.30.0.0/24, bridge kent-gent0; no route out)
   ├─► 172.30.0.1:4000 ── kent-gent-gateway.socket ─► 127.0.0.1:4000 (gateway)
   ├─► 172.30.0.1:8888 ── kent-gent-search.socket  ─► 127.0.0.1:8888 (kent-searxng) ─► search engines
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
| search | SearXNG JSON API via its own bridge (Kent's instance: pinned image, read-only, no capabilities) |
| Gitea, Grafana, Prometheus, Loki, LAN | unreachable |

### 15.3–15.4 POST Allowlist, Anomaly Detection, Access Requests — Planned

Per-Gent, time-bounded proxy grants approved by the Human through Kent; outbound
payload anomaly logging.

---

## 16. Firewall

Deferred by decision: the host firewall will be hardened later. Kent adds only
two UFW rules when UFW is active, allowing inbound on `kent-gent0` to
`172.30.0.1` ports 3129, 4000 and 8888 (recorded, removed on uninstall). Every Kent
service listens on 127.0.0.1 (or the Gent bridge address); the conformance
check verifies nothing is exposed beyond that.

---

## 17. Observability & Alerting — Live

| Component | Version | Listens | Notes |
|---|---|---|---|
| Prometheus | 3.15.0 | 127.0.0.1:9090 | 30 d / 5 GB retention; scrapes prometheus, node, litellm (`metrics` key), loki, alloy, grafana, gitea |
| node_exporter | 1.12.1 | 127.0.0.1:9100 | host metrics |
| Loki | 3.7.8 | 127.0.0.1:3100 | tsdb v13, 30 d retention; logs its own events at `warn` |
| Grafana Alloy | 1.20.0 (apt) | 127.0.0.1:12345 | journald → Loki; labels `unit`, `level`, `identity`, `kent_event`; drops Prometheus-scrape request lines (Gitea `GET /metrics`); live debugging opt-in (`alloy/install.sh --live-debugging`, off on any re-run without it) |
| Grafana | 13.1.0 (vendor package) | 127.0.0.1:3001 | provisioned datasources; dashboards "Kent overview" (calls by identity, denials, router decisions, targets, CPU, memory, gateway log) and "Kent routing" (tier decisions, escalation outcomes, fallbacks, failure reasons, p95 latency, tokens per identity, backends); Logs/Metrics Drilldown apps; no anonymous access |

Gent containers log to journald (`kent-gent-<id>`), so they reach Loki too.

Every gateway call is one JSON line (`kent_event: llm_call`: identity, model group, routed tier and
routing cause, backend, tokens, latency); failed calls also carry `error_class` and `error`, so a
silent fallback to the local tier always records its cause. Operator guide with tested queries:
`docs/observability-guide.html`.

Known gap: Loki and the Alloy UI have no per-account access control; any local account (including
`kent` in the lab profile) can read the whole journal through them.

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

`/var/lib/kent/audit/hmac_chain.log`: each entry
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
`capset`, which are harmless without capabilities. kent-llama needs the NVIDIA device
nodes, so it has `DevicePolicy=closed` with only those allowed instead of
`PrivateDevices`, and no `MemoryDenyWriteExecute` (CUDA may JIT kernels).

`systemd-analyze security` exposure: litellm 1.5, prometheus 1.5, node_exporter
2.0, loki 1.5, alloy 1.7, grafana 2.9, gitea 1.5, kent-squid 1.5, kent-llama 1.6, bridges 1.2.

Planned: systemd-oomd `ManagedOOM*` policies for the heavy services.

---

## 20. Host / Docker Partitioning — Live

| Host (systemd) | Docker |
|---|---|
| `kent-litellm`, `kent-prometheus`, `kent-node-exporter`, `kent-loki`, `alloy`, `grafana-server`, `kent-gitea`, `kent-squid`, `kent-gent-{egress,gateway}.socket`; Kent's timers; `kent-llama` (on demand) | `kent-gent-<id>` containers only |

If Docker stops, Kent and all services keep working; a crashed Gent affects nothing else.

---

## 21. Platform Compatibility

Tested: Linux Mint 22.3, built on Ubuntu 24.04 LTS (systemd 255, Docker 29, Python 3.12);
the installer accepts Ubuntu 24.04 and derivatives built on it. Third-party components
are pinned in `install/services/versions.env`, each with its SHA-256: upstream release
tarballs and binaries, and the Grafana and Alloy `.deb` packages (installed from the file;
no apt source is added, so system updates cannot move them). Squid and base packages come
from the distribution's own apt repositories. Downloads are kept in the installer's cache
(`/var/cache/kent-install`, see §22). Other distributions are untested; Fedora/RHEL would need `dnf`, firewalld
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
| Installer download cache (`/var/cache/kent-install`) | ~1–3 GB: pinned binaries, grafana/alloy `.deb`, Python packages. Kept by a plain uninstall for the next install; removed by `uninstall.sh --purge`; safe to delete any time to reclaim space (`sudo rm -rf /var/cache/kent-install`) |

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
| Hermes release cadence | Kent pins Hermes by tag, commit and `uv.lock` hash; upgrades are a reviewed change to `versions.env` |

---

## 25. Post-Install Checklist

1. `sudo ./kent-admin conformance` → 0 FAIL.
2. `kent -q "Who are you?"` → answered through the gateway (`kent digest` shows the call).
3. Grafana `http://127.0.0.1:3001` (password in `~/.config/kent/grafana_admin_password`) → "Kent overview" populated.
4. Gitea `http://127.0.0.1:3000` → `kent/stack-template` exists and is private.
5. `tests/gent/run_tools_selftest.sh` → 0 failures (isolation, proxy policy, search bridge).
6. A tiny Gent: `kent new DIR`, `kent gent wait ID`, `kent gent export ID ./out`, `kent gent assess ID`, `kent gent destroy ID` → `kent gents` shows it archived.
7. `kent audit` → chain valid.
8. `systemctl list-timers 'kent-*'` → 5 timers.
9. When an Anthropic key is added: `sudo ./kent-admin set-anthropic-key`, `sudo ./kent-admin config prod`, `sudo install/services/litellm/install.sh --set-fallback-timeout 120`, re-run step 1.
