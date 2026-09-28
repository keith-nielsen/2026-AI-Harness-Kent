# Design: Kent's own account, deployment profiles, and the operator experience

| | |
|---|---|
| Status | Proposed (2026-09-28) — for implementation in v0.2 |
| Decisions | Kent runs under its own system account; two deployment profiles, `lab` and `hardened` |
| Driven by | External review F-01/F-02/F-06/F-14 (`docs/audit/2026-09-28-external-security-review.md`) |
| Supersedes | "Kent is the operator's Hermes profile, runs as the operator" (v0.1 decision) |

## 1. The short answers

- **Does the user `su - kent`?** No, never. `kent` is a service account like `postgres` or
  `grafana`: no password, no login shell, no one works "as" it. People keep working as
  themselves and talk to Kent through a `kent` command. Their actions stay attributable to
  them, which is a CCoP requirement (no shared accounts, cl. 5.2.1(c), 5.3.1(e)).
- **Do they end up in permission hell?** Not if one rule holds: **no directory is writable by
  both a human and Kent.** Data crosses the boundary in exactly three ways: the `kent` command
  streams it in (input), Gitea serves it out (results), and `kent gent export` streams a copy
  into the user's own directory, owned by the user. Kent never writes into anyone's home, and
  humans never write into Kent's directories.

## 2. Principles

1. **Kent is a service, not a person.** Account `kent:kent`, home `/var/lib/kent`, shell
   `nologin`, not in `docker`, `sudo` or `adm`.
2. **Humans act as themselves.** Each human who may use Kent is in group `kent-operators`
   and has their own gateway identity (`human-<login>`), their own Grafana and Gitea accounts,
   and their own keys in `~/.config/kent/` (0600).
3. **One owner per directory.** Everything Kent owns lives in `/var/lib/kent`, `/etc/kent` and
   `/opt/kent-*`. Human-owned files live only in human homes. Cross-boundary transfer is a
   stream or an HTTP API, never a shared writable folder.
4. **Kent brings its own Hermes.** A pinned, hash-verified Hermes install in `/opt/kent-hermes`
   with `HERMES_HOME=/var/lib/kent/hermes`. The operator's personal Hermes (`~/.hermes`) and
   Python environments are never touched and keep working independently.
5. **Profiles relax controls, not identities.** `lab` and `hardened` use the same accounts,
   paths and commands; `lab` switches individual controls off for development. There is one
   code path.
6. **Privilege is brokered.** Anything needing root or Docker goes through small root-owned
   tools that validate their arguments. Neither Kent nor humans need `docker` group membership
   for Kent work.

## 3. End-to-end journey

### 3.1 Get and install

```bash
git clone https://github.com/keith-nielsen/2026-AI-Harness-Kent.git && cd 2026-AI-Harness-Kent
sudo ./install.sh --profile lab            # or --profile hardened
```

`install.sh` (new, thin orchestrator over the existing modules):

1. **Preflight**, with a clear pass/fail list and exact fix commands:
   - Ubuntu 24.04, systemd, Docker, free ports, disk;
   - a local model endpoint (`--local-model-url`, default `http://127.0.0.1:8080/v1`), or in
     `hardened`, a model file to manage (`--local-model /path/model.gguf`, hashed and served by
     the managed `llama` module).
2. **Asks at most three questions** (all also available as flags for unattended installs):
   - which local model endpoint or file;
   - an optional Anthropic key (read without echo; can be added later);
   - which human is the first operator (default: the account that ran `sudo`).
3. **Installs the modules in order**, each self-verifying:
   `litellm → prometheus → node_exporter → loki → alloy → grafana → gitea → hermes → kent-core → gent`.
   If a module fails, the installer stops, names the module and its log, and prints the
   uninstall command for what was already installed.
4. **Enrols the first operator** (§4.2).
5. **Runs the conformance check** for the chosen profile and prints the summary.
6. **Prints the next steps**, e.g.:

```
Kent is installed (profile: lab). Next:
  kent                      talk to Kent
  kent status               health of every component
  Grafana  http://127.0.0.1:3001  (user: alice, password: ~/.config/kent/grafana_password)
  Gitea    http://127.0.0.1:3000  (user: alice, password: ~/.config/kent/gitea_password)
You were added to group kent-operators: log out and in once (or run: newgrp kent-operators).
```

The group-membership refresh is the one unavoidable Unix wrinkle. The installer says so
plainly, and `kent` detects a missing membership and explains what to do.

### 3.2 Daily use (always as yourself)

