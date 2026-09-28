# Kent service modules

Each supporting service is installed by its own module under `install/services/<name>/`
(`install.sh`, `uninstall.sh`, units, and a manifest of everything it created). The
modules are the only installer; the earlier monolithic `install/phase_*.sh` scripts
were retired in v0.1.0-rc.2.

Status as of 2026-09-28 (all installed and verified on the reference machine). Normally
all are installed by `sudo ./install.sh` at the repository root, in the order below.

| Module | What | Account | Listens |
|---|---|---|---|
| `litellm` | LiteLLM 1.100.1 gateway, classify-then-route (`auto`), identity auth (`kent_gateway.py`), fallback to local | `litellm` | 127.0.0.1:4000 |
| `prometheus`, `node_exporter` | Prometheus 3.15.0, node_exporter 1.12.1 | `prometheus`, `node_exporter` | :9090, :9100 |
| `loki`, `alloy` | Loki 3.7.8; Grafana Alloy 1.20.0 (apt) shipping journald to Loki | `loki`, `alloy` | :3100, :12345 |
| `grafana` | vendor Grafana 13.1.0 via drop-in + provisioning ("Kent overview") | `grafana` | :3001 |
| `gitea` | Gitea 1.27.3, SQLite; `kent/stack-template`, `kent/gent-<id>` repos | `gitea` | :3000 |
| `searxng` | SearXNG metasearch (digest-pinned container, systemd-managed, read-only, no capabilities) | `kent-searxng` | 127.0.0.1:8888 |
| `hermes` | the `kent` account + `kent-operators` group; Kent's own Hermes (commit-pinned, `uv sync --frozen`) and the tirith command scanner (pinned) in `/opt/kent-hermes`; managed policy (`configs/hermes/base.yaml` + `profile-<lab\|hardened>.yaml`), SOUL, bundled skills + `kent/crew-designer` | `kent` | — |
| `kent-core` | Kent's tools (audit chain, digest, learnings review + escalation relay, QA, `kent-gent`), system timers as kent, the `kent` command and `kent-exec` entry point; enrols the installing operator | `kent` | — |
| `llama` | llama.cpp `llama-server` (root-owned copy of the operator's build) as on-demand `kent-llama.service`; models `root:kent-models` 0440 via read-only bind mount `/srv/kent/models`, SHA-256-checked at each start; polkit start/stop for `kent-operators`; CPU tuning oneshot | `kent-llama`; group `kent-models` | 127.0.0.1:8080 |
| `gent` | Gent runtime: internal Docker network, egress proxy, bridge sockets (gateway, proxy, search), image, spawn/destroy/ctl brokers | `kent-squid`; one `gent-<id>` per Gent | 127.0.0.1:3129; 172.30.0.1:3129/4000/8888 |

Order: llama → litellm → prometheus/node_exporter → loki → alloy → grafana → gitea → searxng → hermes → kent-core → gent.
Uninstall in reverse. `snapshot.sh take/diff` records machine state before/after for rollback checks.

No PostgreSQL: the gateway authenticates by identity without a database; Gitea and
Grafana use SQLite.

## Conventions (every module)

- **Own account per service.** Packaged tools use the account their package creates
  (e.g. `grafana`, `alloy`). Tools Kent installs itself get a dedicated
  `useradd --system --user-group --no-create-home --shell /usr/sbin/nologin` account.
  Kent itself is the `kent` account (home `/var/lib/kent`, no login, no supplementary
  groups); only Kent's own processes run as it.
- **Standard locations.** Code in `/opt/kent-<name>/` (root-owned, read-only to the service),
  config in `/etc/kent/<name>/` (`root:<account>` 0640), secrets in
  `/etc/kent/<name>/credentials/` (root-only, delivered with systemd `LoadCredential`),
  state in `/var/lib/<name>/` (systemd `StateDirectory`), logs to journald.
- **Never take over what Kent didn't create.** `lib-service.sh` refuses to adopt an
  existing account, group, path or unit that isn't in the module's manifest
  (`/var/lib/kent-install/manifest/<name>`). Vendor config files are never overwritten; use the
  vendor's drop-in or override mechanism instead. Uninstall removes only what the
  manifest lists and keeps state unless `--purge-state` is given.
- **Containers run as registered host accounts** (`--user`), never as root or as an id that exists only
  inside the image, so the host's account allocator cannot hand the same id to someone else.
- **Units are namespaced** (`kent-<name>.service`) and listen on `127.0.0.1` only.
- **Hardening baseline** as in `litellm/kent-litellm.service`: `NoNewPrivileges`,
  `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`, kernel/cgroup
  protections, empty capability set, `@system-service` syscall filter, `UMask=0077`.
  Relax per service only where it breaks, with a comment saying why.
- **Pinned, verified artifacts.** Python deps are hash-locked (`--require-hashes`);
  binaries are pinned to a version and verified against the upstream checksum (and
  signature where published). No `curl | sh`, no unpinned `latest`.
- **`--dry-run`** on every install/uninstall prints each privileged action without running it.
- **Humans use the `kent` command** as themselves; Kent runs as the `kent` account. Nothing a
  module installs requires anyone to log in as a service account.

## Access model (single gateway)

All model traffic goes through the gateway. `kent_gateway.py` maps each key to an identity:

| Identity | Models | Routes |
|---|---|---|
| `operator`, `kent` | all (router, fast, smart, frontier, auto) | all |
| `gent` and each `gent-<id>` | router, fast (local only) | chat/completions, models; deny-by-default request shape |
| `metrics` | none | `/metrics/` |

Per-Gent keys live in `/etc/kent/litellm/gent-keys/<id>.key` and are picked up without a
restart. Configs: `configs/litellm_config.dev.yaml` (all local), `configs/litellm_config.yaml`
(prod: smart/frontier → Claude Opus 5.5), `configs/litellm_config.sim.yaml` (simulation only:
smart/frontier → the oracle fixture in `tests/sim/oracle.py`), `configs/litellm_config.sim-routed.yaml`
(prod routing with the oracle in place of Anthropic).

**FALLBACK_TIMEOUT** (`/etc/kent/litellm/litellm.env`, set with `litellm/install.sh
--set-fallback-timeout N`): how long a cloud call may take before falling back to local.
Keep it at 120 once an Anthropic key is configured; see the warning at the top of
`litellm/install.sh`.

## Gents

An operator runs `kent new ./project` (or asks Kent); Kent runs `kent-gent spawn --name N
--project DIR` as the kent account. The root-owned `/opt/kent-gent/bin/kent-spawn-gent`
(allowed for the kent account only by `/etc/sudoers.d/91-kent-gent`) creates:

- account `gent-<id>`, data in `/var/lib/kent-gent/stacks/<id>/data` (`gent-<id>:kent`,
  setgid 2750: Kent reads, never writes), inbox for Kent's answers (read-only in the container);
- a gateway key (identity `gent-<id>`, local tiers only) and a private Gitea repo `kent/gent-<id>`;
- container `kent-gent-<id>` on `kent-gent-net` (internal: no route out), read-only rootfs,
  no capabilities, no-new-privileges, pids/memory/CPU limits, logs to journald → Loki.

A Gent can reach exactly three host services, through systemd socket proxies on 172.30.0.1:
the gateway (:4000), SearXNG (:8888, search) and the egress proxy (:3129; GET/HEAD and
CONNECT:443 to public addresses only). Kent inspects, stops and starts Gent containers only
through the root broker `kent-gent-ctl`, which acts on registered Gents only; the kent
account is not in the `docker` group. The inherited `LEARNINGS.md` from `kent/stack-template` is copied in at spawn.

Lifecycle: the Gent CEO runs tasks in order; an escalation (flagged, router-judged, or
after one failed retry) blocks the task and later ones; `kent-poll-learnings` answers it on
the frontier tier (budget 5 per Gent per 24 h) into the inbox; the task resumes. On
completion the Gent writes `REPORT.md` and publishes learnings; Kent reviews them (smart
tier) and commits confident adoptions to the template, where future Gents inherit them.
`kent-gent assess` publishes the workspace to Gitea and records Kent's assessment;
`kent-gent destroy ID` retires the Gent (archived read-only; `--purge` deletes data).

Circuit breaker (checked every minute by `kent-poll-learnings`): a failed task (after one retry)
or 3 escalations of one task halts the Gent: container stopped, Gitea issue on `kent/gent-<id>`,
registry `paused`. `kent-gent resume ID` drops a resume token in the inbox; the CEO retries the
failed tasks. Spawning is refused at 90% disk. Other lifecycle: `pause`, `restart` (recreate on
the current image, state kept).

## Uninstall

`sudo ./uninstall.sh [--purge]` at the repository root removes every module in reverse
order. Per module, `sudo install/services/<module>/uninstall.sh [--purge-state] [--dry-run]` removes exactly
what the module's manifest lists: units and drop-ins, files, packages Kent installed,
restores vendor unit states and moved-aside paths, then removes accounts. State (data,
logs, archives) is kept unless `--purge-state`. The gent module first destroys every
registered Gent (archiving them unless purging) and removes its firewall rules, Docker
network and images.
