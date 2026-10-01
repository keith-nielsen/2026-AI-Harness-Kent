# External Security Review — Kent Reference Agentic AI Stack

| | |
|---|---|
| **Engagement** | Independent security review of a reference architecture and proof-of-concept implementation (simulated external audit) |
| **Target** | Kent v0.1.0-rc.2 — repository commit `97159d9` and the live reference installation (Ubuntu 24.04, single host) |
| **Review date** | 28 September 2026 |
| **Review basis** | Singapore CSA guidance and codes, and well-published global standards (§1.3) |
| **Method** | Document review, configuration and source inspection, live testing on the reference host, dependency vulnerability scan |
| **Opinion** | **Strong technical baseline for a proof of concept; not fit for deployment as, or connected to, Critical Information Infrastructure (CII) or regulated workloads until the Critical and High findings are remediated and the organisational controls are established.** |

> This is a simulated review produced for the project's own assurance. It is not an audit
> under section 15 of the Cybersecurity Act 2018, is not performed by a Cyber Trust Mark
> certified firm (CCoP 2026 cl. 3.3.3), and does not certify compliance with any standard.

---

## 1. Scope, basis and method

### 1.1 In scope
- The reference architecture: `docs/architecture.md` v3.0.0, `docs/controls.md`, `docs/privilege-map.md`, `docs/manual-validation.md`, `docs/conformance.md`.
- The implementation: `install/services/*` (9 modules), `templates/app/gent/*`, `install/services/kent-core/bin/*`, `install/services/litellm/kent_gateway.py`, configs, tests, CI.
- The live reference host as installed on 28 Sep 2026, including the Hermes `kent` profile and the operator-run local model server.

### 1.2 Out of scope
Physical security; the operator's other workloads on the host; Anthropic's service; Hermes Agent, CrewAI, LiteLLM and other third-party code beyond their configuration and use here. OT-specific requirements (CCoP §10), domain controllers/Kerberos (cl. 5.4, 7.1.3, 8.2.4) and wireless (cl. 5.8) are not applicable to this system.

### 1.3 Criteria (primary sources read in full)

| Source | Nature | Used as |
|---|---|---|
| CSA, *Guidelines on Securing AI Systems*, 15 Oct 2024 | Voluntary guidance | AI lifecycle criteria (Guidelines 1.1–5.1, four-step risk approach, Annex A) |
| CSA, *Securing Agentic AI — Addendum to the Guidelines and Companion Guide on Securing AI Systems*, v1.0, 17 Jun 2026 | Voluntary guidance | Agentic controls 1.1, 2.1–2.11, 3.1–3.4, 4.1–4.5; OWASP ASI T1–T15 |
| CSA, *Cybersecurity Code of Practice for CII 2026* (Release 1), issued 29 Jul 2026 | Mandatory for CIIOs (s.35A(6) of the Act) | Clause-by-clause criteria, §2–§12 and Annex A |
| CSA press release, 22 Jul 2026 (Minister Josephine Teo, OT Cybersecurity Expert Panel Forum 2026) | Policy direction | Threat context (Frontier AI shortening exploitation windows), forthcoming Cloud CCoP, technical guidance on adversarial attack simulation, penetration testing and threat hunting |
| Stephenson Harwood, *Singapore's CSA issues updated CCoP for CII* | Secondary (legal summary) | Interpretation: board accountability, CTM Tier 5, interconnected-systems scope, Commissioner's 30-day reporting powers |
| Zavior, *CCoP 2026 for Singapore CII Owners* | Secondary (vendor summary) | Clause/date cross-check; incident-reporting timelines under S 678/2025 (not independently verified against the gazetted regulation) |
| OWASP Top 10 for LLM Applications / Agentic threats (ASI), MITRE ATLAS, NIST AI RMF, ISO/IEC 42001, ISO/IEC 27001:2022, NIST SP 800-53r5 | Global standards referenced by the CSA documents | Cross-reference only |

**Observation on the sources.** The CCoP 2026 text contains no AI-specific clause (verified by
search of the issued PDF). CSA's AI obligations reach CII owners through the voluntary AI
Guidelines and the Agentic Addendum, while CCoP's general clauses (access, logging, supply
chain, cloud, outsourcing, testing) apply to AI components exactly as to any other system. This
review therefore applies both: the Addendum for *how* to secure the agentic system, the CCoP for
*what a CIIO must evidence* if Kent is deployed as CII or as a CII interconnected system
(cl. 1.2.6).

**Key dates (CCoP 2026).** Effective 29 Jul 2026; most new obligations by 29 Jul 2027
(3.1.4–3.1.5, 3.2.1–3.2.5, 4.1.2–4.1.4, 6.2.4, 7.3.1–7.3.2, 7.3.7, 7.3.10, all of §12);
Cyber Trust Mark Advocate (Tier 5) by 31 Dec 2027 (3.3.1–3.3.3).

### 1.4 Method and evidence
- Read all project documentation and the relevant source.
- On the live host: inspected unit hardening, accounts, listeners, file modes, Hermes configuration and CLI behaviour, gateway and proxy logs in Loki, DNS resolver, time sync, patching and encryption status.
- Ran `pip-audit` against both hash-locked dependency sets (scratch environment).
- Relied on the project's own recorded test results where re-execution was impractical (e.g. the 46 live gateway tests), and spot-checked them.

### 1.5 Severity scale
**Critical**: exploitable now to gain host-level control or bypass a core control. **High**: material gap against a mandatory CCoP clause or a baseline Addendum control. **Medium**: gap with compensating controls or limited exposure. **Low**: hygiene or documentation. **Org**: organisational obligation the software cannot meet by itself.

---

## 2. Executive summary

Kent is a well-engineered reference for a self-hosted agentic stack. It shows several patterns this reviewer would want to see copied, and they are genuinely implemented and tested:
- identity-based model access with deny-by-default request shapes and adversarial tests;
- per-service least-privilege accounts with measured systemd hardening;
- sandboxed Gent containers on an internal network with a restrictive egress proxy;
- a tamper-evident audit chain with external anchors;
- hash-pinned supply chain and manifest-driven, verified-reversible installation;
- circuit-breaker human oversight, cost budgets and a knowledge-poisoning filter;
- a living conformance record and a manual validation runbook.

The review nonetheless finds **1 Critical and 7 High** findings:

1. **(Critical) The agent with a shell is effectively root.** Kent's Hermes terminal runs on the host as the operator. The operator is in the `docker` group (root-equivalent), holds passwordless sudo for Gent spawn and destroy, and, on the reference host, an active development grant over user-writable scripts. Hermes' approval gate can be bypassed with `-z`, including by a nested invocation from Kent's own shell. This contradicts Addendum controls 2.7, 2.8 and 4.4 and CCoP cl. 3.7.1(b), 5.2.1(a) and 5.3.1.
2. **(High) Untrusted content reaches that agent's context.** Gent output and web-derived text are labelled as untrusted, but are still fed to the same tool-capable model. There are no input guardrails, and the Kent-level injection tests were deferred.
3. **(High) No documented risk assessment.** There is no threat model, taint trace, autonomy-level assessment or risk register (CCoP 3.4; AI Guideline 1.2; Addendum 1.1 and §4.1).
4. **(High) Detection and log retention fall short of CCoP §6.** Logs are kept 30 days against the required 12 months, can be deleted locally, and DNS and firewall logs are not collected. There is no anomaly baseline or alerting, no threat hunting and no threat intelligence.
5. **(High) Privileged human access lacks MFA and individual accountability.** The Grafana and Gitea admin accounts are shared.
6. **(High) No response or recovery capability.** There are no backups, BCP/DRP, incident response plan, crisis communications or exercises (CCoP §7–§8).
7. **(High) Cloud frontier use lacks the controls CCoP 3.9–3.10 require.** There is no DLP or redaction before prompts leave the host, and no documented cloud risk assessment or outsourcing terms.

Treat these as the gating items. Many CCoP obligations (§3.1–3.3, §7.3, §9) are organisational: a deploying CIIO must meet them, and the reference should ship templates that make them easy.

