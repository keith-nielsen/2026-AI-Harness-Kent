# Kent — Conformance Report

A living record of how the installed system measures up to the target design in
[`architecture.md`](architecture.md), as amended by the operator's decisions below.
Update it as work lands: add a dated entry under **History**, refresh the tables,
and move items between **Open** and **Closed**.

| | |
|---|---|
| Last assessed | 2026-09-28, v0.1.0-rc.2 (reference machine: Ubuntu 24.04, RTX 2060 SUPER 8 GB, 64 GB RAM) |
| Automated check | `python3 tests/conformance/conformance.py [--markdown FILE]` (operator, no root, read-only) |
| Result | **99 PASS · 0 FAIL · 6 INFO** |
| Test suites | 214 unit (gateway 145, kent_core 42, Gent CEO 12, install 15) · 46 live (`tests/live`) · 24 in-container Gent checks (`tests/gent/run_tools_selftest.sh`) · router probe |
| Manual validation | [`manual-validation.md`](manual-validation.md): operator runbook with prompts, commands, telemetry queries, expected outputs and a sign-off table |
| Control matrix | [`controls.md`](controls.md): each enterprise control with evidence, status and framework mapping |
| Rollback | Full purge-uninstall of all modules returns the host to the pre-install baseline (only non-Kent differences remain) |
| Install record | `~/.local/share/kent/install-records/` (`INSTALL-LOG.md`, snapshots, per-step logs) — local to the machine, not in the repo |

---

## 1. Decisions that amend the design

These are deliberate departures from `architecture.md`. Conformance is judged against the
design *with these amendments*; `architecture.md` itself will be revised to match.

