# Contributing to Kent

**Status: v0.1.0 release candidate. Public contributions are not open yet**, but
issues and discussion are welcome. Security issues: see [SECURITY.md](SECURITY.md).

## Development setup

See [`docs/dev-notes.md`](docs/dev-notes.md): prerequisites, installing the
modules, the dev/prod/sim gateway configs, simulations, tests and rollback checks.

## Conventions

- **Modules** (`install/services/<name>/`): follow [`install/services/README.md`](install/services/README.md).
  Own account per service; code in `/opt/kent-<name>`, config in `/etc/kent/<name>`,
  state in `/var/lib/<name>`; record everything in the manifest; never adopt or
  modify what Kent didn't create; drop-ins instead of editing vendor files;
  `--dry-run` support; loopback-only listeners; hardened units (exposure ≤ 3.0).
- **Artifacts:** pin and verify: SHA-256 in `install/services/versions.env`,
  `--require-hashes` lock files, digest-pinned images. No `curl | sh`, no `latest`.
- **Secrets:** systemd `LoadCredential` or read-only file mounts; never environment
  files, never the repo.
- **Shell:** `set -euo pipefail`; clean `shellcheck -S warning`.
- **Python:** standard library where possible; Gent and Kent tools treat any
  model or Gent output as untrusted data.
- **Docs:** update [`docs/architecture.md`](docs/architecture.md) (Live/Planned),
  [`docs/controls.md`](docs/controls.md) and [`docs/conformance.md`](docs/conformance.md)
  with any behaviour change.

## Required checks before a change

1. CI (`.github/workflows/validate.yml`): shellcheck, config/Python validation, pinned artifacts, unit tests.
2. On a test machine: install the changed module, `tests/conformance/conformance.py` with 0 FAIL,
   and for install changes a rollback diff against a pre-install snapshot.
3. Security-relevant changes: add adversarial tests next to the existing ones in `tests/`.

## Code of Conduct

A Code of Conduct will be published when contributions open.
