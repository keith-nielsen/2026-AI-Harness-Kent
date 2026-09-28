# Glossary

| Term | Definition |
|---|---|
| **Kent** | The persistent agent. Its own pinned Hermes install running as the `kent` system account, plus Kent's tools and timers. Operators reach it with the `kent` command. Supervises the estate: talks with the operator, spawns and assesses Gents, relays escalations, reviews learnings, curates the template, writes the daily digest. Named after the Earl of Kent in *King Lear*: loyal, honest, competent, willing to tell the operator they are wrong. |
| **Gent** | A project team: a CEO loop driving CrewAI workers, in its own Docker container, under its own Unix user (`gent-<id>`), with its own database, gateway key and Gitea repo. Local model only. Identifier: 8 hex characters. |
| **Human / Operator** | A person using the system; top authority. A member of the `kent-operators` group, working as themselves through the `kent` command; never logs in as `kent`. |
| **`kent` command** | The operator's interface (`/usr/local/bin/kent`): chat, one-shot questions, status, digest, audit, Gent handover (`kent new`) and results (`kent gent export`). Crosses into the kent account only through `sudo -u kent kent-exec`, which validates and audits each call. |
| **Profile** | `lab` (development: Kent has a general shell as the kent account) or `hardened` (restricted toolsets and deny rules). Set in `/etc/kent/profile`; switched with `sudo ./kent-admin profile`. |
| **Managed scope** | Hermes' administrator-owned config layer (`HERMES_MANAGED_DIR`, here `/etc/kent/hermes/managed`), merged over Kent's own config; Kent cannot change it. |
| **Gateway** | The LiteLLM proxy at `127.0.0.1:4000`. Every model call goes through it; it authenticates identities (`kent_gateway.py`), classifies `auto` requests and routes them to a tier. |
| **Tier** | Logical model name: `router` (classifies), `fast` (local, routine), `smart` and `frontier` (Claude Opus 5.5 in prod), `auto` (router decides). |
| **Router** | The classification step behind `auto` (LiteLLM complexity router on the local model): fast, smart or frontier; unclassifiable requests go to frontier. |
| **Fallback** | When a cloud tier fails or exceeds `FALLBACK_TIMEOUT`, the gateway answers from the local `fast` tier. |
| **Identity** | Who a gateway key belongs to: `operator`, `kent`, `gent`, `gent-<id>`, `metrics`. Determines which tiers and routes it may use. |
| **Escalation** | A Gent asking Kent for help it can't get locally: written to its `shared_learnings`, answered by Kent on frontier into the Gent's inbox, then the task resumes. Budget: 5 per Gent per 24 h. |
| **Inbox** | `/var/lib/kent-gent/stacks/<id>/inbox`, written by Kent, mounted read-only in the Gent: escalation answers and resume tokens. |
| **Shared learnings** | Transferable lessons a Gent publishes when it finishes. Kent judges each one; confident adoptions that pass the content filter are committed to the template. |
| **Stack template** | The Gitea repo `kent/stack-template`. Its `LEARNINGS.md` is copied into every new Gent and added to its agents' backstories. |
| **Content filter** | Check applied before a learning is committed to the template: blocks instruction-like text, shell substitution, URLs, credentials and encoded blobs. |
| **Assessment** | Kent's frontier-tier judgement of a finished Gent's work (usefulness 1–5, accept/revise/reject), stored in `gent_assessments`; the workspace is published to `kent/gent-<id>`. |
| **Circuit breaker** | Automatic halt of a Gent that cannot make progress (failed task, or one task escalated 3 times): container stopped, Gitea issue opened, registry `paused`; `kent-gent resume` retries. |
| **Heartbeat** | Timestamp the Gent CEO updates on every agent step; the digest flags tasks with no heartbeat for 15 minutes. |
| **Archive** | Default outcome of `kent-gent destroy`: the Gent's data kept read-only under `/var/lib/kent-gent/archive/<id>`; its account and key removed. |
| **Seed-from** | *(Planned)* Spawning a Gent that imports domain knowledge from an archived one into `seed_context`. |
| **Module** | One installable component under `install/services/<name>/` with `install.sh`, `uninstall.sh` and a manifest. |
| **Manifest** | `/var/lib/kent-install/manifest/<module>`: everything a module created (accounts, paths, units, packages, rules), used by uninstall to remove exactly that. |
| **Audit chain** | `/var/lib/kent/audit/hmac_chain.log`: HMAC-chained, append-only record of Kent's actions, denials, installs and sudo use; anchored daily in the journal. |
| **Anchor** | A journal entry `chain=<id> count=N last=<hmac>` recording the chain's length and head, so truncation or rewrites are detectable. |
| **Digest** | Kent's daily report (07:00): gateway use, router decisions, denials, service health, audit status, Gents, learnings, assessments, template changes, alerts. |
| **Oracle** | Test fixture (`tests/sim/oracle.py`) standing in for the cloud model in simulations: requests are queued and answered by a person or a Claude Code session. |
| **Conformance check** | `tests/conformance/conformance.py` (run with `sudo ./kent-admin conformance`): read-only verification of the installed system against the design. |
| **IQ / OQ / PQ** | Installation / operational / performance qualification: the formal validation stages Kent's checks map to (see `controls.md`). |
| **Caius** | Kent's disguise in *King Lear*; used informally for "Kent operating under constrained authority", i.e. a Gent. Not a system identifier. |
