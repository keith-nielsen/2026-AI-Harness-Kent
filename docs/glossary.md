# Glossary

| Term | Definition |
|---|---|
| **Kent** | The persistent helper agent. Runs on the host OS as a systemd service. Named after the Earl of Kent in Shakespeare's *King Lear* — loyal, honest, competent, and willing to speak truth to power. Kent manages the estate: routing, stack lifecycle, frontier escalation, monitoring, template evolution, and the daily digest. |
| **Gent** | A project-scoped stack CEO agent. Each Gent runs in an isolated Docker container with its own database, credentials, and CrewAI workers. Derived from Kent's delegation of "a Gentleman" in Act III of *King Lear*; also evokes "agent" and Kent's disguise as Caius when operating under constrained authority. Identifier pattern: `gent-<uuid8>`. |
| **Human** | The operator. Unix user `enterprise`. Top authority in the system. Interacts via CLI, web UI (Grafana, Gitea), and conversation with Kent. |
| **Ent / Enterprise** | The Human's Unix user account. Serendipitously completes the naming trinity: **Ent**erprise → K**ent** → G**ent**. |
| **Fractal Stack** | A complete project-scoped runtime: one Gent (CEO) + N CrewAI workers + a `stack.db` + a Litestream sidecar, all in Docker. Called "fractal" because each stack is a self-similar copy of the template, capable of managing its own sub-tasks independently. |
| **Tier / T0–T3** | The four inference tiers. T0 Router (classify), T1 Fast (execute simple tasks), T2 Smart (reason and plan), T3 Frontier (cloud escalation for hard problems). |
| **Gateway** | The LiteLLM proxy at `localhost:4000`. Abstracts physical model endpoints behind logical names. All inference consumers hit the gateway, never Ollama directly. |
| **Frontier** | The cloud-based T3 tier. DeepSeek V4 Pro (primary, cost-effective) and Opus 4.7 (fallback, quality-critical). Used for escalations and nightly QA audits. |
| **Seed-from** | The mechanism for resuming work on an archived project. A new Gent is spawned with the latest template (including all global improvements), then domain-specific knowledge from the archived Gent is selectively imported into its `seed_context` table. |
| **Shared Learnings** | A table in each `stack.db` where Gent CEOs publish transferable knowledge. Kent polls this table, evaluates learnings, and either adopts them (propagating to the template and other Gents) or discards them with reasoning. |
| **Circuit Breaker** | The failure handling pattern. After retries exhaust at a given tier, escalation flows upward. If frontier also fails, the task halts, a Gitea issue is created, and the CrewAI checkpoint is preserved for human intervention. |
| **Daily Digest** | Kent's morning report to the Human. Covers inference usage, stack health, security events, pending approvals, and items needing attention. |
| **Output Gate** | The validation step after every inference call where `review_required=true`. The Smart model reviews the output and either passes it, requests a retry, or escalates. |
| **HMAC Chain** | The tamper-evident audit log. Each entry is HMAC-chained to the previous entry. Daily integrity checks detect any modification. Append-only, never rotated. |
| **Caius** | Kent's disguise name in *King Lear*. Used in architecture discussions to describe the conceptual relationship between Kent and Gents — a Gent is "Kent operating under constrained authority." Not used as a system identifier; Gent is the operational term. |