**Counts:** 1 Critical · 7 High · 9 Medium · 3 Low · 3 Org/Informational.

---

## 3. Strengths observed (credited)

| Area | Evidence | Criteria satisfied |
|---|---|---|
| Model access control | `kent_gateway.py`: per-identity policy, deny-by-default request shape, constant-time key compare; 145 unit + 46 live adversarial tests incl. a found-and-fixed `?model=` bypass | Addendum 2.6, 2.7; CCoP 5.1.1–5.1.2, 5.2.1(a) |
| Unique non-human identities | `gent-<id>` Unix user + gateway key + Gitea repo per Gent; keys hot-revoked on destroy | Addendum 2.6 ("treat agents as non-human identities", unique identifiers) |
| Execution sandboxing | Gents: read-only rootfs, `--cap-drop ALL`, `no-new-privileges`, pids/mem/CPU limits, internal network; 24/24 in-container isolation checks | Addendum 2.8, 2.9 (code execution in isolated containers); CCoP 5.5 |
| Egress control | Own Squid: GET/HEAD + CONNECT 443 only; private/loopback/link-local/CGN denied; proxy logs in Loki (747 lines/24 h) | CCoP 6.1.2(c) web proxy logs; Addendum 2.6 (partial) |
| Service least privilege | 8 distinct service accounts; systemd exposure 1.2–2.9; `LoadCredential` secrets | CCoP 3.7.1(b), 5.2.1(a), 5.9; Addendum 2.3 (no hardcoded secrets) |
| Tamper-evident audit | HMAC chain, per-chain journal anchors, truncation/tamper detection demonstrated; chain break → issue | Addendum 4.3 ("immutable, tamper-evident audit logs"); CCoP 6.1.4(b) (partial) |
| Supply chain pinning | SHA-256 pinned binaries (Gitea GPG-verified), `--require-hashes` locks, digest-pinned base image | AI Guideline 2.1; Addendum 2.1 ("validate package hashes"); CCoP 5.12.3 |
| Change control of installs | Manifests; exact uninstall; two verified rollbacks to baseline; CI | CCoP 3.8.1 (technical part) |
| Human oversight & bounds | Circuit breaker (halt + issue + resume), escalation budget, per-project effort caps, disk guard | Addendum 3.1, 4.3 ("circuit-breakers that freeze propagation"), 4.4 |
| Knowledge-poisoning defence | Judge + threshold + content filter + audit + revertible commits | Addendum 2.11 (memory reconciliation); OWASP ASI T1 |
| Router robustness | Injection probe: 0/16 decision changes | Addendum 3.2 (adversarial evaluation) |
| Vulnerability disclosure | `SECURITY.md` with private reporting | AI Guideline 4.4; Addendum 4.5 |
| Validation discipline | Automated conformance (99 checks), manual runbook with expected outputs, findings log | AI Guideline 3.3 (release responsibly) |

---

## 4. Findings

Each finding: severity · requirement references · evidence · risk · recommendation.

### F-01 Critical — Kent's agent runtime has root-equivalent privilege without enforced human approval

**Requirements:** Addendum 2.7 ("do not grant admin privileges to agents… restrict autonomy using policy constraints… whitelist of commands"), 2.8 ("do not grant admin or sudo privilege by default"; "block all inward and outward network access by default"), 2.9, 4.4 ("human approval for any high-risk or irreversible actions"); CCoP 3.7.1(b) least privilege, 5.2.1(a), 5.3.1(a)–(b) (privileged access only from hardened environments), 5.9.4.

