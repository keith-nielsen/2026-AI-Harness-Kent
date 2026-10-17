# Kent — Control Matrix

Kent is a reference template for an agentic AI stack that must satisfy
enterprise stability, supportability, control and audit requirements, the kind
expected for PII-bearing data or banking/medical-class operations. This document
lists each control such an environment needs, how Kent implements it, the
evidence that proves it, and its status.

> **Scope and honesty.** Kent v0.1.0 is a *reference implementation*, not a
> certified or compliant system. Framework references (ISO/IEC 27001:2022
> Annex A, SOC 2 Trust Services Criteria, NIST SP 800-53 Rev. 5, HIPAA Security
> Rule §164.312, PCI DSS v4.0) indicate *which requirement a control speaks to*;
> they are not attestations. Controls marked **Gap** must be closed, and the
> whole deployment assessed, before regulated data is processed.

Status: **Met** (implemented and verified) · **Partial** · **Gap** (not yet implemented) · **Org** (organisational, outside the software)

Evidence refers to repository tests and checks; `conformance` = `tests/conformance/conformance.py`.

---

## 1. Access control and least privilege

| # | Control | Kent implementation | Evidence | Status | Maps to |
|---|---|---|---|---|---|
| AC-1 | Unique identity per service, no shared accounts | Own system account per service (`litellm`, `prometheus`, `loki`, `alloy`, `grafana`, `gitea`, `kent-squid`), own account per Gent (`gent-<id>`) | conformance §14 "no shared service account" | Met | ISO A.5.16, A.8.2 · SOC2 CC6.1 · NIST AC-2, IA-2 |
| AC-2 | Least privilege for model access | Identity-scoped gateway keys: Gents local-only, metrics key metrics-only; deny-by-default request shape for restricted identities | `tests/gateway` (145), `tests/live` (46) | Met | ISO A.8.2, A.8.3 · SOC2 CC6.3 · NIST AC-6 |
| AC-3 | Least privilege for processes | Empty capability sets, `NoNewPrivileges`, read-only system, seccomp filters; exposure ≤ 3.0 | conformance §19 | Met | NIST AC-6(9), CM-7 · PCI 2.2 |
| AC-4 | Privileged actions limited and explicit | Only two root tools runnable by the operator (`kent-spawn-gent`, `kent-destroy-gent`), argument-validated; all sudo use ingested into the audit chain | conformance §14; audit chain `command` events | Met | ISO A.8.2, A.8.18 · NIST AC-6(2), AU-2 |
| AC-5 | Data access separation | Kent reads Gent data read-only (group + SQLite `mode=ro`); Gents cannot read kent.db or each other | `tests/gent/run_tools_selftest.sh`; §9 layout | Met | ISO A.8.3 · HIPAA 164.312(a)(1) |
| AC-6 | Human authentication to UIs | Grafana and Gitea local admin accounts, loopback-only, no anonymous access | conformance §12 | Partial | NIST IA-2(1) · PCI 8.4 · HIPAA 164.312(d) |
| AC-7 | MFA / SSO for human access | — | — | **Gap** | ISO A.8.5 · PCI 8.4 · NIST IA-2(1) |
| AC-8 | Periodic access review | Registry + manifests make accounts enumerable | `docs/privilege-map.md` | **Org** | ISO A.5.18 · SOC2 CC6.2 |
| AC-9 | Segregation of duties | Single-operator design | — | **Org** (limitation) | ISO A.5.3 · NIST AC-5 |

## 2. Network security

| # | Control | Kent implementation | Evidence | Status | Maps to |
|---|---|---|---|---|---|
| NS-1 | Services not exposed | Every service on 127.0.0.1 (or the Gent bridge address) | conformance §16 | Met | ISO A.8.20 · PCI 1.3 · NIST SC-7 |
| NS-2 | Workload segmentation | Gents on an internal Docker network; only gateway and proxy reachable through socket bridges | tools self-test; install isolation probe | Met | ISO A.8.22 · NIST SC-7(5) |
| NS-3 | Egress control | Own Squid: GET/HEAD + CONNECT 443 to public addresses only; private ranges and host services denied | tools self-test (8 proxy checks) | Met | NIST SC-7(5), AC-4 · PCI 1.4 |
| NS-4 | Host firewall, default deny | Two narrow Kent rules only; host policy deferred | — | **Gap** (deferred) | PCI 1.2 · NIST SC-7 |
| NS-5 | Approved outbound POST / per-entity grants | — | — | **Gap** (planned §15.4) | NIST AC-4 |
| NS-6 | TLS for internal traffic | Loopback-only HTTP between local services | — | Partial (acceptable on a single host; required when multi-host) | PCI 4.2 · HIPAA 164.312(e)(1) |

## 3. Data protection

