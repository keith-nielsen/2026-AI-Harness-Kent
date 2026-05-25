# Kent — Enterprise Agentic AI Stack

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Architecture](https://img.shields.io/badge/Architecture-v2.3.0-green)](docs/architecture.md)
[![Status](https://img.shields.io/badge/Status-Pre--alpha-yellow)](.)
[![Validate](https://github.com/keith-nielsen/2026-AI-Harness-Kent/actions/workflows/validate.yml/badge.svg)](.github/workflows/validate.yml)

> **Originated:** 2026 · **Current release:** [v1.0](https://github.com/keith-nielsen/2026-AI-Harness-Kent/releases/tag/v1.0) · **Status:** Pre-alpha / Architecture phase

A local-first agentic AI stack with tiered inference, fractal task delegation,
and a Shakespeare-inspired operational personality.

Kent is a self-hosted system where a persistent AI agent manages your computing
estate, delegates complex work to disposable project-scoped agents (Gents),
and routes inference across local GPU models and cloud frontier APIs — all
under your control, on your hardware, with your data staying local.

**Named after the Earl of Kent from Shakespeare's *King Lear***: loyal, honest,
competent, and willing to tell you when you're wrong.

---

## Repository Structure

```
├── docs/               # Architecture, privilege map, glossary, dev notes
├── install/            # 9-phase installation system (install.sh + phase scripts)
├── configs/            # LiteLLM routing, observability, egress proxy
├── schemas/            # SQLite schemas (kent.db + stack.db)
├── scripts/            # Operational: spawn/destroy Gents, QA audit, digests
├── skills/             # Kent's Hermes agent skills (crew-designer, etc.)
│   └── kent/crew-designer/
├── templates/          # Docker Compose, Dockerfile, Gent app code + SOUL.md
├── .github/workflows/  # CI: lint, YAML validation, structure checks
├── CITATION.cff        # Machine-readable citation metadata
├── LICENSE             # Apache 2.0
└── NOTICE              # Third-party attribution
```

## Architecture at a Glance

```
Human (enterprise)
  └── Kent (persistent agent — host OS, systemd)
        ├── Inference Gateway (LiteLLM → Ollama local + cloud frontier)
        │     ├── T0 Router    — Gemma 4 4B      (classify, route)
        │     ├── T1 Fast      — Gemma 4 26B-A4B (extraction, formatting)
        │     ├── T2 Smart     — Qwen3.6-27B     (reasoning, coding, planning)
        │     └── T3 Frontier  — DeepSeek V4 / Opus 4.7 (escalations, QA)
        │
        ├── Gent-a3f7c291 (project stack — Docker, isolated)
        │     ├── CEO agent (Hermes-derived)
        │     └── CrewAI workers (role-based, YAML-configured)
        │
        ├── Gitea (version control, template repo, audit trail)
        ├── Grafana + Prometheus + Loki (observability)
        └── Squid egress proxy (outbound security)
```

Full details: [`docs/architecture.md`](docs/architecture.md) (v2.3.0)

## Key Design Decisions

- **Four-tier brain**: Right-size inference to task complexity. 95% of work stays
  local; frontier cloud handles the remaining 5%.
- **Gateway abstraction**: All consumers call logical model names (`router`,
  `fast`, `smart`, `frontier`). Swap models or add cluster nodes via config
  change — zero application code changes.
- **Fractal delegation**: Kent spawns isolated Gents (project stacks) from a
  template. Each Gent runs in Docker with scoped credentials, its own database,
  and no access to other Gents or Kent's data.
- **Directional access control**: Gents can call fast + smart. Only Kent can call
  frontier. Escalation flows upward, never sideways.
- **Knowledge sharing**: Gents publish learnings. Kent evaluates, cross-pollinates,
  and evolves the template. Global improvements propagate to future Gents
  automatically.
- **Security by default**: Secrets via mounted files (never env vars), per-entity
  Unix users, read-only container filesystems, egress proxy with POST denied by
  default, HMAC-chained audit logs.

## Target Hardware

**Phase 1 (single box):** AMD Ryzen AI Max+ 395, 128GB unified RAM / 96GB VRAM
(Framework Desktop or equivalent Strix Halo platform).

**Dev environment:** Any Linux box with an NVIDIA or AMD GPU (tested on 8GB VRAM
with smaller models). Use `--dev` flag for dev-mode model selection:
```bash
sudo ./install.sh --dev
```

**Phase 2 (cluster):** Add a second node for larger models. Gateway abstraction
enables migration via config change.

## Documentation

| Document | Purpose |
|----------|---------|
| [Architecture Blueprint](docs/architecture.md) | Full system design (v2.3) — entity hierarchy, tier routing, security, observability |
| [Installation Guide](install/install.sh) | 9-phase installer with test gates (--dev, --cloud, --dry-run flags) |
| [Privilege Map](docs/privilege-map.md) | Every filesystem path, ownership, permission, and process — security reference |
| [Glossary](docs/glossary.md) | Kent/Gent/Human/Ent naming, fractal stacks, tiers, seed-from |
| [Developer Notes](docs/dev-notes.md) | Run modes, model configs, known limitations |
| [SQL Schemas](schemas/) | `kent.sql` (estate DB) + `stack.sql` (per-Gent project DB) |
| [Crew Templates](skills/kent/crew-designer/templates/) | Research, content, and analysis crew configurations |

## Project Status

**Pre-alpha / Architecture phase.** The design is stable (v2.3.0 of the
architecture blueprint); installation scripts are being validated. Not yet
suitable for production use.

### Roadmap

- [x] Architecture blueprint finalized (v2.3.0)
- [x] Installation scripts (9 phases with test gates)
- [x] SQL schemas for estate and per-Gent databases
- [x] LiteLLM gateway configs (production, dev, cloud modes)
- [ ] Validation: E2E test on target hardware
- [ ] First Gent spawn from template
- [ ] Knowledge sharing cross-pollination verified
- [ ] Production hardening and documentation

## License

Apache 2.0 — see [LICENSE](LICENSE).

Kent includes code derived from [Hermes Agent](https://github.com/NousResearch/hermes-agent)
by Nous Research (MIT License) — see [NOTICE](NOTICE).

## Citation

If you use Kent in your work, please cite:

```bibtex
@software{nielsen2026kent,
  author  = {Nielsen, Keith},
  title   = {Kent — Enterprise Agentic AI Stack for High-Governance Environments},
  year    = {2026},
  doi     = {10.5281/zenodo.TODO},
  url     = {https://github.com/keith-nielsen/2026-AI-Harness-Kent}
}
```

See [`CITATION.cff`](CITATION.cff) for machine-readable metadata.

## Author

**Keith Nielsen** — [keith-nielsen](https://github.com/keith-nielsen) on GitHub