**Evidence (live host):**
- `hermes -p kent config show`: Terminal *Backend: local*. Kent's shell runs directly on the host as the operator.
- `id`: operator ∈ `docker`. Control of the Docker socket is equivalent to root.
- `/etc/sudoers.d/91-kent-gent`: operator may run `kent-spawn-gent`/`kent-destroy-gent` as root without a password (argument-validated; acceptable in isolation).
- `/etc/sudoers.d/90-kent-agent` **ACTIVE**: passwordless root for every `install/services/*.sh` in a repository the operator can write. This is equivalent to unrestricted root for anything running as the operator, Kent included.
- Approval behaviour depends on how Hermes is started (verified in the source of the pinned release during remediation):
  - `hermes chat -q` (single-query mode, used by the project's runbook and simulations) **denies** dangerous commands by default (`approvals.single_query_mode: deny`);
  - `hermes -z/--oneshot` sets `HERMES_YOLO_MODE=1`, which **bypasses all approvals**.
  - Kent's own terminal can start a nested `hermes -z …`, which escapes the approval gate.
  - *Correction (2026-09-28):* the first issue of this report attributed the bypass to the runbook's single-query mode; it applies to `-z` only.

**Risk:** Any successful prompt injection (F-02) or model error in Kent becomes arbitrary host compromise: secrets, audit chain, all Gents. This is Addendum §3's "rogue actions" at the highest capability level (System Management + Code Execution). It is also OWASP ASI T2 (tool misuse), T3 (privilege compromise) and T11 (unexpected RCE).

**Recommendation:**
1. Revoke the development grant on any host that is not a disposable lab. Never use it where Kent runs unattended.
2. Run Kent's tools in a sandboxed Hermes backend (container or remote), not `local`. Alternatively, move Kent to its own account with no `docker` group membership, exposing only a narrow, policy-checked socket proxy or the existing root-owned spawn/destroy tools.
3. Replace free-form shell with an allowlisted tool surface (`kent-gent`, `kent-audit`, `kent-digest`, read-only queries), enforced outside the model.
4. Never run Kent with `-z` or `HERMES_YOLO_MODE` except in test harnesses. Pin managed deny rules against nested `hermes -z`/`--yolo`. Require interactive approval for spawn, destroy, publish and template commits (see F-17).
5. Record the autonomy level (Addendum Table 4: Kent ≈ Level 3) and the resulting controls in the risk register (F-03).

### F-02 High — Untrusted data is not isolated from a tool-capable agent's control flow

**Requirements:** Addendum 2.9 ("decouple data processing flow from control flow", CaMeL), 4.1 (input guardrails for indirect prompt injection; sanitise inter-agent messages), 1.1 (taint tracing); AI Guideline 4.1; OWASP LLM01 / ASI T6, T12.

**Evidence:**
- Gent-authored text reaches Kent's model context through several paths: `kent-gent status` (learning summaries, error text), assessment dossiers, the digest, and web research embedded in Gent workspaces.
- Mitigations exist: labelling (`untrusted-gent-text=`), the SOUL instruction to treat Gent output as data, judge prompts that frame input as data, and the template content filter.
- Prompt K4 in the runbook shows Kent *states* the correct policy. This is a behavioural statement, not an enforced control.
- Kent-level (layer-3) injection tests are deferred (`docs/conformance.md` §5).

**Risk:** With F-01, a single malicious web page read by a Gent can become an instruction executed on the host by Kent. The path runs: the Gent summarises the page, the summary becomes a learning or report, and Kent reads it.

**Recommendation:**
1. Produce a taint trace (Addendum Fig. 9) for all Gent → Kent flows.
2. Route tainted content only through a *quarantined* model call with no tools (dual-LLM pattern), returning structured, schema-validated fields to Kent.
3. Add input guardrails (e.g. an injection classifier) at the tainted boundaries.
4. Complete the layer-3 tests in a sandboxed profile, and add them to CI with a stubbed model.

### F-03 High — No documented risk assessment, threat model or risk register

**Requirements:** CCoP 3.4.1–3.4.5 (framework, methodology incl. threat modelling, risk register with the 8 listed fields, residual-risk thresholds); AI Guideline 1.2 and the four-step approach (§3.2); Addendum §4.1 Step 1 (autonomy level, threat modelling, capability-centric risks), Step 4 (residual risk; periodic re-evaluation), control 1.1.

**Evidence:** `docs/controls.md` is a control matrix (implementation and evidence) and `docs/conformance.md` a findings log. Neither records risk scenarios, likelihood/impact ratings, owners, treatment plans or residual risk. No autonomy-level or capability assessment exists for Kent or Gents.

**Recommendation:**
1. Publish `docs/risk-assessment.md`, containing:
   - a STRIDE/MAESTRO threat model per workflow;
   - autonomy levels (Kent ≈ Level 3, Gent CEO ≈ Level 2 with ReAct workers);
   - a capability inventory per the Addendum taxonomy (Kent: Planning, Agent Delegation, Tool Use, Code Execution, System Management, File & Data Management; Gents: Internet & Search, Code Execution, File & Data Management);
   - a risk register in the cl. 3.4.4 format;
   - a risk-acceptance section for deployers.
2. Re-assess after changes to workflows, capabilities or autonomy.

### F-04 High — Log coverage, retention and protection below CCoP §6.1

**Requirements:** CCoP 6.1.1 (all access attempts and activities; network connections), 6.1.2(a)–(d) (firewall, DNS, web proxy, NIDS logs), 6.1.4(a)–(e) (consistent time, protection against modification/deletion, **12-month retention**, analysable structure, retention policy); Addendum 4.3.

**Evidence:**
- **Retention:** Loki 30 days (`configs/loki.yaml`, `retention_period: 720h`); Prometheus 30 days / 5 GB; journald at defaults (≈251 MB on disk).
- **Protection:**
  - Logs sit on the same host, in stores writable by their service accounts and by root.
  - The audit chain file is operator-owned and deletable.
  - Its journal anchors share the journal's rotation.
- **Collection:**
  - Collected: web proxy logs (✓), gateway calls and denials (✓).
  - Not collected: DNS resolver logs, UFW/firewall logs, NIDS (none deployed).
- **Time:** NTP synchronised (✓).

**Recommendation:**
1. Set retention to ≥ 400 days (or the sector requirement) for security-relevant streams.
2. Ship logs off-host to write-once storage (object lock or a SIEM), including audit anchors.
3. Enable resolver query logging and UFW logging, and ship both.
4. Consider a host NIDS on the Gent bridge.
5. Write the log retention policy (6.1.4(e)).

### F-05 High — Detection is observational, not alerting; no threat hunting or threat intelligence

**Requirements:** CCoP 6.2.1 (monitor, collect, analyse and correlate *all* cybersecurity events; trigger response), 6.2.2 (IOC scanning, normal-activity baseline, alerts on deviations), 6.2.3 (annual review), 6.2.4 (facilitate CSA-supported detection), 6.3 (threat hunting every 24 months), 6.4 (CTI intake and sharing with the Commissioner), 12.7 (interconnected systems); Addendum 4.3 (behavioural profiling, real-time alerting on anomalies or inactivity); AI Guideline 4.2 (drift/anomaly).

**Evidence:**
- Present: dashboards, a daily digest, and Gitea issues for two conditions (circuit breaker, chain break).
- Absent:
  - Grafana/Loki alert rules and a paging channel;
  - IOC matching;
  - a baseline of normal gateway, egress or tool-use behaviour;
  - a CTI feed or threat-hunting procedure.

**Recommendation:**
1. **Alert rules**: Loki ruler and Grafana alerting on:
   - denial spikes;
   - unknown identities;
   - new egress domains;
   - per-identity token or call anomalies;
   - failed units;
   - stale Gent heartbeats;
   - audit-chain gaps;
   - routing-mix drift.
2. **IOC scanning** of proxy and DNS logs.
3. **Baselines** documented and reviewed yearly.
4. **CTI**: document how a CIIO feeds threat intelligence into these rules and how it would facilitate CSA sensors (6.2.4). The Loki and Prometheus HTTP APIs already make export straightforward.

### F-06 High — Privileged human access lacks MFA and individual accountability

**Requirements:** CCoP 5.3.1(c)–(f) (privileged account inventory, MFA, no sharing, break-glass controls), 12.3.1–12.3.3, 5.1.4 (session timeout), 5.2.1(c) (no shared accounts), 5.2.2 / 12.2.3–12.2.4 (annual account review); Addendum 2.6; ISO 27001 A.8.5.

**Evidence:**
- **Shared admin accounts:** Grafana `admin` and Gitea `kentadmin` are single shared local admins.
- **No MFA anywhere:** passwords are the only factor for these accounts and for `sudo`.
- **Privileged inventory:** covered in part by `docs/privilege-map.md`.
- **Undocumented:** session timeout policy, account-review procedure, break-glass procedure.

**Recommendation:**
1. **Individual identities** for each human, with SSO/OIDC and MFA for Grafana and Gitea.
2. **Stronger sudo:** MFA for sudo (e.g. PAM U2F), or privileged access through a hardened jump context.
3. **Break-glass**: a procedure with post-use review.
4. **Accounts and sessions:**
   - session timeouts configured explicitly;
   - an annual account review script listing Kent accounts, keys and Gitea tokens.

### F-07 High — No backup, continuity, incident response, crisis communication or exercise capability

**Requirements:** CCoP 7.1.1–7.1.8 (CIRP with CIRT, reporting, forensics, RCA), 7.2 (crisis comms), 7.3.1–7.3.10 (exercise plan with MSEL; annual scenario exercises incl. interconnected systems), 8.1.1–8.1.5 (offline, protected, tested backups), 8.2.1–8.2.5 (BCP/DRP with RTO/RPO); AI Guideline 3.2; Addendum 2.5 (memory snapshots, versioned rollback).

**Evidence:**
- **Backups:** none of kent.db, the audit chain, Gitea (template and records), Grafana state or Gent archives. Gent archives are kept only on the same host.
- **Plans:** no IR plan, BCP, DRP or exercise plan.
- **Partial substitutes:** the manual runbook and the verified rollback prove *installation* recoverability, not *data* recoverability.

**Recommendation:**
1. **Backup and restore module:**
   - encrypted, offline or immutable copies with restore tests;
   - scope: kent.db, the audit chain and secret, Gitea, Grafana, Gent archives, configuration.
2. **Templates** for a CIRP (including AI-specific playbooks: prompt injection, rogue agent, poisoned template, key compromise, model tampering), crisis communications, and BCP/DRP with RTO/RPO.
3. **Exercise plan with MSEL:**
   - STRIDE-derived scenarios covering Kent and Gents as interconnected systems;
   - a first tabletop exercise, e.g. "poisoned learning propagates to all Gents".

### F-08 High (for regulated or CII data) — Cloud frontier use without data controls or cloud/outsourcing governance

**Requirements:** CCoP 3.9.1–3.9.4 (remain accountable; inform the Commissioner; cloud risk assessment formally accepted and submitted within 30 days; provider appoints a person in Singapore for service of process), 3.10.1–3.10.5 (outsourcing oversight, contractual access, incident-reporting and audit rights); the forthcoming Cloud CCoP (press release 22 Jul 2026); Addendum 4.1/4.2 (PII detection on inputs and outputs, e.g. Presidio), enterprise DLP (§4.2 "Implementing Controls at Enterprise-scale"); AI Guideline 2.1 (supplier standards); PDPA obligations for personal data.

**Evidence:**
- The router can send any Kent or operator prompt to Anthropic (smart/frontier).
- There is no redaction or DLP step, and no data classification gate.
- There is no documented provider risk assessment, zero-retention configuration or contractual terms. `docs/controls.md` itself lists DP-3/DP-4 as gaps.

**Recommendation:**
1. **DLP at the gateway:** a pre-call guardrail that detects and redacts PII and secrets, and blocks classified content from cloud tiers (a LiteLLM guardrail hook or a `kent_gateway` pre-call).
2. **Data classification policy** deciding which data may leave the host.
3. **Provider governance:**
   - a provider risk assessment template aligned with cl. 3.9.3;
   - outsourcing terms per cl. 3.10.3: access, incident notification, audit rights;
   - zero-data-retention settings.
4. **Local-only mode** for regulated data, documented as the default for CII use until the above is complete.

### F-09 Medium — AI model supply-chain integrity not controlled

**Requirements:** AI Guideline 2.1 (supply chain), 2.2 (model selection trade-offs), 2.3 (track, authenticate and version models), 4.3 (secure updates); Addendum 2.1 ("do not use LLMs from unknown or untrusted sources", review model cards, scan models), 2.4 (model/agent cards, data lineage); CCoP 5.12.3 (verify integrity before use), 3.8.1 (change management), 5.9.1(f) (software applications baseline).

**Evidence:**
- **World-writable model file:** the active model `Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf` is mode `0777`.
- **Shared, unvetted directory:** the model directory also holds unrelated community models.
- **Unrecorded alternate model:** the launcher offers an alternate "uncensored" model set.
- **No integrity or provenance control:** there is no pinned hash, load-time verification, provenance record or model card for the local model.
- **Unmanaged runtime:** `llama-server` runs under the operator account, outside Kent's install manifests and change control.

**Risk:** A tampered or backdoored model (OWASP ASI T1/T7; MITRE ATLAS "ML supply chain compromise") would serve every tier, including the router, whose decisions gate cloud spend and Gent escalation.

**Recommendation:**
1. **Managed model module:** a dedicated `llama` account and unit, with a model directory owned by root:llama and mode 0640.
2. **Integrity and provenance:** a SHA-256 manifest for approved models, verified at start; provenance and model-card records in `versions.env` or `models.lock`.
3. **Change control:** swaps go through the conformance check.
4. **Model separation:** exclude unapproved or "uncensored" models from the served set.

### F-10 Medium — Known-vulnerable dependency in the Gent image; no scanning or SBOM in the pipeline

**Requirements:** CCoP 5.14.1–5.14.3 (identify, track and remediate vulnerabilities; annual assessment), 5.10.1 (patch management incl. compensating controls), Annex A (vulnerability assessment); press release (Frontier AI shortens the exploitation window); AI Guideline 2.1 (SBOM, vulnerability databases); Addendum 2.1 (SCA, pip-audit).

**Evidence:**
- **Gent image:** `pip-audit` on `install/services/gent/image/requirements.lock` reports **chromadb 1.1.1** with CVE-2026-45829 (pre-authentication code injection), CVE-2026-45833 (code injection), CVE-2026-45830 (cross-tenant data access) and CVE-2026-45831 (RBAC scope bypass). No fixed version is listed.
- **Exposure is currently latent:** Gents run no Chroma server and CrewAI memory is not enabled.
- **Gateway:** the gateway lock is clean.
- **Pipeline gaps:** CI has no SCA, container scan or SBOM.

**Recommendation:**
1. **Remediate chromadb:**
   - remove chromadb from the image if CrewAI allows (optional-dependency or extras review);
   - otherwise record a compensating-control rationale (no server, memory disabled, network-internal) in the risk register;
   - track the upstream fix.
2. **CI scanning:** add `pip-audit` for both locks and `trivy` for the image, failing on High or Critical without a waiver.
3. **SBOMs:** generate CycloneDX SBOMs per release and attach them to GitHub Releases.

### F-11 Medium — No rate limits or token budgets at the gateway

**Requirements:** Addendum 3.1 (rate limits per agent/session; time and token limits; limits on agent interactions); OWASP LLM10; CCoP availability objectives (§8).

**Evidence:**
- **Present:** Gent escalation budget (5 per 24 h), CrewAI `max_iter`/`max_tokens` caps, and container limits.
- **Absent:** per-identity RPM/TPM or budget limits in the LiteLLM configuration. The Kent and operator identities are unbounded against the cloud tier.

**Recommendation:** Per-identity RPM/TPM and daily token budgets enforced in `kent_gateway` (or LiteLLM rate-limit settings), with alerts at 80% (F-05).

### F-12 Medium — Gent egress is open to any public domain

**Requirements:** Addendum 2.6 ("whitelist approach for outward network access… protective DNS"), 2.1 (structured retrieval APIs instead of scraping; prioritise verified sources); CCoP 12.4.3 (monitor and block unauthorised outbound connections), 5.6.2; design doc §15.2–15.4 (POST allowlist and access requests, Planned).

**Evidence:**
- **Controls in place:** GET/HEAD and CONNECT:443 to *any* public destination, logged; private ranges denied.
- **Exfiltration channel remains:** URL paths and query strings. There is no payload/URL-length anomaly check and no domain allowlist per project.
- **Search method:** web search scrapes an HTML endpoint rather than a structured API.

**Recommendation:**
1. **Egress policy:** per-Gent domain allowlists declared in `project.yaml`, with Kent approving additions (the §15.4 access-request flow).
2. **Protective DNS** for the proxy.
3. **Anomaly rules:** alert on long URLs and query strings, and on first-seen domains.
4. **Structured search:** a structured search API with source prioritisation.

### F-13 Medium — Host baseline controls not established by the reference

**Requirements:** CCoP 5.9.1–5.9.3 (documented security configuration baselines incl. malware protection, annual review), 5.10 (patch management), 5.13.3(a) (data at rest), 5.6.1 (network access rules and review), 11.2.1 / Annex A (DNSSEC validation — mandatory only for internet-facing DNS; guidance otherwise); Addendum 2.4 (encrypt data and agent memory at rest).

**Evidence:**
- **Host firewall:** UFW is active, but the default policy is neither managed nor verified by Kent (deferred by decision).
- **Patching:** `unattended-upgrades` is not installed.
- **Encryption:** the root filesystem is not encrypted (ext4 on a plain partition).
- **Anti-malware:** none.
- **DNS:** the resolver reports `DNSSEC=no/unsupported`.
- **Baseline:** no host security configuration baseline document.

**Recommendation:**
1. **Baseline module:**
   - a CIS Ubuntu 24.04 Level 1 baseline;
   - a UFW default-deny policy;
   - `unattended-upgrades` for security patches;
   - DNSSEC validation (or a validating upstream);
   - an anti-malware and file integrity monitoring decision (see F-20).
2. **Encryption at rest:** full-disk (LUKS) or at least the Kent data paths as a deployment prerequisite.

### F-14 Medium — Segregation of duties and of environments

**Requirements:** CCoP 3.7.1(c), 3.2.2(c) (no conflicts of interest), 5.13.2 (separate system and database administration); AI Guideline 2.4 and 3.1 (segregation of environments).

**Evidence:**
- **Single operator:** one person is administrator, database administrator, approver and reviewer.
- **Mixed tooling on the reference host:** development-only tooling (the agent sudo grant, the simulation oracle and its gateway configuration) is installed on the same host as the "production" reference.

**Recommendation:**
1. **Document the separation model** for deployers: which roles must be distinct (operator, security reviewer, approver of template changes and model changes), and how the design supports it (Gitea review, signed commits).
2. **Separate dev from prod:** package development tooling as a separate, clearly non-production module.

### F-15 Medium — Credential lifecycle: long-lived static keys

**Requirements:** CCoP 5.17.1–5.17.2 (protect keys; manage their lifecycle; annual review); Addendum 2.6 (short-lived, scoped, just-in-time credentials; ephemeral tokens).

**Evidence:**
- **Static bearer keys:** the operator, kent and metrics gateway keys and the Gitea token have no expiry or rotation.
- **Gent keys:** valid for the Gent's lifetime.
- **Audit HMAC secret:** a static file in the operator's home.

**Recommendation:**
1. **Rotation:** a `--rotate-keys` operation per module, scheduled rotation with overlap, and key age in the digest.
2. **Scoped Gent credentials:** short-lived gateway tokens minted per Gent session.
3. **Secret protection:** move the audit HMAC secret to a root-owned credential used via a helper.

### F-16 Medium — End-to-end traceability and content logging incomplete

**Requirements:** Addendum 4.3 (distributed tracing with unique request IDs across agents and tool calls; log inputs, outputs, tool calls and internal state changes; cryptographically signed logs where needed); AI Guideline 4.1 (log queries and prompts for audit, subject to privacy); CCoP 6.1.1(a).

**Evidence:**
- **Present:**
  - the gateway logs call metadata (identity, tier, tokens, latency, `call_id`), but not prompts or outputs;
  - Gent tool activity reaches Loki through verbose container logs.
- **Gaps:**
  - Kent's own tool calls and prompts stay in Hermes' local session store and are not centralised;
  - no correlation ID links operator request → Kent → Gent → gateway calls.

**Recommendation:**
1. **Correlation ID:** propagate a trace ID (a header plus a Gent environment variable) and log it at every hop.
2. **Centralise Kent's activity:** ship Hermes session and tool-call logs to Loki, with redaction.
3. **Content logging:** a configurable prompt/response log with a privacy-reviewed retention policy.

### F-17 Low — Human-in-the-loop policy for high-impact agent actions is undefined

**Requirements:** Addendum 4.4 (human approval for high-risk or irreversible actions; hierarchical automation; guard against overwhelming humans, T10), 2.11 (memory reconciliation with human reviewers).

**Evidence:**
- **Autonomous actions:** Kent may spawn Gents, publish workspaces and commit template learnings (confidence ≥ 0.7) without approval.
- **Lenient dev judge:** in dev mode the judge is the local model, which adopted 5 of 6 learnings.

**Recommendation:** Define an action-risk table (e.g. spawn, destroy, template commit and cloud escalation above a budget each need approval) and enforce it in `kent-gent` and the poller, with an approval queue in Gitea issues or the digest.

### F-18 Low — Security testing programme is internal only

**Requirements:** CCoP 5.15 (annual penetration test by CREST-accredited providers or equivalent; after major changes), 5.16 (red or purple team plan and exercise every 24 months), Annex A (Breach and Attack Simulation); press release (technical guidance on adversarial attack simulation, penetration testing and threat hunting forthcoming); Addendum 3.2 (behavioural testing, adversarial evaluation, AI red teaming integrated with pentest).

**Evidence:** Extensive in-house adversarial tests exist (gateway, isolation, router probe, poisoning). There is no independent test, no red-team plan, and no AI red-teaming harness (e.g. garak, PyRIT, promptfoo) in CI.

**Recommendation:** An AI red-team test suite in CI, a red-team plan template per cl. 5.16.1(a)–(f), and an independent pentest before any CII use.

### F-19 Low — Governance artefacts for deployers are missing

**Requirements:** CCoP 3.5 (policies, standards, procedures, reviewed annually), 3.6.1 (Security-by-Design Framework applicability statement), 3.8.1 (change management identify/authorise/implement/validate), 9.1–9.2 (awareness and competency; records); AI Guideline 1.1 (awareness and training for developers, owners and leaders); Addendum 4.4 (secure-use awareness).

**Recommendation:** Ship templates: security policy, change procedure (the manual runbook as a validation step), SbD applicability statement, operator training outline for secure agentic AI use, and roles and responsibilities (cl. 3.2.2).

### F-20 Org/Info — Obligations only the deploying organisation can meet

| Obligation | Clause | Note for deployers |
|---|---|---|
| Board mandate, cyber resilience framework (tolerance, mitigation, transfer, recovery), annual board training, 6-monthly threat briefings | 3.1.1–3.1.5 | The reference's controls, risk register (F-03) and digest can feed board reporting |
| Senior management role, written authorities, 6-monthly posture reports, resourcing | 3.2.1–3.2.5 | — |
| Cyber Trust Mark Advocate (Tier 5), and a Tier 5 audit firm | 3.3.1–3.3.3 | By 31 Dec 2027 for existing CIIOs; 24 months for new |
| Audit remediation plan within 30 working days | 2.1 | Use `docs/conformance.md` findings format |
| Asset inventory incl. interconnected systems and topology | 4.1.1–4.1.4, 12.1 | `docs/privilege-map.md` is a strong base; add owners, critical functions, dependencies (Anthropic API, GitHub, OS mirrors) and topology |
| Incident reporting under the National Cybersecurity Incident Reporting Framework; suspicious activity on interconnected systems | 7.1.1(b), 12.7.5 | Secondary source (Zavior) cites S 678/2025: 2 h notification, 72 h details, 30 days final report; verify against the gazetted text |
| Commissioner's requests (reports, plans, logs within 30 days) | 5.14.5, 5.15.5, 5.16.3, 6.1.3, 6.3.4, 7.1.8, 7.2.5, 7.3.2, 7.3.8 | Keep artefacts current and exportable |
| File integrity monitoring (AIDE) and anti-malware | 5.9.2(f); Annex A | Deferred by project decision |

---

## 5. Compliance matrices

Status: **Met** · **Partial** · **Gap** · **Org** (deployer) · **N/A**. "Ref" = finding.

### 5.1 CSA Guidelines on Securing AI Systems (Oct 2024)

| Guideline | Status | Assessment | Ref |
|---|---|---|---|
| Key principle: secure by design and default | Partial | Strong defaults (deny-by-default gateway, sandboxed Gents); agent privilege default fails | F-01 |
| §3.2 four-step risk approach (assess, prioritise, implement, residual risk) | Gap | Steps 3 (controls) evident; steps 1, 2, 4 undocumented | F-03 |
| 1.1 Awareness and competency | Org | No training material | F-19 |
| 1.2 Security risk assessment / threat modelling | Gap | — | F-03 |
| 2.1 Secure the supply chain (SBOM, vuln DBs, supplier standards) | Partial | Hash pinning ✓; model provenance, SBOM, SCA ✗; chromadb CVEs | F-09, F-10 |
| 2.2 Model selection security trade-offs | Partial | Tiering rationale documented; local model choice and "uncensored" variant not assessed | F-09 |
| 2.3 Identify, track, protect AI assets (models, data, prompts, logs, assessments) | Partial | Prompts (SOUL, skills), logs, assessments versioned; models not | F-09 |
| 2.4 Secure the development environment | Partial | Hardened services; dev and prod co-resident with dev sudo grant | F-01, F-14 |
| 3.1 Secure deployment infrastructure (access, logging, segregation, secure defaults, firewalls) | Partial | Strong service hardening and isolation; host firewall and baseline deferred | F-13 |
| 3.2 Incident management procedures | Gap | — | F-07 |
| 3.3 Release responsibly (security checks before release) | Met | Conformance, CI, runbook before each RC | — |
| 4.1 Monitor AI system inputs (log queries/prompts) | Partial | Metadata logged; prompts not | F-16 |
| 4.2 Monitor outputs and behaviour (anomaly, drift) | Partial | Routing and validation logged; no anomaly or drift alerting | F-05 |
| 4.3 Secure-by-design updates and continuous learning | Partial | Template learning is gated; model updates uncontrolled | F-09, F-17 |
| 4.4 Vulnerability disclosure process | Met | `SECURITY.md` (private reporting) | — |
| 5.1 Proper data and model disposal | Partial | `--purge`, `--purge-state`; no retention/disposal policy for archives, models | F-04 |

### 5.2 CSA Securing Agentic AI Addendum (Jun 2026)

| Control | Status | Assessment | Ref |
|---|---|---|---|
| §4.1 Step 1: autonomy level, threat model, taint tracing, capability risks | Gap | Not documented | F-03, F-02 |
| §4.1 SaaS / shared-responsibility (cloud model) | Gap | No provider responsibility matrix | F-08 |
| 1.1 Risk assessment per standards (taint tracing for L2/L3) | Gap | — | F-03 |
| 2.1 Supply chain security (models, tools, libraries, hashes, SCA, trusted sources, structured search APIs) | Partial | Hashes ✓, trusted pinned sources ✓; model provenance ✗, SCA ✗, HTML scraping | F-09, F-10, F-12 |
| 2.2 Model hardening (instruction-following, refusal) | Partial | Opus 5.5 for cloud tiers; local model unassessed | F-09 |
| 2.3 System hardening (SbD, zero trust, robust system prompt, no hardcoded secrets) | Met/Partial | SOUL and prompts hardened; secrets via credentials ✓; zero-trust partial | — |
| 2.4 Identify, track, protect assets (SBOM, agent cards, encrypt memory at rest) | Partial | No SBOM or agent cards; no encryption at rest | F-10, F-13 |
| 2.5 Regular backups (memory snapshots, versioned rollback) | Partial | Template history in Git ✓; no data backups | F-07 |
| 2.6 AuthN/AuthZ (non-human identities, unique IDs, scoped short-lived creds, outbound allowlist, protective DNS) | Partial | Unique agent identities ✓; long-lived keys; no allowlist or protective DNS | F-15, F-12 |
| 2.7 Limit agency (no admin to agents, policy engine, command allowlist, no privilege self-modification) | **Gap (Kent)** / Met (Gents) | Gents tightly limited; Kent unrestricted shell | F-01 |
| 2.8 Least privilege, secure by default (no sudo by default; block network by default) | **Gap (Kent)** / Met (Gents) | — | F-01 |
| 2.9 Segregation of environments; decouple data from control flow; sandboxed code execution | Partial | Gent sandbox ✓; Kent control flow not isolated from tainted data | F-02 |
| 2.10 Self-reflection before decisions | Partial | CEO validation ✓; Kent has no enforced reflection or clarification gate | — |
| 2.11 Reduce hallucination (memory reconciliation, grounding, verification) | Met | Judge + filter + human-revertible template; validation against files | — |
| 3.1 Availability (rate limits, token/time limits, interaction limits) | Partial | Effort caps, escalation budget ✓; no gateway rate limits | F-11 |
| 3.2 Security testing (behavioural, adversarial, red teaming) | Partial | Strong internal suite; no AI red-team tooling or independent test | F-18 |
| 3.3 Secure external tools / MCP (trusted sources, mTLS/OAuth, rug-pull checks) | Partial | No MCP servers configured; Hermes skills and tools not inventoried or pinned | F-09 |
| 3.4 Secure inter-agent communication (authN, integrity, logging, no sensitive data leakage) | Partial | Kent↔Gent via DB and read-only inbox (integrity by filesystem ownership); no message authentication or central log | F-16 |
| 4.1 Validate inputs (guardrails, schema validation, sanitisation, PII detection) | Gap | Only template content filter | F-02, F-08 |
| 4.2 Validate outputs (checkpoints, PII guardrails, code scanning before execution) | Partial | Validation checkpoints ✓; no PII or code scanning of Gent code | F-08 |
| 4.3 Continuous monitoring and logging (tool calls, tamper-evident, tracing, alerting, circuit breakers) | Partial | Tamper-evident audit ✓, circuit breaker ✓; tracing and alerting ✗ | F-05, F-16 |
| 4.4 Human-in-the-loop for high-risk actions | Partial | Breaker ✓; approvals bypassable; no action-risk policy | F-01, F-17 |
| 4.5 Vulnerability disclosure | Met | `SECURITY.md` | — |

OWASP Agentic threats (Addendum Annex A) — residual exposure:

| Threat | Residual | Primary control / gap |
|---|---|---|
| T1 Memory poisoning | Low–Med | Template filter + judge + audit; F-17 |
| T2 Tool misuse | **High (Kent)** / Low (Gent) | F-01 |
| T3 Privilege compromise | **High** | F-01, F-15 |
| T4 Resource overload | Med | Caps and budgets; F-11 |
| T5 Cascading hallucination | Med | Validation, escalation, assessment |
| T6 Intent breaking / goal manipulation | **High** | F-02 |
| T7 Misaligned and deceptive behaviour | Med | Breaker, assessment; F-09 |
| T8 Repudiation and untraceability | Med | Audit chain ✓; F-16 |
| T9 Identity spoofing | Low | Constant-time keyed identities |
| T10 Overwhelming human-in-the-loop | Low | Digest and issues; define limits (F-17) |
| T11 Unexpected RCE | **High (Kent)** / Low (Gent) | F-01; Gent sandbox |
| T12 Agent communication poisoning | Med | F-02 |
| T13 Rogue agents | Low–Med | Identity, sandbox, breaker |
| T14 Human attacks on MAS | Med | F-06 |
| T15 Human manipulation | Med | F-19 (awareness) |

### 5.3 CSA Cybersecurity Code of Practice for CII 2026 (Release 1)

Applicability assumes Kent is deployed as a CII or as a CII interconnected system of a CIIO.
**Ref impl** = can be satisfied in this repository; **Org** = deploying CIIO.

| Clause | Requirement (abridged) | Status | Where | Ref |
|---|---|---|---|---|
| 1.2.6 | Code applies to CII interconnected systems | Info | — | F-20 |
| 2.1 | Audit remediation plan within 30 working days; Board updates | Org | Org | F-20 |
| 3.1.1–3.1.5 | Board mandate, cyber resilience framework, training, 6-monthly threat briefings | Org | Org | F-20 |
| 3.2.1–3.2.5 | Senior management role, written authorities, reports, resources | Org | Org | F-20 |
| 3.3.1–3.3.3 | Cyber Trust Mark Advocate (Tier 5); Tier 5 auditors | Org | Org | F-20 |
| 3.4.1–3.4.5 | Risk management framework, methodology, risk register, monitoring | Gap | Ref impl + Org | F-03 |
| 3.5.1–3.5.3 | Policies, standards, procedures; annual review | Gap | Ref impl (templates) + Org | F-19 |
| 3.6.1 | Security-by-Design Framework applicability | Partial | Ref impl | F-19 |
| 3.7.1(a) | Defence in depth | Met | — | — |
| 3.7.1(b) | Least privilege | Partial | Ref impl | F-01 |
| 3.7.1(c) | Segregation of duties | Gap | Org + Ref impl | F-14 |
| 3.7.2 | Defence by diversity; zero trust (to the extent possible) | Partial | Ref impl | F-15 |
| 3.8.1 | Change management (identify, authorise, implement, validate) | Partial | Ref impl | F-09, F-19 |
| 3.9.1–3.9.4 | Cloud: accountability, inform Commissioner, risk assessment within 30 days, local legal representative | Gap | Org (+ Ref impl template) | F-08 |
| 3.10.1–3.10.5 | Outsourcing oversight and contract terms | Gap | Org | F-08 |
| 4.1.1 | CII asset inventory (a)–(j) incl. topology, cloud, outsourced | Partial | Ref impl (privilege map) + Org | F-20 |
| 4.1.2–4.1.4 | Identify, inventory and oversee interconnected systems | Org | Org | F-20 |
| 5.1.1–5.1.2 | Access restricted, authN/authZ commensurate with risk | Met (services) / Partial (UIs) | — | F-06 |
| 5.1.3 | External parties' access documented, supervised, on-site | Org | Org | — |
| 5.1.4 | Session timeout or monitoring | Gap | Ref impl | F-06 |
| 5.2.1(a)–(e) | Least privilege accounts, install rights, no shared accounts, activity anomaly monitoring, disable unused | Partial | Ref impl | F-01, F-05, F-06 |
| 5.2.2 | Annual account review | Gap | Ref impl + Org | F-06 |
| 5.3.1(a)–(f) | Privileged access: selected accounts, hardened environment, inventory, MFA, no sharing, break-glass | Gap | Ref impl + Org | F-01, F-06 |
| 5.4 | Domain controller trust monitoring | N/A | — | — |
| 5.5.1–5.5.3 | Segmentation by risk; minimum inter-segment traffic; isolate on incident | Met | Gent network; pause/destroy isolates | — |
| 5.6.1–5.6.3 | Network access rules reviewed; no external/internet connection unless necessary | Partial | Loopback-only services ✓; host firewall deferred; Gent internet by design | F-12, F-13 |
| 5.7 | Remote connection (MFA, encryption, scanning) | N/A (none configured) | — | — |
| 5.8 | Wireless LAN | N/A | — | — |
| 5.9.1–5.9.3 | Security configuration baselines incl. malware protection; annual review | Partial | Service baseline ✓ (units); host baseline ✗ | F-13 |
| 5.9.4 | Authorised administration devices only | Org | Org | — |
| 5.10.1–5.10.2 | Patch management incl. integrity, testing, rollback, oversight | Partial | Pinned upgrades via modules and rollback ✓; no monitoring of releases or advisories | F-10, F-13 |
| 5.11 | Portable devices and media | N/A / Org | — | — |
| 5.12.1–5.12.3 | Approved applications list; annual review; integrity verification | Partial | Pinned artifacts ✓; models and Hermes plugins not on an approved list | F-09 |
| 5.12.4–5.12.6 | Multi-tier, OWASP, WAF (internet-facing) | Partial / N/A | Not internet-facing | — |
| 5.12.7 | Compilers/debuggers only where necessary | Partial | Gents run code by design (sandboxed); document the rationale | F-03 |
| 5.13.1–5.13.5 | DB access, SA/DBA segregation, data-at-rest, anomaly and bulk-query monitoring | Partial | Kent reads Gent DBs read-only ✓; no encryption, DB monitoring | F-13, F-14 |
| 5.14.1–5.14.5 | Vulnerability identification, remediation, annual assessment | Gap | Ref impl (CI) + Org | F-10 |
| 5.15.1–5.15.5 | Annual penetration test by accredited testers | Org | Org | F-18 |
| 5.16.1–5.16.3 | Red/purple team plan; every 24 months | Org | Org (+ plan template) | F-18 |
| 5.17.1–5.17.2 | Cryptographic key protection and lifecycle | Partial | Keys protected ✓; lifecycle ✗ | F-15 |
| 6.1.1 | Log access and activities, network connections | Partial | Gateway, proxy, sudo, installs ✓; Kent activity, network connections partial | F-04, F-16 |
| 6.1.2 | Firewall, DNS, web proxy, NIDS logs | Partial | Proxy only | F-04 |
| 6.1.3 | Provide logs to the Commissioner on request | Partial | Exportable via APIs; no procedure | F-20 |
| 6.1.4 | Consistent time, protection, **12-month retention**, structure, policy | Gap | Ref impl | F-04 |
| 6.2.1–6.2.3 | Monitor, correlate, IOCs, baseline, alerts, annual review | Gap | Ref impl | F-05 |
| 6.2.4 | Facilitate CSA-supported detection systems | Partial | Architecture permits; document | F-05 |
| 6.3.1–6.3.4 | Threat hunting every 24 months | Org | Org (+ hunting queries) | F-05 |
| 6.4.1–6.4.3 | CTI intake, sharing with the Commissioner, controls | Org | Org | F-05 |
| 7.1.1–7.1.8 | Incident response plan, CIRT, RCA, forensics | Gap | Ref impl (templates, AI playbooks) + Org | F-07 |
| 7.2.1–7.2.5 | Crisis communication plan | Org | Org | F-07 |
| 7.3.1–7.3.10 | Exercise plan (MSEL), annual scenario exercises incl. interconnected systems, CSA technical exercises | Org | Org (+ scenario library) | F-07 |
| 8.1.1–8.1.5 | Offline, protected, tested backups | Gap | Ref impl | F-07 |
| 8.2.1–8.2.5 | BCP/DRP with RTO/RPO, annual review | Gap | Org (+ template) | F-07 |
| 9.1–9.2 | Awareness programme; competencies; certified supervision of risk assessments and audits | Org | Org | F-19 |
| 10 | OT security | N/A | — | — |
| 11.2 | DNSSEC (internet-facing DNS only) | N/A (Annex A guidance: validation recommended) | — | F-13 |
| 12.1.1–12.1.2 | Interconnected-system inventory and topology | Partial | Privilege map; add topology | F-20 |
| 12.2.1–12.2.4 | Least privilege; disable inactive; annual review | Partial | Ref impl | F-06 |
| 12.3.1–12.3.3 | MFA for privileged access; no sharing; break-glass | Gap | Ref impl + Org | F-06 |
| 12.4.1–12.4.3 | Segmentation; firewalls at boundaries; monitor and block unauthorised outbound | Partial | Gent segmentation ✓; host firewall deferred; open GET egress | F-12, F-13 |
| 12.5.1–12.5.2 | Only necessary ports and services | Met | Loopback-only; conformance verifies | — |
| 12.6.1 | Timely patching with compensating controls | Partial | — | F-10 |
| 12.7.1–12.7.6 | Review management and monitoring activity, EDR logs, virtualisation monitoring, report suspicious activity to CSA, annual review | Gap | Ref impl + Org | F-05, F-20 |
| Annex A | Enterprise guidance (SbD, principles, wireless, VA, PT, threat hunting, BAS, DNSSEC) | Partial | — | F-10, F-18 |

### 5.4 Policy direction (press release, 22 Jul 2026; law-firm and vendor summaries)

| Point | Implication for this reference | Status | Ref |
|---|---|---|---|
| Frontier AI lets attackers find vulnerabilities faster, shortening exploitation windows | Continuous SCA and image scanning; rapid patch path; exploitability-based prioritisation | Gap | F-10, F-13 |
| Boards must own a documented cyber resilience framework (tolerance, mitigation, transfer, recovery), reviewed annually | Reference should produce board-ready posture data: risk register, KPIs, digest roll-up | Org / Gap | F-03, F-20 |
| Cyber Trust Mark Tier 5 mandatory (CIIOs and their auditors) | Organisational; reference controls map to CTM domains | Org | F-20 |
| Visibility over interconnected systems (response to APT campaigns such as UNC3886, per Zavior) | Inventory, topology, monitoring of Kent as an interconnected system | Partial | F-05, F-20 |
| CSA will work with CIIOs to deploy threat detection across network segments | Log export and sensor facilitation procedure | Partial | F-05 |
| Comprehensive exercise plans for coordinated response | Scenario library incl. AI-specific scenarios | Gap | F-07 |
| New Cloud CCoP (2H 2026) with CSP companion guides (AWS, Google Cloud, Azure) | Anthropic API use is a cloud dependency. Prepare a cloud risk assessment now; re-assess against the Cloud CCoP when issued | Gap | F-08 |
| Forthcoming technical guidance on adversarial attack simulation, penetration testing, threat hunting | Plan templates and test harnesses now; align when issued | Gap | F-18 |
| Commissioner may request reports and plans within 30 days (Stephenson Harwood) | Keep artefacts current and exportable | Org | F-20 |
| Incident reporting: 2 h / 72 h / 30 days (S 678/2025, per Zavior; to verify) | IR playbook and contact procedures | Gap | F-07 |
| No AI-specific clauses in CCoP 2026 (auditor's reading of the issued text) | AI controls must be evidenced through the AI Guidelines and Addendum plus general CCoP clauses | Info | §1.3 |

---

## 6. Remediation roadmap

| Priority | Window | Actions | Findings |
|---|---|---|---|
| **P0 — before any non-lab use** | Immediately | Revoke the dev sudo grant. Sandbox Kent's terminal or restrict it to allowlisted tools. Remove Kent's docker-group access. Forbid approval-bypass modes outside tests. | F-01 |
| **P1** | 0–30 days | Risk assessment and register (autonomy levels, taint trace, capabilities). Quarantined processing of Gent output plus input guardrails. Layer-3 injection tests. chromadb removal or waiver. pip-audit, trivy and SBOM in CI. Model integrity manifest and permissions. | F-02, F-03, F-09, F-10 |
| **P2** | 30–90 days | Off-host immutable logging with 12-month retention; DNS and firewall logs. Alert rules and baselines. MFA and SSO with individual accounts. Backup and restore module with restore tests. Gateway DLP/redaction and a local-only mode for regulated data. Per-identity rate limits and budgets. Egress allowlists. | F-04, F-05, F-06, F-07, F-08, F-11, F-12 |
| **P3** | 90–180 days | Host baseline module (CIS, UFW default-deny, unattended upgrades, encryption at rest). Key rotation. End-to-end tracing. Action-risk HITL policy. AI red-teaming harness. Governance and IR/BCP/exercise templates. | F-13, F-15, F-16, F-17, F-18, F-19 |
| **Deployer** | Per CCoP timeline | Board, senior-management and CTM Tier 5 obligations; cloud and outsourcing notifications; exercises; independent pentest and red team; incident reporting. | F-20 |

---

## 7. Auditor's opinion

For its stated purpose, a reference template and proof of concept, Kent is **above the typical
standard**. Its core security mechanisms are real, tested and reversible, and its documentation
is honest about what is not yet built. The architecture is sound: identity-scoped model access,
sandboxed sub-agents, restrictive egress, tamper-evident audit, and supply-chain pinning are the
right foundations and align well with the CSA Agentic Addendum.

However, the most privileged component, Kent itself, is the least constrained. Until F-01 and F-02
are resolved, a single prompt injection could turn the orchestrating agent into a host-level
attacker. Separately, the CCoP 2026 detection, retention, identity, recovery and cloud-governance
obligations (F-04 to F-08) are not yet met. **The reviewer does not recommend deploying Kent as,
or connecting it to, CII, or processing regulated or personal data with it, until P0 and P1 are
complete, P2 is substantially complete, and the deploying organisation has put the F-20
obligations in place.** A follow-up review should verify remediation, include independent
penetration and AI red-team testing, and re-assess against the Cloud CCoP once issued.

---

## 8. Remediation status (living section, maintained by the project)

Not part of the reviewer's opinion. Updated as remediation lands; a follow-up review verifies it.
State as of **28 Sep 2026, evening**, on branch `release/v0.1.0`, tag **v0.1.0-rc.3**.

| Finding | Status | What changed / what remains |
|---|---|---|
| F-01 Critical | **Partly remediated** | Done: Kent is its own no-login `kent` account (not in docker/sudo/adm); Docker reached only via root brokers `kent-spawn-gent`/`kent-destroy-gent`/`kent-gent-ctl` (registry-checked); own commit-pinned Hermes with admin-owned managed policy; deny rules for nested `hermes -z/--oneshot/--yolo`, `HERMES_YOLO_MODE`, managed dir, credentials, docker; `chat -q` single-query and cron deny dangerous commands; tirith 0.4.2 pre-exec scanner pinned and enforced (fail-closed in hardened). **Remains:** dev sudo grant `/etc/sudoers.d/90-kent-agent` active until 2026-09-29 20:40 (auto-expiry) — revoke on any non-lab host; lab profile still gives Kent a local shell as `kent`; sandboxed terminal backend / allowlisted tool surface (hardened) not built; interactive approval for spawn/destroy/publish (F-17) not built |
| F-02 High | Open | No quarantined processing of Gent output; layer-3 injection tests deferred while the sudo grant is active |
| F-03 High | Open | No risk register / threat model |
| F-04 High | Open | Retention/off-host immutable logs not done |
| F-05 High | Open | No alert rules |
| F-06 High | **Partly remediated** | Each human's use of Kent attributed (`human:<name>` via `kent-exec`, sudo-audited); MFA/SSO not done |
| F-07 High | Open | No backup/restore module |
| F-08 High | Open | No DLP/redaction, no local-only mode flag |
| F-09 Medium | Open | Model file 0777 on reference host; no integrity manifest |
| F-10 Medium | Open | chromadb 1.1.1 CVEs latent in Gent image; no pip-audit/trivy/SBOM in CI |
| F-11 Medium | Open | No per-identity rate limits/budgets |
| F-12 Medium | Open | Gent egress open to any public domain (SearXNG bridge added as structured search). Kent's Hermes no longer retries failed web calls through anonymous outside vendors (`web.keyless_fallback: false`, conformance-checked); page fetching (`web_extract`) has no backend yet |
| F-13 Medium | Open | Host baseline; note: operator's personal SearXNG on 0.0.0.0:8082 found and **retired** (28 Sep) |
| F-14 Medium | Partly | Lab vs hardened profiles (`/etc/kent/profile`, `kent-admin profile`) |
| F-15 Medium | Open | Static keys, no rotation |
| F-16 Medium | Open | Tracing incomplete. Failed gateway calls now record `error_class`/`error`, so fallbacks to the local tier are explained in Loki; "Kent routing" dashboard |
| F-17 Low | Open | HITL policy undefined |
| F-18/F-19/F-20 | Open | Programme/governance/deployer items |

Additional findings raised during remediation (28 Sep):
- **Silent escalation failures.** Oracle (sim) tiers rejected Hermes's `reasoning_effort`, so every
  smart/frontier call fell back to the local tier within ~2 ms with no recorded cause. Fixed (`drop_params`,
  test) and failure causes are now logged.
- **Local telemetry is readable by every local account.** Loki and the Alloy UI have no per-account access
  control; in the lab profile the `kent` account can read the whole journal. Open.
- **Stale units outside the repo.** An early dev-stack `open-webui.service` crash-looped for ~3 weeks
  (591k restarts, ~4k log lines/h). Removed; the reinstall test should look for unknown units.
- **Containers ran as root.** SearXNG's image runs as uid 0; Kent's instance now runs as the registered
  `kent-searxng` account (`--user`), and conformance checks every Kent container is non-root and
  registered. The image's internal uid/gid 977 had also been handed by the host allocator to Kent's
  `kent` group (overlap with the operator's personal container's files) — resolved.
- **Unpinned runtime download.** Hermes would auto-download the latest tirith on first use; now pinned
  (SHA-256, release signature checked with openssl; cert chain to Sigstore root not verified — no cosign).
- **Env overrides beat config.** `TIRITH_*` (and similar) env vars override Hermes config; security-relevant
  env is pinned in the managed `.env` (applied last, root:kent 0640).

---

## Appendix A — Evidence log (reference host, 28 Sep 2026)

| # | Check | Result |
|---|---|---|
| E1 | `hermes -p kent config show` → Terminal | `Backend: local` |
| E2 | `hermes --help` (`-z` one-shot); pinned-release source `hermes_cli/oneshot.py`, `tools/approval.py` | `-z` sets `HERMES_YOLO_MODE=1` (bypass); `chat -q` single-query mode denies dangerous commands by default (corrected 2026-09-28) |
| E3 | `id` | operator in `docker`, `sudo`, `adm` |
| E4 | `install/dev/agent-sudo.sh status` | `ACTIVE: /etc/sudoers.d/90-kent-agent` |
| E5 | Active model file mode | `-rwxrwxrwx … Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf` |
| E6 | `pip-audit` gent lock | chromadb 1.1.1: PYSEC-2026-311 / CVE-2026-45829, -3813 / CVE-2026-45830, -3814 / CVE-2026-45833, -3815 / CVE-2026-45831; no fix listed |
| E7 | `pip-audit` gateway lock | No known vulnerabilities |
| E8 | `configs/loki.yaml` | `retention_period: 720h` |
| E9 | `resolvectl status` | `DNSSEC=no/unsupported` |
| E10 | `timedatectl` | NTP active, synchronised |
| E11 | `unattended-upgrades` | not installed |
| E12 | Root filesystem | ext4 on plain partition (no dm-crypt) |
| E13 | Squid logs in Loki | 747 lines / 24 h |
| E14 | Grafana drop-in | single `admin` user; sign-up and anonymous disabled; no SSO/MFA |
| E15 | Gateway config | no RPM/TPM/budget settings |
| E16 | Conformance run | 99 PASS / 0 FAIL / 6 INFO |
| E17 | CCoP 2026 PDF text search for "AI" / "artificial intelligence" | no matches |

## Appendix B — Sources

- CSA, Guidelines on Securing AI Systems (Oct 2024): https://isomer-user-content.by.gov.sg/36/42140c27-030f-4bb9-b4c7-b543ad2ddad4/guidelines-on-securing-ai-systems_2024-10-15.pdf — landing page: https://www.csa.gov.sg/resources/publications/guidelines-and-companion-guide-on-securing-ai-systems/
- CSA, Securing Agentic AI — Addendum (v1.0, 17 Jun 2026): https://www.csa.gov.sg/resources/publications/addendum-on-securing-ai-systems/
- CSA, Codes of Practice (CCoP for CII 2026, issued 29 Jul 2026): https://www.csa.gov.sg/legislation/codes-of-practice/
- CSA press release, 22 Jul 2026: https://www.csa.gov.sg/news-events/press-releases/cybersecurity-code-of-practice-for-critical-information-infrastructure-to-be-updated-to-address-apt-and-ai-enabled-threats/
- Stephenson Harwood summary: https://www.stephensonharwood.com/insights/singapores-csa-issues-updated-cybersecurity-code-of-practice-for-critical-information-infrastructure/
- Zavior summary: https://zavior.ai/cii-details
- OWASP Agentic AI Threats and Mitigations: https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/
- OSV advisories: https://osv.dev/vulnerability/PYSEC-2026-311 (and -3813, -3814, -3815)
