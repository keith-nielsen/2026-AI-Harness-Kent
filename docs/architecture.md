---
title: Kent — Agentic Stack Architecture
version: 3.4.0
date: 2026-10-18
authors:
  - Keith Nielsen <keith-nielsen@github>
status: Release candidate (v0.1.0-rc) — describes the installed, conformance-tested system
license: Apache-2.0
changelog:
  - version: 3.4.0
    date: 2026-10-18
    summary: >
      Thinking-budget triage (classifier redesign task 5; operator decisions 2026-10-17/18:
      high-water on with conf09 at ~30%, low-water unset, Kent only, keep auto's classifier and
      its frontier escalation). When Kent's auto call lands on default, kent_gateway asks the
      local model one question first (the conversation plus a conf09 "how likely is a correct
      reply without thinking, 0-9" message, without the routing-check preamble: 54 prompt
      tokens instead of 74, same separation; 1 token, thinking off, token probabilities). An
      expected digit below 5.0 raises that call's thinking budget to smart's 4096; errors, a
      30 s timeout or no usable answer leave it at 2048 (5 s at first; a cold
      11k-token prompt timed out, and the real request reuses what the triage reads). Settings in configs/triage.json
      (/etc/kent/litellm/triage.json, re-read on change); one kent_event "triage" log line per
      decision, joined to its call by litellm_call_id (now also on llm_call lines). Measured on
      the real model through a scratch gateway: decisions match the offline triage check
      (8 probes), the real request reuses the triage's cached prompt (5 new tokens), cost about
      1.3 s per triaged turn with the preamble (the triage message is always new prompt); the
      shorter wordings tried lost usable answers or separation (triagecheck-10181435).
  - version: 3.3.1
    date: 2026-10-18
    summary: >
      Grafana plugins pinned with the package. Grafana downloaded and updated about 17
      plugins from grafana.com on every start, and those copies override the ones bundled in
      the pinned .deb; the 3.3.0 install failed its data-source check while the prometheus
      plugin was being replaced. kent.conf now turns off plugin preinstall and web installs,
      the installer empties /var/lib/grafana/plugins, refreshes the vendor unit's
      /var/lib/grafana/plugins-bundled from the package (root-owned; it was empty here, so
      Grafana had run on downloaded copies only) and checks that prometheus and loki are
      the bundled versions (13.1.7, 13.2.0), the health check waits up to 30 s, and
      conformance fails on any downloaded plugin; plugin signatures use the package's own key
      (no key fetch). Tested on a scratch Grafana 13.2.2 started with the vendor unit's paths: no
      downloads and no outbound connections in 60 s, both data sources healthy, all 24 dashboard
      queries answered. Found in the same audit: installing the squid package had also started
      the vendor squid.service (a proxy on *:3128, loopback clients allowed, LAN refused); the
      gent installer now switches it off when Kent installed the package, and conformance checks it.
  - version: 3.3.0
    date: 2026-10-17
    summary: >
      Five tiers (operator decision): fast (local, thinking off), default (local, thinking
      up to 2048 tokens), smart (local, thinking up to 4096), cheap (DeepSeek V4.1 Flash,
      cloud; a placeholder, nothing routes to it yet) and frontier (Claude Opus 5.5, cloud).
      The cloud tiers are Kent's (and the operator's); Gents stay local-only (router, fast,
      default, smart), enforced by the gateway policy, the per-call upstream check, the
      installer guard and conformance. smart drops from 8192 to 4096: on the 42 hard probes
      4096 passed 74.6% (= 8192) at 23% less time; default is 2048 because on all 115 probes
      no classifier separated the prompts that are safe without thinking (thinking off
      77.7% vs 2048 85.8%). Both thinking tiers time out at 900 s (longest probe call 604 s
      at 4096). Interim `auto` until the triage step: the classifier's fast and smart labels
      both go to default, frontier escalates, and a failed or timed-out classification goes to
      default (it went to frontier before; a classifier timeout under load sent a one-line
      sum to the cloud in the scratch test). New optional DeepSeek key (credential
      deepseek_api_key, install.sh --set-deepseek-key, kent-admin set-deepseek-key); sim and
      sim-routed send cheap to the oracle as model oracle-cheap. The call log adds routed_model
      (the tier that served an auto call; routed_tier stays the classifier's label) and the
      digest and dashboards group by it. Built and tested in the repo
      2026-10-17 (unit, live, scratch gateway with the oracle); install pending (operator sudo).
  - version: 3.2.11
    date: 2026-10-16
    summary: >
      Tiers: smart is now the local model with thinking on, set per request by the
      gateway (extra_body: chat_template_kwargs.enable_thinking, thinking_budget_tokens,
      Qwen's thinking-mode sampling); router and fast send thinking off explicitly.
      Frontier stays Claude Opus 5.5 (oracle in sim/sim-routed), the only cloud tier.
      kent_gateway adds the thinking budget to the caller's max_tokens (reasoning counts
      against it). Gents may now call smart (operator decision); every Gent call is
      checked after routing and refused unless it lands on the local model server,
      the installer refuses a config that points a Gent tier or its fallback elsewhere,
      and conformance checks the installed tiers. Gent triage has three outcomes
      (fast / think on smart / escalate); a rejected attempt is retried on smart.
      Bench mode's gateway is chosen when the bench tools are installed (dev or
      sim-routed). Thinking budget 8192, from the think-v1-1016 probe run (42 probes x 3:
      74.6% pass, 65.1% at 2048, 41.3% thinking off); smart has its own 1200 s timeout
      (thinking to the budget took up to 823 s) and the Gent client waits 1500 s. Built
      and tested 2026-10-16; install pending (operator sudo).
  - version: 3.2.10
    date: 2026-10-16
    summary: >
      The local model server moves from the TheTom llama.cpp fork (c26cbdf; K q8_0 /
      V turbo3, 256k, no speculative decoding) to upstream llama.cpp v0.6.0 (d812350,
      built with GGML_CUDA_FA_QUANTS=all): Qwen3.6-35B-A3B MTP Q4_K_XL at 128k context,
      K q8_0 / V q4_0, MTP with 2 drafts and no acceptance threshold (draft cache f16),
      -ub 1024, every expert layer on the CPU, -lm none (v0.6.0 has no --no-mmap).
      256k and turbo3 are retired (operator decision). Measured beforehand (kvcheck-10061119,
      kv-quality: q8/q4 same quality as q8/q8; depth runs: MTP 2 p0 fastest at every depth)
      and after the switch on Kent: one stream ~30 tok/s prose, both slots 40 tok/s
      combined, peak VRAM 5.8 of 8 GB with both slots on 16K prompts, image input works
      with MTP, conformance 149/0. The fork build is kept at /opt/kent-llama.forkc26cbdf
      for rollback.
  - version: 3.2.9
    date: 2026-10-05
    summary: >
      The local model server no longer overrides its batch thread count (`-tb 4`
      removed; llama.cpp then uses `-t`, 5). Every pass with more than one token
      uses the batch threads, including each step while both slots are generating.
      A formal A-B-B-A comparison under the bench host profile (tests/bench/tbcheck.sh,
      decision rule fixed beforehand: keep the override only if it is 3% faster
      somewhere and not 3% slower anywhere) found -tb 4 8% slower for two concurrent
      decodes and 3% slower for concurrent real workloads, and no faster anywhere
      (prompt processing identical).
  - version: 3.2.8
    date: 2026-10-05
    summary: >
      Starting or stopping the local model server no longer changes any host
      performance setting. The kent-llama-tuning.service that switched SMT and CPU
      boost off when kent-llama started wrote the boot values back when it stopped,
      so every benchmark that stopped kent-llama to run its own server silently ran
      with boost on (about 3.95 instead of 3.6 GHz, 82-86 C). It is retired: the
      installer removes it from an existing install without running its restore step,
      so SMT and boost stay exactly as they are. Host settings (SMT, boost, CPU
      governor, swap, GPU clock lock) are now the operator's alone, set by hand with
      tests/bench/hostprofile.sh (show, check, apply, defaults); no Kent service and no
      bench job changes them, and bench jobs refuse to measure unless `check` passes
      after kent-llama is stopped. The conformance check asserts that no tuning unit
      exists.
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
      (SMT, boost; retired in 3.2.8). Addresses audit finding F-09 (model file
      permissions and integrity).
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
matching tier: the local model answering directly for routine work, the same local
model thinking first for reasoning work (two budgets), Claude Opus 5.5 for frontier work
(DeepSeek V4.1 Flash is defined as a cheap cloud tier for later use), with automatic
fallback to the local model when a tier fails.

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
| Kent (`kent` key) | all: router, fast, default, smart, cheap, frontier, auto | kent.db (R/W); every Gent's stack.db (**read-only**) | unrestricted (host process) | Gents |
| Gent CEO + workers (`gent-<id>` key) | router, fast, default, smart (local only) | own stack.db (R/W) | gateway + egress proxy only | nothing |
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
| `fast` | local llama.cpp model, thinking off | Qwen3.6-35B-A3B via `llama-server` on 127.0.0.1:8080 | routine, mechanical work; Gent tasks by default |
| `default` | the same local model, thinking up to 2048 tokens | same | ordinary work: where `auto` sends a request unless it is classified up |
| `smart` | the same local model, thinking up to 4096 tokens | same | harder reasoning and coding; Gent tasks triaged "think", Gent retries, learning reviews |
| `cheap` | DeepSeek V4.1 Flash (Kent only) | local model (dev config); the oracle as `oracle-cheap` (sim, sim-routed) | reserved: defined so the tiering is in place; nothing routes to it yet |
| `frontier` | Claude Opus 5.5 (Kent only) | local model (dev config); the oracle (sim, sim-routed) | hard problems, escalations, assessments, QA |
| `auto` | router → default / frontier (interim, see §4); for Kent, triage may raise default to smart's budget | same | the default for Kent and the operator |