| # | Control | Kent implementation | Evidence | Status | Maps to |
|---|---|---|---|---|---|
| DP-1 | Secrets never in env files, code or repo | systemd `LoadCredential`, Gitea `*_URI`, read-only key mounts; operator copies 0600; secret scan before release | conformance §13 | Met | ISO A.8.24 · PCI 3.6 · NIST SC-12, IA-5 |
| DP-2 | Minimise data sent to cloud models | Gents never reach cloud tiers; only Kent/operator; router keeps routine work local | gateway tests; router probe | Partial | ISO A.5.34 · HIPAA 164.502(b) |
| DP-3 | Redaction / DLP before cloud calls | — | — | **Gap** (required for PII) | ISO A.8.11, A.8.12 · HIPAA 164.312(e) · PCI 3.3 |
| DP-4 | Provider terms: zero data retention, BAA/DPA | — | — | **Org** | HIPAA 164.308(b) · GDPR Art. 28 |
| DP-5 | Encryption at rest | Relies on host full-disk encryption (not enforced by installer) | — | **Gap** | ISO A.8.24 · PCI 3.5 · HIPAA 164.312(a)(2)(iv) |
| DP-6 | Backup and restore | Gent data archived on retire; no backup of kent.db, Gitea, audit chain | — | **Gap** | ISO A.8.13 · SOC2 A1.2 · NIST CP-9 |
| DP-7 | Data retention and disposal | Loki/Prometheus 30 d; `--purge-state` for disposal; Gent `destroy --purge` | uninstall tests | Partial | ISO A.8.10 · PCI 3.2 |

## 4. Logging, monitoring and audit

| # | Control | Kent implementation | Evidence | Status | Maps to |
|---|---|---|---|---|---|
| LM-1 | Every model call logged with identity | Gateway emits `llm_call` with identity, tier, routing decision, tokens, latency | conformance §17 (Loki query) | Met | ISO A.8.15 · SOC2 CC7.2 · NIST AU-2, AU-3 · PCI 10.2 |
| LM-2 | Access denials logged | `access_denied` events; ingested into the audit chain | conformance §17; `kent-audit-ingest` | Met | NIST AU-2 · PCI 10.2.4 |
| LM-3 | Tamper-evident audit trail | HMAC-chained log, journal anchors with chain identity, daily verification | `tests/kent_core/test_audit_chain.py` (17) | Met | ISO A.8.15 · NIST AU-9, AU-10 · PCI 10.3 |
| LM-4 | Centralised logs and metrics | Alloy → Loki; Prometheus scrapes all services; Grafana dashboard | conformance §17 | Met | ISO A.8.16 · SOC2 CC7.2 · NIST SI-4 |
| LM-5 | Alerting on critical events | Circuit breaker and chain break → Gitea issue + crit journal entry; daily digest | `tests/kent_core/test_poll_learnings.py` | Partial (no paging channel) | NIST IR-6, SI-4(5) · SOC2 CC7.3 |
| LM-6 | Off-host, immutable log storage | — | — | **Gap** | PCI 10.3.3 · NIST AU-9(2) |
| LM-7 | Log retention per regulation (e.g. 1–7 years) | 30 days hot | — | **Gap** (policy-dependent) | PCI 10.5 · HIPAA 164.316(b)(2) |
| LM-8 | Time synchronisation | Host NTP (systemd-timesyncd) assumed | — | **Org** | NIST AU-8 · PCI 10.6 |

## 5. Change, configuration and supply chain

| # | Control | Kent implementation | Evidence | Status | Maps to |
|---|---|---|---|---|---|
| CM-1 | Pinned, verified artifacts | SHA-256 pinned binaries (Gitea also GPG-verified), hash-locked Python deps, digest-pinned base image | CI "Pinned artifacts" | Met | ISO A.8.19 · NIST SA-12, SR-4 · PCI 6.3 |
| CM-2 | Recorded, reversible installation | Manifests per module; exact uninstall; verified rollback to the pre-install snapshot (twice) | `tests/install` (15); rollback record | Met | ISO A.8.32 · NIST CM-3, CM-6 |
| CM-3 | Vendor configs untouched | Drop-ins and provisioning only; pre-existing state moved aside and restored | layer-5 tests; rollback diff | Met | NIST CM-6 |
| CM-4 | Change control on the template Gents inherit | Every template change is a Gitea commit with source and reasoning; content filter; revertible | poller tests; Gitea history | Met | ISO A.8.32 · SOC2 CC8.1 |
| CM-5 | CI on every change | shellcheck, config/Python validation, pinned-artifact checks, unit suites | `.github/workflows/validate.yml` | Met | SOC2 CC8.1 · NIST SA-11 |
| CM-6 | Vulnerability scanning, SBOM | Lock files enumerate every dependency; no scanner yet | — | **Gap** | ISO A.8.8 · PCI 6.3.3, 11.3 · NIST RA-5 |
| CM-7 | File integrity monitoring | — | — | **Gap** (AIDE planned) | PCI 11.5 · NIST SI-7 |
| CM-8 | Key and credential rotation | Keys regenerated on reinstall; per-Gent keys revoked on destroy | — | Partial (no scheduled rotation) | PCI 3.7 · NIST IA-5(1) |

