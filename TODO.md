# Kent — TODO

Near-term work. Status of every control: [`docs/controls.md`](docs/controls.md);
assessment history and open items: [`docs/conformance.md`](docs/conformance.md).

**Status:** v0.1.0 release candidate

## To v0.1.0

- [ ] Anthropic key: `--config prod`, `FALLBACK_TIMEOUT=120`, re-run conformance and one tiny Gent simulation with live Opus
- [ ] Merge the release branch and tag `v0.1.0`

## Regulated-workload gaps (controls.md)

- [ ] DP-3 Redaction / DLP before cloud calls; DP-4 provider zero-retention + BAA/DPA
- [ ] DP-5 Encryption at rest; DP-6 backups; RO-4 disaster-recovery plan
- [ ] LM-6/7 Off-host immutable log shipping with configurable retention
- [ ] AC-7 MFA/SSO for Grafana and Gitea
- [ ] NS-4 Host firewall default-deny; NS-5 egress POST allowlist with approvals
- [ ] CM-6 Vulnerability scanning + SBOM (pip-audit, trivy); CM-7 AIDE; CM-8 scheduled key rotation
- [ ] RO-6 systemd-oomd policies for heavy services

## Features

- [ ] Seed-from archive (architecture §11.2)
- [ ] Push adopted learnings to running Gents; give the judge its past verdicts (dedupe)
- [ ] Stronger learning uptake by local models (make "apply team knowledge" part of expected output)
- [ ] Dev-mode judge: raise the adoption bar or require operator approval when the judge is local
- [ ] llama.cpp as a managed service (own account and unit)
- [ ] Grafana alert rules + notification channel
- [ ] Layer-3 prompt-injection tests against Kent (sandboxed profile)

*Items move to GitHub Issues when concrete enough to assign. Last updated: 2026-09-28.*