default and smart are fast's model with thinking turned on per request, so they need no
second model and no extra VRAM. The gateway sets it in each deployment's `extra_body`, which overrides
the caller: `chat_template_kwargs.enable_thinking: true`, `thinking_budget_tokens` (the
server stops the reasoning there) and Qwen's thinking-mode sampling (temperature 1.0,
top_p 0.95, top_k 20, presence_penalty 1.5). llama-server counts reasoning tokens against
`max_tokens`, so kent_gateway adds the budget to the caller's `max_tokens` after routing
(`thinking_max_tokens`). router and fast send `enable_thinking: false` explicitly.

The budgets come from the 2026-10-17 budget curve on all 115 bench probes (production
server; 42 hard probes x 3 repeats, 73 easy probes x 1-3): pass rate off 77.7% / 2048 85.8% /
4096 88.1% / 8192 87.2%. On the hard probes 4096 passed 74.6% against 65.1% at 2048 (8 vs 20
discordant, p 0.036) and equalled 8192 at 23% less time, so smart is 4096. Easy probes gain
nothing from thinking (off 98.6% vs 2048 97.7%, a third of the time), but no classifier tried
(router, NVIDIA classifier, logprob triage: best AUROC 0.77) separates them reliably enough to
send anything to thinking off, so the default thinks (2048). Thinking decodes at about 20 tok/s
(Qwen's thinking sampling lowers MTP acceptance); the longest probe call took 430 s at 2048 and
604 s at 4096, so both thinking tiers have their own `timeout`/`stream_timeout` of 900 s; on
timeout they fall back to fast. Gent clients wait 1500 s, so a call that runs out of time comes
back as the fallback answer, not a client timeout.

### 2.3 Gateway Configurations

| Config | File | cloud tiers (frontier, cheap) go to | Use |
|---|---|---|---|
| prod | `configs/litellm_config.yaml` | `anthropic/claude-opus-5-5`, `deepseek/deepseek-v4.1-flash` (model id unverified until a key is tested) | normal operation (needs an Anthropic key; the DeepSeek key is optional) |
| dev | `configs/litellm_config.dev.yaml` | the local model | local-only testing |
| sim | `configs/litellm_config.sim.yaml` | the oracle fixture (`tests/sim/oracle.py`) | end-to-end simulations without a key; `auto` serves every tier locally |
| sim-routed | `configs/litellm_config.sim-routed.yaml` | the oracle fixture (`oracle-frontier`, `oracle-cheap`), prod routing | try the tiers as they will behave with keys: `auto` sends frontier to the oracle, which can answer with tool calls |

In every config, router/fast/default/smart are the local model (default and smart thinking).

Select with `install/services/litellm/install.sh --config dev|prod|sim`. No
application code changes: every consumer uses logical names.

### 2.4 Local Model

The local model is llama.cpp's `llama-server`, run on demand as `kent-llama.service`
(llama module) and serving one model under the alias `locally-run-model` at
`http://127.0.0.1:8080/v1`. Operators start and stop it with `kent llama start|stop`
(a polkit rule allows exactly that unit and those verbs to `kent-operators`; the `kent`
account cannot). It runs as `kent-llama` from a root-owned copy of the build in
`/opt/kent-llama`, with settings in `/etc/kent/llama/llama.env` (the measured optimum:
Qwen3.6-35B-A3B MTP Q4, 128k context, K q8_0 / V q4_0 cache, MTP 2 drafts, `-ncmoe 40`,
non-thinking, upstream llama.cpp v0.6.0; change with
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
name (`human:<name>`) and keeps the previous record as `models.sha256.<UTC time>`. Starting or
stopping the server changes no host performance setting (3.2.8): SMT, CPU boost, the CPU governor,
swap and the GPU clock lock are set only by the operator, by hand, with `tests/bench/hostprofile.sh`
(`show` and `check` need no root; `apply` sets the bench profile and `defaults` the boot defaults,
both with sudo). The llama.cpp settings were measured with SMT off and boost off.

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
| `gent` | systemd credential `gent_key` (tests) | router, fast, default, smart | chat/completions, models |
| `gent-<id>` | `/etc/kent/litellm/gent-keys/<id>.key` (written at spawn, hot-reloaded) | router, fast, default, smart | chat/completions, models |

A Gent call must also land on the local model server (`LOCAL_MODEL_API_BASES`): kent_gateway
checks every Gent call after LiteLLM has resolved tier and fallback, and refuses it otherwise
(`gent_upstream_refusal`); the installer refuses a config that points a Gent tier, or a
fallback of one, anywhere else.
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

Interim mapping (3.3.0, until the triage step replaces this classifier): the labels fast and
smart both go to the `default` tier and frontier to `frontier`; nothing goes to thinking off.
An unclassifiable request, or a classification that fails or times out (15 s), goes to
default; before 3.3.0 it went to frontier, and under load a classifier timeout sent a
one-line sum to the cloud. Every decision is logged
(`routed_tier`, `routing_cause`) and shown in the digest and on Grafana.

Measured (live probe, `tests/live/router_probe.py`, before 3.3.0): easy prompts → fast 8/8;
hard prompts → smart/frontier 8/8; prompt injections telling the router to
downgrade or upgrade changed no decision. On the 42 hard bench probes the same classifier
sent 54% of the prompts that needed thinking to fast (2026-10-16), which is why its fast
label no longer means thinking off.

**Thinking-budget triage (3.4.0, Kent only).** When the router sends a Kent call to `default`,
`kent_gateway.apply_triage` first sends the local model the same conversation (and tools) plus
one user message: "How likely is it that the assistant's next reply would be fully correct if it
answered right now, with no hidden thinking? 0 = very unlikely, 9 = certain. Reply with one digit
only."
It asks for one token with thinking off and reads the digit probabilities. If the expected digit
is below the high-water mark (5.0), the call keeps `default` but gets smart's thinking budget
(4096); otherwise, or on any error, timeout (30 s) or unusable answer, it stays at 2048. The
low-water mark (send easy calls to thinking off) is unset: no classifier tried separated those
calls reliably enough. Settings: `configs/triage.json` → `/etc/kent/litellm/triage.json`
(checked by the installer and conformance; edits apply without a restart). Each decision is a
`kent_event: "triage"` line (score, mass, decision, budget, ms, version), joined to its call
by `litellm_call_id`.

Calibration: on the 115 bench probes the threshold sends 33 with a usable answer to 4096 (~30%;
`triagecheck-1017` with the preamble, `triagecheck-10181435` without); with the preamble it scored 88.7% at 57 s per item against 85.8% / 50 s for always
2048 and 88.1% / 65 s for always 4096 (in-sample; held out on the hard 42, the top half scored
75.4% vs 70.1% for a random half). Live through a scratch gateway on the real model, the
decisions matched the offline ones and the real request reused the triage's cached prompt.
The triage costs about 1.2-1.3 s per triaged turn: the triage message follows the
conversation, so it is never cached, and this server reads new prompt slowly (part of the MoE
runs on the CPU); about half of it is fixed (chat formatting, one decode step). Wording
(2026-10-18, 115 probes): dropping the "[Routing check …]" preamble cut the message from 74
to 54 tokens (0.97 → 0.87 s median) with the same separation (AUROC on 4096-helps 0.67 vs
0.68, 4 vs 2 unusable answers); shorter wordings (32-39 tokens, ~0.55 s) made the model start
answering instead of rating (16-71 of 115 unusable) or separated worse (0.59). The threshold reflects bench probes, not real Kent traffic; telemetry and shadow
runs (later tasks) are how it gets recalibrated.

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
configured on the reference machine yet, so frontier falls back to local.

cheap is DeepSeek V4.1 Flash through LiteLLM's DeepSeek provider, with its own optional
credential (`deepseek_api_key`, set with `--set-deepseek-key` or `kent-admin set-deepseek-key`).
It is a placeholder (3.3.0): Kent-only, nothing routes to it, and without a key a call fails at
once and falls back to fast. The model id is unverified until a key is tested.

### 5.3 Fallback — Live

If a default, smart, cheap, frontier or auto call fails, the gateway answers from the local `fast`
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

No key or a forged key → 401; a Gent key on cheap/frontier, admin routes, a
query-string model or a routing header → 403. Covered by the gateway unit tests
(`tests/gateway`, 224) and 50 live tests with a canary upstream.

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
| Both | Hermes' built-in dangerous-command detector (tirith left Hermes in v0.21.6); no runtime package installs; one-shot and cron runs deny dangerous commands; yolo/oneshot re-entry, docker, managed-scope and credential paths denied; update checks off; secrets redacted | |

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
Squid instance (own account and config); the vendor `squid.service` is not used, and when Kent
installed the squid package it switches that service off (the package starts it, listening on
*:3128).

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
no apt source is added, so system updates cannot move them). Grafana's plugins are the
copies bundled in its package; its own plugin downloads and updates are turned off. Squid and base packages come
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

1. Add the Anthropic key (and the DeepSeek key once cheap has a job); tune `FALLBACK_TIMEOUT`.
2. default and smart are the local model thinking (3.3.0); on hardware too slow to think,
   point them at a cloud model instead, after taking them out of the Gent policy (the
   installer refuses a non-local Gent tier).
3. On bigger hardware, serve router/fast/default/smart from separate local models; shift
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
| Router misclassification | interim `auto` sends everything to default except frontier escalations; a failed classification stays local; decisions logged and reviewed in the digest |
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