## 6. AI-specific controls

| # | Control | Kent implementation | Evidence | Status | Maps to |
|---|---|---|---|---|---|
| AI-1 | Model access tiered by trust | Router classifies; untrusted workloads (Gents) local-only; cloud only via Kent | gateway + router tests | Met | NIST AI RMF MANAGE 2 · ISO/IEC 42001 A.6 |
| AI-2 | Prompt-injection resistance | Gent output labelled untrusted; judge/assessor prompts treat it as data; router injection probe; template content filter | router probe; poller poisoning tests | Partial (layer-3 Kent tests deferred) | OWASP LLM01 · NIST AI 600-1 |
| AI-3 | Tool/agent sandboxing | Gent tools confined to the workspace; containers without capabilities; no host execution of Gent code. Kent's Hermes in a bubblewrap sandbox: operator folders only by per-session grant, private state and credentials not mounted, own tools via an allowlisting broker (architecture §12.4) | tools self-test (24); broker/grant unit tests; Kent containment tests pending (operator) | Partial | OWASP LLM06 (excessive agency) |
| AI-4 | Human oversight of autonomous work | Circuit breaker halts stuck Gents; operator resumes; assessments recorded; digest | breaker tests (live + unit) | Met | EU AI Act Art. 14 · NIST AI RMF GOVERN |
| AI-5 | Cost / resource bounds | Escalation budget, per-project effort caps, container limits, disk guard | poller + CEO tests | Met | OWASP LLM10 (unbounded consumption) |
| AI-6 | Quality assurance of outputs | Validation per task, Kent assessment (frontier), nightly QA sample | CEO tests; `gent_assessments` | Partial (QA not yet run on cloud) | ISO/IEC 42001 A.6.2.4 |
| AI-7 | Knowledge-poisoning control | Judge threshold + content filter + audit + revertible commits | `test_poisoned_learnings_*` | Met | OWASP LLM04 (data poisoning) |

## 7. Resilience and operations

| # | Control | Kent implementation | Evidence | Status | Maps to |
|---|---|---|---|---|---|
| RO-1 | Graceful degradation | Cloud failure falls back to the local tier (`FALLBACK_TIMEOUT`) | live fallback tests | Met | SOC2 A1.1 · NIST CP-2 |
| RO-2 | Self-recovery | systemd restarts; Gent requeues interrupted tasks; `kent-gent restart` | CEO tests | Met | NIST SI-13 |
| RO-3 | Health and capacity monitoring | Scrape targets, host metrics, stale-heartbeat detection, disk guard | conformance §17, §22 | Met | SOC2 A1.1 |
| RO-4 | Disaster recovery plan | — | — | **Gap** | ISO A.5.30 · NIST CP-2, CP-10 |
| RO-5 | Runbooks and incident response | Commands documented (`dev-notes.md`, architecture §25) | — | Partial | ISO A.5.24 · NIST IR-8 |
| RO-6 | Memory-pressure protection | — | — | **Gap** (systemd-oomd policies planned) | — |

---

## Formal validation (IQ / OQ / PQ)

Regulated environments (for example GxP computerised systems or banking model-risk
validation) expect installation, operational and performance qualification.
Kent's existing checks map as follows:

| Stage | Question | Kent evidence |
|---|---|---|
| **IQ** — installation qualification | Is it installed as specified, and can it be removed? | Each module's install-time verification; `tests/conformance/conformance.py` (99 checks); `snapshot.sh` before/after diffs; verified rollback to the pre-install state |
| **OQ** — operational qualification | Does each function behave as specified, including under attack? | 214 unit tests (gateway policy, audit chain, CEO state machine, install library), 46 live gateway tests with a canary upstream, 24 in-container isolation checks, router injection probe |
| **PQ** — performance qualification | Does the integrated system do the job end to end? | Recorded simulations: operator → Kent → Gent → escalation → assessment → learning adoption → inheritance by a new Gent; circuit-breaker trip and resume |

What a regulated deployment still adds: an approved validation plan and
traceability matrix, signed test records, change control over re-validation, and
periodic review. The conformance report ([`conformance.md`](conformance.md)) is the
living record these would be built from.

---

## Gaps before regulated data (summary)

1. **DP-3** redaction/DLP before cloud calls, and **DP-4** provider zero-retention + BAA/DPA.
2. **DP-5** encryption at rest, **DP-6** backups, **RO-4** DR plan.
3. **LM-6/7** off-host immutable logs with regulatory retention.
4. **AC-7** MFA/SSO; **NS-4** host firewall; **CM-6/7** vulnerability scanning and FIM; **CM-8** scheduled rotation.
5. Organisational controls: access reviews, segregation of duties, incident response, validation sign-off.