| Topic | Original design (v2.3) | Decision (operator), now in architecture v3.0.0 |
|---|---|---|
| Inference engine | Ollama on a Unix socket | Ollama dropped. Local model = operator-run `llama-server` (llama.cpp) on 127.0.0.1:8080; not yet a managed service |
| Cloud model | DeepSeek + Anthropic | Claude only: smart and frontier → Claude Opus 5.5 (`claude-opus-5-5`) in prod; no DeepSeek |
| Local-first | ~95% local | End-state for later; cloud intelligence does the heavy lifting for now |
| Tier access | Gent CEO: fast + smart | Gents are **local-only** (router, fast). Anything harder is escalated to Kent |
| Kent's identity | Unix user `kent`, own service | Restored in architecture 3.1.0: Kent is the no-login `kent` account with its own commit-pinned Hermes (`/opt/kent-hermes`); humans use the `kent` command (group `kent-operators`); `~/ai-env` is untouched; an operator may run their personal Hermes on Kent's pinned install with their own home and gateway key. (From rc.1 to rc.2 Kent was a profile of the operator's Hermes, running as the operator.) |
| Service accounts | Some shared groups (`ollama`, `agentic-logs`) | Every service has its own `user:group`; no shared `kent:kent` |
| Gateway auth | LiteLLM virtual keys + DB | Single LiteLLM gateway, identity auth in `kent_gateway.py` (operator, kent, gent, gent-<id>, metrics); no PostgreSQL |
| Log shipping | Promtail | Grafana Alloy (apt package), journald → Loki |
| Git forge | Gitea | Gitea (kept) |
| Gent → Gitea | Gent commits under its own PAT | Gents have no Gitea access; Kent publishes each Gent's workspace to `kent/gent-<id>` |
| Firewall, AIDE, CVE scanning | Specified | Deferred: to be hardened later |
| Litestream sidecar | Per-Gent | Not used: stack data is archived on retire |
| Secrets layout | `/home/enterprise/secrets` | `/etc/kent/<service>/credentials` (root, `LoadCredential`); operator copies in `~/.config/kent` (0600) |

---

## 2. Conformance by design area

| § | Area | Status | Evidence |
|---|---|---|---|
| 1 | Entity hierarchy & access rules | ✅ | Gateway identities enforce tiers; Gents can't reach frontier, kent.db, other Gents, or host services |
| 1.3 / 8.1 | Frontier escalation path | ✅ | Gent → `shared_learnings` → `kent-poll-learnings` → frontier (Kent key) → Gent inbox → task resumes (sim: 3 escalations answered) |
| 2 / 4 | Four tiers, router classifies everything | ✅ | `auto` = LiteLLM complexity router on local model; live probe: easy→fast 8/8, hard→smart/frontier 8/8, injections in either direction changed nothing |
| 3 | Inference gateway, scoped keys | ✅ | no key/forged key → 401; Gent → smart/frontier → 403; per-Gent keys hot-loaded; 145 adversarial unit tests + 46 live tests (incl. a fixed `?model=` bypass) |
| 5 | Frontier tier, fallback | ⚠️ partial | Config and fallback to local fast verified (`FALLBACK_TIMEOUT`); **live Opus calls pending an Anthropic key** |
| 6 / 7.3 | CrewAI Gent CEO | ✅ | Kanban CEO: ordered tasks, escalation, capped retries, validation against files on disk, heartbeat, crash recovery, report + learnings |
| 7.2 | Kent duties | ✅ | Spawn/destroy, escalation relay, learnings review + template commits (including retired Gents), assessments, daily digest, nightly QA, audit ingest/anchor (5 user timers, all verified armed) |
| 8.2 | Circuit breaker | ✅ | Failed task or 3 escalations of one task → container stopped, Gitea issue, registry `paused`; `kent-gent resume` retries (tested live) |
| 8.3 | Nightly QA audit | ✅ (untested live) | `kent-qa-audit` timer 02:00; frontier review → `qa_audit_log` |
| 9 | Data architecture | ✅ | `kent.db` (operator), per-Gent `stack.db` (Gent-owned, Kent read-only via group), registry rows never deleted |
| 10 | Knowledge sharing & templates | ✅ | Judge → adopt/discard with notes; confident adoptions committed to `kent/stack-template`; new Gents inherit `LEARNINGS.md` (proven sim run 3) |
| 11 | Lifecycle | ⚠️ partial | spawn, pause, resume, restart (new image), archive/destroy ✅ · **seed-from archive not built** |
| 12 | Security (least privilege, isolation) | ✅ | Per-Gent UID, read-only rootfs, no caps, no-new-privileges, internal Docker network, only gateway + proxy reachable |
| 13 | Secrets | ✅ | `LoadCredential` for services; Gent key mounted read-only; nothing secret in env or repo |
| 14 | Unix users & groups | ✅ | 8 distinct service accounts; `gent-<id>` per Gent, removed on retire |
| 15 | Egress control | ⚠️ partial | Own Squid: GET/HEAD + CONNECT 443 to public addresses only · **POST allowlist, access-request flow, payload anomaly detection not built** |
| 16 | Firewall | ⏸ deferred | Only two narrow UFW rules on the Gent bridge (recorded, removed on uninstall) |
| 17 | Observability & alerting | ✅ | Prometheus (7 targets up), Loki via Alloy (gateway calls + Gent logs), Grafana dashboard; critical alerts → Gitea issues |
| 18 | Logs & HMAC audit chain | ✅ | Chain with per-chain journal anchors, ingest of gateway denials, installs and sudo; chain break → Gitea issue · `chattr +a` not applied (operator-owned file) |
| 18.4 | Security audit schedule (AIDE, pip-audit, trivy) | ⏸ deferred | — |
| 19 | systemd hardening | ✅ | Exposure scores 1.2–2.9 (all ≤ 3.0), `NoNewPrivileges` on all |
| 20 | Host / Docker partitioning | ✅ | Services on host under systemd; Gents in Docker only |
| 22 | Storage guard | ✅ | Spawning refused at ≥ 90% disk |

---

## 3. Verified end-to-end behaviour (simulation, 2026-09-27/28)

Gateway in `--config sim`: smart/frontier answered by an oracle fixture
(`tests/sim/oracle.py`) standing in for Opus while no API key exists.

| Run | Gent | What it proved | Kent's assessment |
|---|---|---|---|
| 1 | `7246f84e` kernel-check | Operator → Kent (Hermes) designed the crew and spawned it; live web research via the proxy; 3 escalations answered; crash recovery | 3/5 revise |
| 2 | `ba6f7125` uptime-note | Tiny tasks: 4 tasks, 0 retries, ~70 s; learnings judged (1 adopted → template commit) | 4/5 revise |
| 3 | `95c696df` loadavg-note | New Gent inherited the adopted learning (cross-pollination); uptake by the small model partial | — |
| — | `416681c4` breaker-test | Circuit breaker trip → resume → trip → archive | — |
| — | `f0dcecd6` final-smoke | Post-reinstall smoke on a fresh install | 5/5 accept |

---

## 4. Findings and fixes

Found by the simulation (S), rollback test (R) and conformance pass (G). All fixed unless marked.

| # | Finding | Fix |
|---|---|---|
| S1 | Later tasks started while an earlier one was blocked | Tasks wait behind a blocked predecessor |
| S2 | Validator saw truncated files and failed complete work | Head+tail excerpts, labelled |
| S4 | A Gent could spam escalations (frontier cost) | Budget 5 per Gent per 24 h |
| S5 | Adopted learnings go into every future Gent (injection spread) | Template content filter, even if the judge adopts |
| S6 | Scripts written without exec bit | `#!` files written 0750 |
| S8 | No way to move a Gent to a fixed image | `kent-gent restart` (recreate, state kept) |
| S9 | Escalations waited up to 5 min | Poll every minute |
| S11 | Interrupted task stuck `in_progress` forever | Requeued at CEO start |
| S12 | Content filter blocked inline code (false positive) | Pattern narrowed; tests both ways |
| S13 | Learning uptake by the small local model is partial | **Open**: idea: make "apply team knowledge" part of expected output |
| S14 | Gents re-propose already discarded learnings | **Open**: give the judge Kent's past verdicts |
| G1–G14 | Circuit breaker, pause/resume, archive-first destroy, heartbeat, chain-break alert, disk guard, digest sections + 24 h window bug | Implemented and tested |
| R1 | Gent base image not recorded → left after uninstall | Recorded when pulled; removed on uninstall |
| R2 | Purging Alloy left a dangling enable symlink | New manifest kind `enabled`: disabled before purge |
| R3 | Audit anchors had no chain identity → reinstall failed verification; an alert line could hide the latest anchor | Per-chain anchors; earlier chains reported |
| R4 | Fresh Gent install aborted silently (grep under `pipefail`) | Fixed; build failures now print the build log |
| E1 | Weekly `fstrim` stalled synchronous writes on the root SSD (~110 ms per write) | **Environment**: schedule for idle hours |
| R5 | After a reinstall without a reboot, the every-minute and every-5-minute timers went dormant (`OnBootSec`/`OnUnitActiveSec`), silently stopping escalations, learning review, the circuit breaker and audit ingest; the check only tested that timers were *listed* | Calendar schedules; the installer restarts timers; installer and conformance now require every timer to be armed |
| R6 | Learnings of a Gent destroyed before its final review were never reviewed | Poller reviews paused and archived Gents too; escalations of archived Gents expire (no frontier spend); paused ones wait |
| D1 | Documentation described the retired phase installer, Ollama and DeepSeek | Docs rewritten for the live harness (architecture v3.0.0, privilege map, dev notes, glossary, README); legacy installer, scripts and configs removed; CI rewritten |

---

## 5. Open items

| Item | Notes |
|---|---|
| **External security review (2026-09-28)** | [`audit/2026-09-28-external-security-review.md`](audit/2026-09-28-external-security-review.md): 1 Critical, 7 High, 9 Medium, 3 Low against CSA AI Guidelines, the Agentic AI Addendum and CCoP 2026. P0: sandbox or allowlist Kent's tools, remove its root-equivalent access, revoke the dev sudo grant |
| Anthropic key + live Opus calls | Then set `FALLBACK_TIMEOUT=120` (see warning in `install/services/litellm/install.sh`) and re-run conformance with `--config prod` |
| Dev-mode learning judge | Local judge adopted 5/6 generic learnings vs Opus 1/7: raise the bar or require operator approval until a key exists |
| Seed-from archive (§11.2) | Not built |
| Egress POST allowlist / access requests / anomaly detection (§15.2–15.4) | Not built |
| Key rotation (§12.1), per-key token budgets (§12.2) | Not built |
| Push learnings to running Gents (§10.1 A2A) | Not built (template path only) |
| Regulated-data gaps | Redaction/DLP before cloud calls, encryption at rest, backups/DR, off-host immutable logs, MFA/SSO: see [`controls.md`](controls.md) "Gaps before regulated data" |
| Firewall, AIDE, pip-audit/trivy, `chattr +a` | Deferred hardening |
| Layer-3 prompt-injection tests against Kent | Deferred while the agent sudo grant is active |
| Full conformance run on the kent-account layout (3.1.0) | Deferred by the operator. Module self-verification, non-root conformance checks and 215 unit tests pass; `kent-admin conformance` and the manual runbook (reference answers K1–K6) to be re-run and recorded |
| Hardened profile | Policy written and rendered (install-verified: tirith fails closed); not yet exercised in use |
| llama.cpp as a managed service | Deferred by decision |

---

## 6. How to re-assess

Manual, operator-run: [`manual-validation.md`](manual-validation.md) (sign-off table in §12). Automated:

```bash
sudo ./kent-admin conformance --markdown /tmp/conformance.md              # live system (root, read-only)
python3 -m pytest tests/gateway tests/kent_core tests/gent tests/install   # unit
LITELLM_BIN=<venv>/bin/litellm python3 -m pytest tests/live               # live gateway (ports 4099/9199)
tests/gent/run_tools_selftest.sh                                          # Gent isolation
python3 tests/live/router_probe.py --repeat 2                             # router stability/injection
```

Rollback test: `install/services/snapshot.sh take <name>` → `sudo ./uninstall.sh --purge` → `snapshot.sh take` → `snapshot.sh diff 00c-baseline <name>`.

---

## History

| Date | Change |
|---|---|
| 2026-09-28 | First report (v0.1.0-rc.1): all modules installed (litellm, prometheus, node_exporter, loki, alloy, grafana, gitea, kent-core, gent); full simulation; two full rollback cycles; 99 PASS / 0 FAIL / 6 INFO |
| 2026-09-28 | Simulated external security review against CSA Guidelines on Securing AI Systems (2024), Securing Agentic AI Addendum (2026) and CCoP 2026: see `audit/`. Findings open |
| 2026-09-28 | v0.1.0-rc.2: legacy installer and configs retired; docs rewritten for the live harness; control matrix and manual validation runbook added (runbook executed end to end on the reference machine, outputs recorded in it); findings R5 and R6 fixed; 99 PASS / 0 FAIL / 6 INFO; 214 unit + 46 live tests |
| 2026-09-28 | v0.1.0-rc.3: Kent's own account and pinned Hermes (architecture 3.1.0), SearXNG, root install/uninstall/kent-admin, conformance ported with new checks (incl. keyless web fallback off); sim-routed gateway flavour; failure causes logged; Kent routing dashboard. 220 unit tests pass; **conformance not yet re-run for rc.3** (last full run: rc.2) |
| 2026-09-28 | Architecture 3.1.0 implemented (uncommitted at time of writing): `kent` account and pinned Hermes, `kent` command + `kent-exec`, system timers, Gent brokers incl. `kent-gent-ctl`, SearXNG module and Gent search bridge, lab/hardened profiles, `install.sh` / `uninstall.sh` / `kent-admin`, conformance check ported. Full conformance run deferred |
| 2026-09-28 | tirith 0.4.2 pinned (SHA-256; release signature checked) and enforced through Kent's managed scope; Kent's SearXNG runs as the new `kent-searxng` account instead of root (fixes the uid/gid 977 overlap with the image's internal user); operator's personal SearXNG container retired |