| You type | What happens |
|---|---|
| `kent` | Interactive chat with Kent (Hermes, as `kent`) |
| `kent -q "…"` | One question, one answer |
| `kent status` | Services, timers, Gents, audit chain, disk: one screen |
| `kent digest` | Today's digest |
| `kent gents` / `kent gent status ID` / `kent gent logs ID` | Supervise Gents |
| `kent new PROJECT_DIR` | Spawn a Gent from your project files (streamed in; your files stay yours) |
| `kent share FILE…` | Give Kent a file to read (copied into Kent's inbox for you) |
| `kent gent export ID [DEST]` | Copy a Gent's deliverables into DEST, owned by you |
| Gitea web UI / `git clone` | Browse or clone every Gent's published workspace and the template history |
| Grafana | Dashboards and logs, with your own login |

Nothing here needs `sudo`, `su` or knowing where Kent keeps its files.

### 3.3 Administration (explicitly privileged)

`sudo kent-admin <task>`:
- `add-operator LOGIN` / `remove-operator LOGIN`: group membership, gateway identity, Grafana
  and Gitea accounts, keys written into that user's `~/.config/kent/` *as that user*;
- `set-anthropic-key`;
- `set-fallback-timeout N`;
- `profile lab|hardened` (re-applies controls, then runs the conformance check);
- `rotate-keys`;
- `backup` / `restore`;
- `uninstall [--purge]`.

### 3.4 Uninstall

`sudo ./uninstall.sh [--purge]`: the modules in reverse, driven by their manifests (as today).
Kent's own data is kept unless `--purge`; operators' personal key files are listed for them to
delete, never deleted from their homes by root.

## 4. Identities, groups, privileges

### 4.1 Accounts and groups

| Name | Type | Purpose | Members / notes |
|---|---|---|---|
| `kent` | system user + group | Kent's runtime: Hermes, timers, tools | no login, not in `docker` / `sudo` / `adm` |
| `kent-operators` | group | humans allowed to use Kent | installer adds the first operator; `kent-admin add-operator` adds more |
| `gent-<id>` | system user + group | one per Gent (unchanged) | created and removed by the root broker |
| service accounts | system users | litellm, prometheus, loki, alloy, grafana, gitea, kent-squid (unchanged) | — |
| `llama` | system user | managed local model server (`hardened`; optional in `lab`) | GPU access via the `render`/`video` groups only |

### 4.2 How a human reaches Kent

`/usr/local/bin/kent` (one symlink, recorded in the manifest; the FHS location for software an
administrator installs locally) runs `/opt/kent-core/bin/kent`, which:

- **Local subcommands** (`status`, `digest`, `gents`, `gent status|logs`): reads Kent's
  group-readable status files and the Loki, Prometheus and Gitea APIs with the *human's* own
  credentials. No identity switch.
- **Chat** (`kent`, `kent -q`): `sudo -u kent /opt/kent-core/libexec/kent-chat` via a single
  sudoers rule:
  `%kent-operators ALL=(kent) NOPASSWD: /opt/kent-core/libexec/kent-chat`.
  - The wrapper takes no arguments that reach a shell.
  - It sets `HOME=/var/lib/kent` and `HERMES_HOME=/var/lib/kent/hermes`, clears the environment,
    and starts Hermes attached to the user's terminal.
  - It records `SUDO_USER` as the session's principal, so every Kent action in the audit chain
    carries the human who asked.
- **Transfers** (`new`, `share`, `gent export`): the human side packs or unpacks; the Kent side
  runs as `kent` through the same kind of fixed sudoers entry. Data moves through a pipe, so
  files always end up owned by whoever writes them.

Later option: run Hermes as a persistent `kent` service in ACP mode on a Unix socket owned by
`kent:kent-operators` (0660). The client then needs no sudo at all. This is kept as a v0.3
candidate once multi-session behaviour is verified.

### 4.3 Brokered privilege

| Broker (root-owned, argument-validated) | Called by | Replaces |
|---|---|---|
| `kent-spawn-gent`, `kent-destroy-gent` (exist) | `kent` | — |
| `kent-gent-ctl inspect\|logs\|stop\|start <id>` (new) | `kent`, the circuit breaker | Kent's direct `docker` calls (5 call sites) |
| `kent-admin` (new) | humans via `sudo` | ad-hoc module re-runs |

Sudoers: `kent ALL=(root) NOPASSWD: /opt/kent-gent/bin/kent-spawn-gent, /opt/kent-gent/bin/kent-destroy-gent, /opt/kent-gent/bin/kent-gent-ctl`.
Humans get no root rules for daily work.

## 5. Filesystem layout and permissions

| Path | Owner:group | Mode | Contents |
|---|---|---|---|
| `/opt/kent-hermes/` | root:root | 0755 | pinned Hermes (git checkout at a fixed commit + hash-locked venv) |
| `/opt/kent-core/` | root:root | 0755 | Kent tools, `kent` CLI, libexec wrappers |
| `/etc/kent/profile` | root:root | 0644 | `lab` or `hardened` |
| `/etc/kent/kent/` | root:kent | 0750 | `kent.conf`; `credentials/` (Kent's gateway key, Gitea token, audit secret) via `LoadCredential` |
| `/var/lib/kent/` | kent:kent | 0750 | Kent's home |
| `/var/lib/kent/hermes/` | kent:kent | 0700 | HERMES_HOME: profile, SOUL, skills, sessions, memory |
| `/var/lib/kent/kent.db`, `audit/` | kent:kent | 0600 / 0700 | Kent's database, audit chain |
| `/var/lib/kent/shared/` | kent:kent-operators | 2750 | status snapshot, digests: human-readable, Kent-written |
| `/var/lib/kent/inbox/<login>/` | kent:kent | 0700 | files from `kent share` / `kent new` (written by Kent from the stream) |
| `/var/lib/kent-gent/stacks/<id>/data/` | gent-<id>:kent | 2750 | Gent data; group `kent` (was the operator's group) |
| `/var/lib/kent-gent/archive/<id>/` | kent:kent | 0750 | retired Gents (were root:<operator>) |
| `~<human>/.config/kent/` | human | 0700 / 0600 | that human's gateway key, Grafana and Gitea passwords, written by `kent-admin` running *as that user* |

Kent's timers become **system units** running as `kent` with the usual hardening
(`ProtectHome=yes`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/kent`), instead of the
operator's user units with linger.

## 6. Where permission hell usually comes from, and how this design avoids it

| Trap | Typical symptom | Avoided by |
|---|---|---|
| Shared writable directories + different umasks | "Permission denied" on files a colleague or service created | No shared writable directories; transfers are streams, so the writer owns the file |
| Root creating files in a user's home | Root-owned `~/.config/...` the user can't edit (this happened once in v0.1 with the snapshot tool) | Anything in a home is written by `runuser -u <login>` |
| Service reading a user's home | Service can't read `~/project` (home is 0750) | The human-side CLI reads the files and streams them |
| Running the same tool as two users against one state directory | Hermes cache or SQLite owned by the wrong user; lock errors | Kent's Hermes state is only ever used as `kent` (the wrapper enforces it); the operator's Hermes is a separate install |
| Orphaned UIDs after deleting per-Gent users | Archive files owned by "1003" | Archives are re-owned to `kent:kent` on retire |
| SQLite read by another account | "attempt to write a readonly database" in WAL mode | Read-only access uses rollback-journal databases and `mode=ro` (as today) |
| Git repos shared between users | `fatal: detected dubious ownership` | Humans clone from Gitea into their own directories; no shared working copies |
| Group membership not active yet | "not in kent-operators" right after install | Installer and CLI detect it and say "log out/in or `newgrp kent-operators`" |
| Docker socket as a permission shortcut | Silent root equivalence | No `docker` group for Kent or for Kent work; brokers only |

## 7. Profiles

| Control | `lab` | `hardened` |
|---|---|---|
| Kent account, paths, CLI, brokers | same | same |
| Kent's tool surface | Hermes terminal as `kent` on the host (can't reach human homes, root or Docker) | Hermes terminal backend sandboxed (container) or tools limited to an allowlist (`kent …` subcommands, read-only diagnostics) |
| Approval prompts | may be bypassed (`kent -q`, scripts) | enforced; pre-approved allowlist only |
| Docker for Kent | via brokers; `--lab-docker` optionally adds `kent` to `docker`, with a loud warning | brokers only |
| Development sudo grant | available (`install/dev/agent-sudo.sh`), time-boxed | refused (installer removes it) |
| Local model | any endpoint the operator runs | managed `llama` account/unit, hash-listed models only |
| Gent egress | open public GET/CONNECT:443 | per-project domain allowlist + protective DNS |
| Cloud tiers | smart/frontier → Claude (if key) | same, plus redaction/DLP guardrail and a `local-only` switch for regulated data |
| Gateway limits | generous | per-identity RPM/TPM and daily budgets |
| Human UI login | local accounts per human | SSO/OIDC + MFA per human |
| Logs | local, 30 days | off-host, immutable, ≥ 12 months |
| Backups | optional | required, with restore tests |
| Conformance check | runs; lab-only relaxations reported as INFO "lab profile" | all hardened controls must PASS |

The profile is recorded in `/etc/kent/profile`, shown by `kent status` and in the digest, and
changed only by `sudo kent-admin profile …`, which re-applies controls and re-runs conformance.

## 8. What changes from v0.1

| Area | v0.1 | v0.2 |
|---|---|---|
| Kent's identity | the operator | `kent` system account |
| Hermes | operator's install, profile `kent` | Kent's own pinned install; operator's Hermes untouched and restored |
| Kent data | `~/.local/share/kent`, `~/.config/kent` | `/var/lib/kent`, `/etc/kent/kent` (migrated, audit chain preserved) |
| Timers | operator user units + linger | system units as `kent` |
| Gent data group | operator's primary group | `kent` |
| Docker from Kent code | direct (`docker` group) | `kent-gent-ctl` broker |
| Human access | implicit (it's you) | `kent-operators` group + per-human identity and keys |
| Entry point | nine module installers | `./install.sh --profile …` (modules remain for advanced use) |
| Gateway identities | `operator`, `kent`, `gent*`, `metrics` | `human-<login>` per person, `kent`, `gent*`, `metrics` |

Migration on an existing v0.1 host: `sudo ./install.sh --profile lab --migrate` moves Kent's
data, re-owns Gent data and archives, keeps the audit chain (same secret, so the chain continues),
restores the operator's original Hermes profile files, and prints the before/after conformance.

## 9. To verify during implementation

1. Hermes as a separate install with `HERMES_HOME` under `/var/lib/kent`: sessions, memory and
   skills all resolve inside it (the codebase references `HERMES_HOME`).
2. Concurrent chats from two operators against one `HERMES_HOME`: whether Hermes' state store
   tolerates it. If not, per-principal session directories.
3. The terminal attach through `sudo -u kent`: TTY, colours, resize and Ctrl-C behave normally.
4. A sandboxed terminal backend for `hardened` (Hermes supports several backends): choose and
   test one.
5. GPU access for a `llama` account (`render`/`video` groups) without touching other users.

## 10. Acceptance tests (added to the suite)

- **Fresh install as a second, unprivileged test user:** clone → `install.sh` → `kent -q` works
  without sudo, su or file errors.
- **Two operators:** each sees their own identity in gateway logs and the audit chain; neither
  can read the other's `~/.config/kent`.
- **Round trip:** `kent new` from a user-owned directory → Gent runs → `kent gent export` into
  the user's directory → every file owned by the user.
- **Isolation:** `kent` cannot read `/home/*`, cannot use Docker, cannot sudo beyond the brokers.
- **Profile switch lab ↔ hardened:** conformance reflects it; nothing breaks for the operator.
- **Uninstall:** baseline diff clean; the operator's own Hermes and files unchanged.

## 11. Kent's own Hermes: what to carry over, and how to configure it after install

*Decided 2026-09-28: Kent gets its own pinned Hermes install; v0.1 Kent data may be discarded (destructive conversion).*

### 11.1 The operator's Hermes compared with a fresh install (surveyed 2026-09-28)

| Area | Operator's install | Needed by Kent? |
|---|---|---|
| Code | Clean git checkout of upstream `ddb56d0`, no local modifications (11 commits behind) | Pin the same kind of checkout at a chosen commit |
| Python env | `uv`-managed venv (≈ 300 MB) from `uv.lock` (331 hashed packages) | Yes: `uv sync --frozen` into `/opt/kent-hermes` |
| Bundled tools | Chromium, agent-browser, Node, npm, ffmpeg under `~/.hermes/tools` (most of the 2.2 GB) | No (Kent does no browsing or media; Gents do web work) |
| Model | `custom` → LiteLLM `:4000` with a **stale key (401 since the last reinstall)** | Kent: `auto` via the gateway with Kent's key. Operator: gets a personal `human-<login>` key (§4.1) |
| Auxiliary models (compression, titles) | Direct to llama.cpp `:8080`, **bypassing the gateway** | Through the gateway (`fast` tier) so every call is logged and authorised |
| Web search | SearXNG backend, `http://localhost:8082` | Yes, but from a Kent-managed SearXNG (§11.3) |
| Toolsets (CLI) | 16 incl. terminal, code_execution, delegation, cronjob, kanban, image_gen, tts, vision, web | Per profile (§11.2) |
| Plugins | spotify | No |
| Approvals | `destructive_slash_confirm: false` | Lab: default; hardened: enforced |
| `.env` | 16 set values incl. personal **DEEPSEEK_API_KEY** and **OPENROUTER_API_KEY**, `SEARXNG_URL`, `TERMINAL_ENV=local`, debug flags | **Never copied.** Kent reaches cloud models only through the gateway |
| Auth | Nous Portal OAuth | No |
| Profiles | `freetier` (OpenRouter free model), `kent` (v0.1 Kent) | `kent` is retired from the operator's install, restored to pre-Kent state |
| Skills | ~135 incl. non-bundled ones, an archive, and `red-teaming/godmode` (a jailbreak skill) | Only Kent's reviewed skills (`kent/crew-designer` + an explicit allowlist); `godmode` and similar never |
| Memories / sessions / kanban / switchyard routing experiments | Personal (`MEMORY.md`, `USER.md`, 55 MB `state.db`) | No (Kent starts with an empty memory) |
| Messaging gateway | Configured, stopped, no platforms | Not in v0.2 (future: an approved channel for alerts) |
| SOUL.md | Stock Hermes | Kent's SOUL from `templates/app/SOUL.md` |

Conclusion: nothing in the operator's install is needed to reproduce Kent. The useful customisation
is **SearXNG as the search backend**; everything else is either personal (keys, memories, skills)
or should not follow Kent (direct model endpoints, jailbreak skill, cloud keys).

### 11.2 Configuration as code

Kent's Hermes is configured only from the repository, never by hand-editing
`/var/lib/kent/hermes`:

| Layer | File | Owner | Purpose |
|---|---|---|---|
| Pin | `install/services/versions.env`: `HERMES_REPO`, `HERMES_COMMIT`, sha256 of `uv.lock` | repo | what gets installed |
| Base | `configs/hermes/kent.yaml` | repo | model and auxiliary routing via the gateway, web backend, SOUL, skills allowlist |
| Profile policy | `configs/hermes/profile-lab.yaml`, `profile-hardened.yaml` | repo | permitted toolsets, terminal backend, approval mode |
| Site overrides | `/etc/kent/hermes/overrides.yaml` | root:kent 0640 | the operator's local choices (see below), validated against the profile policy |

Toolsets per profile:

| Toolset | lab | hardened |
|---|---|---|
| clarify, todo, memory, session_search, skills | ✓ | ✓ |
| web (SearXNG) | ✓ | ✓ |
| file | ✓ (Kent's home only) | read-only allowlist |
| terminal | ✓ (`local`, as `kent`) | sandboxed backend, or replaced by an allowlisted `kent` tool |
| code_execution, delegation, cronjob | ✓ | ✗ (Gents do execution; timers are system units) |
| image_gen, tts, vision, browser, kanban, spotify, MCP | ✗ unless overridden | ✗ |

After install, the operator changes Kent's Hermes with:

```bash
sudoedit /etc/kent/hermes/overrides.yaml      # e.g. web.base_url, extra approved skills, toolsets
sudo kent-admin hermes apply                  # validate against profile policy → write config as kent → smoke test → audit event
sudo kent-admin hermes skill add <path|hub-id> --review   # shows the skill, pins its hash, then enables it
sudo kent-admin hermes upgrade <commit>       # side-by-side install, tests, switch, keep previous for rollback
```

`apply` rejects anything the profile forbids (e.g. `terminal: local` under `hardened`), and every
change is an audit-chain event. Model keys and cloud credentials are never set in Hermes; they
live only in the gateway.

### 11.3 SearXNG becomes a Kent module

Today SearXNG is a hand-started container (`searxng/searxng:latest`, config in `~/searxng`),
**published on 0.0.0.0:8082**. Docker-published ports bypass UFW, so it is reachable from the LAN.
As a module (`install/services/searxng`):

- **Image and exposure:** image pinned by digest; listening on `127.0.0.1` (Kent) and on the Gent
  bridge through a socket proxy (Gents).
- **Configuration and secrets:** config in `/etc/kent/searxng/settings.yml`, seeded from the
  operator's current engine choices; `secret_key` via a credential file; limiter on.
- **Logging:** logs to journald → Loki.
- **Gent search:** Gents switch from scraping DuckDuckGo HTML to SearXNG's JSON API. This is a
  structured retrieval API, as the Agentic Addendum recommends (addresses part of review F-12).
- **Operator's personal Hermes:** can use the same instance (read-only HTTP; no permission
  coupling), and the hand-run container can then be retired.
