# Kent — Reference Agentic AI Stack

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Architecture](https://img.shields.io/badge/Architecture-v3.0.0-green)](docs/architecture.md)
[![Status](https://img.shields.io/badge/Status-v0.1.0--rc-orange)](https://github.com/keith-nielsen/2026-AI-Harness-Kent/releases)
[![Conformance](https://img.shields.io/badge/Conformance-99%20pass%20%2F%200%20fail-brightgreen)](docs/conformance.md)
[![Validate](https://github.com/keith-nielsen/2026-AI-Harness-Kent/actions/workflows/validate.yml/badge.svg)](.github/workflows/validate.yml)

**A reference template for a well-designed, fully integrated, self-hosted agentic
AI stack**: minimum privilege, complete logging and telemetry, industry-standard
use of an agent harness (Hermes), local models and frontier models, and the
controls, procedures and formal validation an enterprise-class deployment needs
for stability, supportability, control and audit, the bar set by PII-bearing,
banking- or medical-class workloads.

A persistent agent (**Kent**) works with the operator and supervises the estate.
Project work is delegated to disposable, isolated teams (**Gents**: a CEO loop
driving CrewAI workers). Every model call passes through one gateway that
classifies it and routes it to a local model or to Claude Opus 5.5, falling back
to local when the cloud fails. Gents learn; Kent curates what they learn into a
shared template, so every new Gent starts smarter.

> **v0.1.0 is a release candidate of a reference implementation, not a certified
> system.** [`docs/controls.md`](docs/controls.md) lists every control, its
> evidence and status, including the gaps to close before processing regulated data.

Named after the Earl of Kent in Shakespeare's *King Lear*: loyal, honest,
competent, and willing to tell you when you're wrong.

---

## At a glance

```
Operator ── kent (Hermes profile "kent", runs as the operator)
              │  model "auto"
              ▼
   LiteLLM gateway 127.0.0.1:4000 ── identity auth (kent_gateway.py), every call logged
      router (local) ─► fast (local llama.cpp) │ smart / frontier (Claude Opus 5.5)
              │                                 └─ on failure → local fast
              ▲ local tiers only
   Gent containers (gent-<id>: own user, key, DB; read-only rootfs; internal network)
      └─ web: read-only via Kent's Squid proxy     └─ escalations → Kent → frontier

   Prometheus · node_exporter · Loki · Alloy · Grafana   (metrics, logs, dashboard)
   Gitea: stack template, per-Gent repos, alert issues  ·  HMAC audit chain
```

| Component | Version | Runs as |
|---|---|---|
| LiteLLM gateway | 1.100.1 | `litellm` |
| Local model | llama.cpp `llama-server` (Qwen3.6-35B-A3B on the reference machine) | operator |
| Frontier / smart | Claude Opus 5.5 (`claude-opus-5-5`) | via gateway |
| Agent harness | Hermes Agent v0.21.x (profile `kent`) | operator |
| Gent runtime | CrewAI 1.15.22, Python 3.12 image (digest-pinned) | `gent-<id>` |
| Metrics / logs | Prometheus 3.15.0, node_exporter 1.12.1, Loki 3.7.8, Alloy 1.20.0 | own accounts |
| Dashboards | Grafana 13.1.0 | `grafana` |
| Forge | Gitea 1.27.3 | `gitea` |
| Egress proxy | Squid 6 (own instance) | `kent-squid` |

## What makes it a reference

- **Minimum privilege:** own account per service and per Gent; empty capability
  sets; systemd exposure scores 1.2–2.9; gateway identities limit who can call which
  model; Gents can't reach cloud models, host services, other Gents or the LAN.
- **Everything logged and measured:** every model call and denial as structured
  JSON with identity and routing decision; journald → Loki; Prometheus scrapes
  every service; Grafana dashboard; HMAC-chained audit log anchored daily.
- **Controls with evidence:** a control matrix mapped to ISO 27001, SOC 2,
  NIST 800-53, HIPAA and PCI DSS requirement areas ([`controls.md`](docs/controls.md)).
- **Formal validation:** installation, operational and performance qualification
  (IQ/OQ/PQ) evidence: 99-check conformance run, 260 automated tests plus 24 in-container checks, end-to-end
  simulation, and verified rollback to the pre-install state.
- **Reversible by construction:** every module records what it creates; uninstall
  removes exactly that.

## Quick start

Requirements: Ubuntu 24.04, Docker, Hermes Agent with a `kent` profile, and a
llama.cpp server on `127.0.0.1:8080`. Details in [`docs/dev-notes.md`](docs/dev-notes.md).

```bash
cd install/services
for m in "litellm --config dev" prometheus node_exporter loki alloy grafana gitea kent-core gent; do
  set -- $m; sudo ./"$1"/install.sh "${@:2}" || break   # each module verifies itself
done
python3 ../../tests/conformance/conformance.py     # expect 0 FAIL
kent                                               # talk to Kent
```

Add Claude later: `sudo ./litellm/install.sh --set-anthropic-key`, then
`--config prod` and `--set-fallback-timeout 120`.

## Repository

```
docs/                 architecture, controls, conformance, privilege map, glossary, dev notes
install/services/     one module per component (install.sh, uninstall.sh, units, manifest)
install/dev/          development helpers (time-limited sudo grant)
configs/              gateway (dev/prod/sim), Prometheus, Loki, Alloy, Grafana, Gitea, Squid
schemas/              kent.sql (Kent) and stack.sql (per Gent)
templates/app/        Kent's SOUL.md and the Gent CEO/CrewAI package
skills/kent/          Kent's Hermes skill: crew-designer
tests/                unit, live, Gent isolation, simulation oracle, conformance check
```

## Documentation

| Document | Purpose |
|---|---|
| [Architecture](docs/architecture.md) | The design, section by section, marked Live or Planned |
| [Controls](docs/controls.md) | Control matrix with evidence, framework mapping, IQ/OQ/PQ, gaps |
| [Conformance](docs/conformance.md) | Living record of assessments, findings and open items |
| [Privilege map](docs/privilege-map.md) | Every account, port, path, credential and privileged action |
| [Developer notes](docs/dev-notes.md) | Install, run, simulate, test, roll back |
| [Glossary](docs/glossary.md) | Terms |
| [Service modules](install/services/README.md) | Module conventions and access model |

## Status

**v0.1.0 release candidate.** All components installed and verified on the
reference machine (RTX 2060 SUPER 8 GB, 64 GB RAM). Pending: an Anthropic key for
live Opus calls, and the gaps listed in [`controls.md`](docs/controls.md) and
[`conformance.md`](docs/conformance.md) §5. Short-term work is in [`TODO.md`](TODO.md).

## License

Apache 2.0, see [LICENSE](LICENSE). Third-party components and attributions: [NOTICE](NOTICE).

## Citation

```bibtex
@software{nielsen2026kent,
  author  = {Nielsen, Keith},
  title   = {Kent — Reference Agentic AI Stack},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/keith-nielsen/2026-AI-Harness-Kent}
}
```

See [`CITATION.cff`](CITATION.cff).

## Author

**Keith Nielsen** — [keith-nielsen](https://github.com/keith-nielsen)
