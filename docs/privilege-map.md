# Kent — Privilege Map

Every account, path, port, credential and privileged action a Kent install
creates, with owner and mode. Source of truth: the module manifests in
`/var/lib/kent-install/manifest/<module>` (what uninstall removes) and the installers in
`install/services/`. Verified on the reference machine for v0.1.0-rc (kent-account layout);
`tests/conformance/conformance.py` re-checks accounts, listeners, hardening and
secret file modes.

`<op>` = a human operator (member of `kent-operators`; the installer enrols whoever ran it).

---

## 1. Accounts

| Account:group | Created by | Home / shell | Runs | Removed by |
|---|---|---|---|---|
| `litellm:litellm` | litellm module | none / nologin | `kent-litellm.service` | litellm uninstall |
| `prometheus:prometheus` | prometheus module | none / nologin | `kent-prometheus.service` | prometheus uninstall |
| `node_exporter:node_exporter` | node_exporter module | none / nologin | `kent-node-exporter.service` | node_exporter uninstall |
| `loki:loki` | loki module | none / nologin | `kent-loki.service` | loki uninstall |
| `alloy:alloy` | Alloy apt package (installed by Kent) | package default | `alloy.service` (+ Kent drop-in) | `apt purge` via alloy uninstall |
| `grafana:grafana` | Grafana package (pre-existing, not Kent's) | package default | `grafana-server.service` (+ Kent drop-in) | never (not Kent's) |
| `gitea:gitea` | gitea module | none / nologin | `kent-gitea.service` | gitea uninstall |
| `kent-squid:kent-squid` | gent module | none / nologin | `kent-squid.service` | gent uninstall |
| `kent-searxng:kent-searxng` | searxng module | `/nonexistent` / nologin | the `kent-searxng` container (`--user`; never root) | searxng uninstall |
| `kent-llama:kent-llama` | llama module | `/nonexistent` / nologin, locked password, no sudo | `kent-llama.service` (on demand); reads models via group `kent-models` (unit `SupplementaryGroups`, not membership) | llama uninstall |
| `kent:kent` | hermes module | `/var/lib/kent` / nologin | Kent (its pinned Hermes, via `kent`), Kent's 5 timers | hermes uninstall |
| `gent-<id>:gent-<id>` | `kent-spawn-gent` | `/nonexistent` / nologin | container `kent-gent-<id>` | `kent-destroy-gent` |
| DynamicUser | systemd | — | `kent-gent-egress.service`, `kent-gent-gateway.service` | automatic |
| `<op>` | — (existing) | existing | the `kent` command (as themselves), incl. `kent llama start\|stop` | — |

Group `kent-operators` (hermes module): humans allowed to use Kent. Membership is
the only thing that lets a person reach Kent; nobody logs in or `su`s to `kent`.
It also grants, through the polkit rule `/etc/polkit-1/rules.d/60-kent-llama.rules`,
start/stop/restart of `kent-llama.service` and nothing else (no other unit, no
enable/mask/edit); the `kent` account is excluded by name.

Group `kent-models` (llama module): readers of the model directory in the **hardened**
profile (`root:kent-models`, 0750; files 0440). Members: the installing operator, hardened
only. The service gets it per unit. Never `kent`, a Gent or another service account
(conformance checks this). In the **lab** profile the model files stay operator-owned and
world-readable (0444: Kent removes only write bits), so the group has no members and no file
depends on it.

No account is shared between services. The installers refuse to adopt an
existing account or group they did not create.

The kent account is in no supplementary group: not `docker` (Gent containers are
reached only through the root brokers in §5), not `adm` (it reads its audit anchors
from Loki), not `sudo`.

---

## 2. Listening ports

| Address | Service | Account | Reachable from |
|---|---|---|---|
| 127.0.0.1:4000 | LiteLLM gateway | litellm | host |
| 127.0.0.1:9090 | Prometheus | prometheus | host |
| 127.0.0.1:9100 | node_exporter | node_exporter | host |
| 127.0.0.1:3100, :9095 | Loki HTTP, gRPC | loki | host |
| 127.0.0.1:12345 | Alloy UI/metrics | alloy | host |
| 127.0.0.1:3001 | Grafana | grafana | host |
| 127.0.0.1:3000 | Gitea | gitea | host |
| 127.0.0.1:3129 | kent-squid (egress proxy) | kent-squid | host (via the bridge for Gents) |
| 127.0.0.1:8888 | SearXNG (`kent-searxng`, container) | kent-searxng, no capabilities | host |
| 172.30.0.1:8888 | search bridge (socket proxy) | DynamicUser | Gent network only |
| 172.30.0.1:4000 | gateway bridge (socket proxy) | DynamicUser | Gent network only |
| 172.30.0.1:3129 | egress bridge (socket proxy) | DynamicUser | Gent network only |
| 127.0.0.1:8080 | llama-server (`kent-llama.service`, on demand) | `kent-llama` | host |

UFW (if active): `allow in on kent-gent0 to 172.30.0.1 port 3129,4000,8888 proto tcp`, recorded and removed on uninstall.

---

## 3. Paths

### 3.1 Code (root-owned, read-only to services)

| Path | Owner | Mode | Contents |
|---|---|---|---|
| `/opt/kent-litellm/` | root:root | 0755 | venv (111 hash-locked packages), `lib/kent_gateway.py`, `bin/kent-litellm-start` |
| `/opt/kent-prometheus/` | root:root | 0755 | `prometheus`, `promtool` |
| `/opt/kent-node-exporter/` | root:root | 0755 | `node_exporter` |
| `/opt/kent-loki/` | root:root | 0755 | `loki` |
| `/opt/kent-gitea/` | root:root | 0755 | `gitea` |
| `/opt/kent-hermes/` | root:root | 0755 | `src/` (Hermes at a pinned commit, read-only), `venv/` (`uv sync --frozen`), `bin/uv`, `bin/tirith` (pinned pre-exec command scanner), `bin/kent-hermes` (launcher; clean environment) |
| `/opt/kent-core/` | root:root | 0755 | `bin/*.py` Kent tools + `kent-*` names, `bin/kent` (human CLI), `libexec/kent-exec` (Kent's side), schemas |
| `/usr/local/bin/kent` | root:root | symlink | → `/opt/kent-core/bin/kent` |
| `/opt/kent-gent/bin/kent-spawn-gent`, `kent-destroy-gent`, `kent-gent-ctl` | root:root | 0755 | root brokers (sudo, §5) |

### 3.2 Configuration and credentials

| Path | Owner | Mode | Notes |
|---|---|---|---|
| `/etc/kent/` | root:root | 0755 | |
| `/etc/kent/litellm/` | root:litellm | 0750 | `config.yaml` (rendered), `litellm.env` (`FALLBACK_TIMEOUT`) |
| `/etc/kent/litellm/credentials/` | root:root | 0700 | `operator_key`, `kent_key`, `gent_key`, `metrics_key`, `anthropic_api_key` (0600) → `LoadCredential` |
| `/etc/kent/litellm/gent-keys/` | root:litellm | 0750 | `<id>.key` (0640), one per live Gent |
| `/etc/kent/prometheus/` | root:prometheus | 0750 | `prometheus.yml`; `credentials/litellm_metrics_key` (0640) |
| `/etc/kent/loki/` | root:loki | 0750 | `loki.yaml` |
| `/etc/kent/alloy/` | root:alloy | 0750 | `config.alloy` |
| `/etc/kent/grafana/` | root:grafana | 0750 | dashboards; `credentials/admin_password` |
| `/etc/kent/gitea/` | root:gitea | 0750 | `app.ini` (read-only to Gitea); `credentials/` secrets via `*_URI` (0640) |
| `/etc/kent/squid/` | root:kent-squid | 0750 | `squid.conf` |
| `/etc/kent/searxng/` | root:root | 0755 | `settings.yml`; `credentials/env` (`SEARXNG_SECRET`, 0600) |
| `/etc/kent/profile` | root:root | 0644 | `lab` or `hardened` |
| `/etc/kent/hermes/managed/` | root:kent | 0750 | Hermes managed scope: `config.yaml` (base + profile policy), `.env` (gateway key, SearXNG URL, tirith pins), both 0640; overrides Kent's own config |
| `/etc/kent/kent/` | root:kent | 0750 | `kent.conf`; `credentials/` `litellm_kent_key`, `gitea_kent_token`, `audit_hmac_secret` (0640 root:kent) |
| `/etc/grafana/provisioning/{datasources,dashboards}/kent.yaml` | root:grafana | 0640 | provisioning (vendor dirs, Kent files) |
| `/etc/systemd/system/kent-*.service`, `*.socket` | root:root | 0644 | Kent units |
| `/etc/systemd/system/{alloy,grafana-server}.service.d/kent.conf` | root:root | 0644 | drop-ins (vendor units untouched) |
| `/etc/sudoers.d/91-kent-gent`, `92-kent-operators` | root:root | 0440 | §5 |

### 3.3 State

| Path | Owner | Mode | Kept on uninstall? |
|---|---|---|---|
| `/var/lib/litellm/` | litellm | 0750 | only with `--purge-state` removed |
| `/var/lib/prometheus/`, `/var/lib/loki/`, `/var/lib/gitea/` | own account | 0750 | same |
| `/var/lib/alloy/` | alloy | 0750 | same |
| `/var/lib/grafana/` | grafana | 0755 | pre-existing content moved aside to `/var/lib/kent-install/backup/grafana/`, restored on uninstall |
| `/var/lib/kent-squid/`, `/var/log/kent-squid/` | kent-squid | 0750 | same |
| `/var/lib/kent-gent/` | root | 0755 | same |
| `/var/lib/kent/` | kent | 0750 | `kent.db` (0600), `audit/` (0700), `digests/`, `inbox/<op>/` (projects handed over by `kent new`), `hermes/` (Kent's Hermes home: sessions, memory, skills), `work/` |
| `/var/lib/kent-gent/stacks/<id>/data/` | gent-<id>:kent | 2750 (setgid) | Gent read/write, Kent read-only |
| `/var/lib/kent-gent/stacks/<id>/inbox/` | kent:gent-<id> | 0750 | Kent writes, Gent reads (mounted read-only) |
| `/var/lib/kent-gent/keys/<id>.key` | root:gent-<id> | 0440 | mounted read-only at `/run/kent/gent_key` |
| `/var/lib/kent-gent/registry/` | root | 0700 | Gents `kent-destroy-gent` may remove |
| `/var/lib/kent-gent/archive/<id>/` | root:kent | 0750 / files 0640 | retired Gents, read-only |
| `/var/lib/kent-install/manifest/<module>` | root | 0644 | install records (removed with `--purge-state`) |
| `/var/lib/kent-install/backup/<module>/…` | root | 0700 | moved-aside pre-existing paths |
| `/var/lib/kent-install/logs/<time>/` | root | 0700 | per-module logs of each `./install.sh` run |

### 3.4 Operator files

| Path | Mode | Contents |
|---|---|---|
| `~/.config/kent/` | 0700 | the operator's own credentials: `litellm_operator_key`, `gitea_admin_password`, `grafana_admin_password` (0600) |

Nothing of Kent's lives in an operator's home. Files move between an operator and
Kent only as a stream through `kent` (`kent new DIR` sends three project files;
`kent gent export ID DIR` receives a Gent's workspace, written as the operator).

---

## 4. Scheduled jobs (system units, `User=kent`)

| Timer | Schedule | Does |
|---|---|---|
| `kent-poll-learnings` | every 1 min | escalation relay, learning review, template commits, circuit breaker |
| `kent-audit-ingest` | every 5 min | gateway denials, install events, sudo commands → audit chain |
| `kent-qa-audit` | 02:00 | nightly QA sample on frontier |
| `kent-audit-anchor` | 04:30 | verify + anchor the audit chain in the journal |
| `kent-digest` | 07:00 | daily digest |

All run with `ProtectSystem=strict`, `ProtectHome=yes`, `NoNewPrivileges=yes`, except
`kent-poll-learnings`, which calls the Gent brokers through sudo and so runs with
`ProtectSystem=full` and without `NoNewPrivileges`.

---

## 5. Privileged actions

| Who | May run | As | Constraint |
|---|---|---|---|
| `%kent-operators` | `/opt/kent-core/libexec/kent-exec` (`/etc/sudoers.d/92-kent-operators`, NOPASSWD; `closefrom_override` so `kent --grant` can pass folder descriptors) | kent | the only way into Kent; kent-exec validates every argument, caps payloads (64 KiB question, 200 MiB export) and records `human:<name>` in the audit chain |
| `kent` (outside the sandbox: timers, kent-exec, kent-broker) | `kent-spawn-gent`, `kent-destroy-gent`, `kent-gent-ctl` (`/etc/sudoers.d/91-kent-gent`, NOPASSWD) | root | Kent's Hermes is sandboxed (`no_new_privs`) and reaches these only through kent-broker; root-owned brokers; stack id must be 8 hex; project files read with `O_NOFOLLOW`, regular files only, 256 KiB max; destroy and ctl act only on Gents in the root-only registry; ctl allows inspect/logs/stop/start |
| `<op>` with sudo | `./install.sh`, `./uninstall.sh`, `./kent-admin`, the module installers | root | normal sudo with password (a time-limited development grant, `install/dev/agent-sudo.sh`, exists for unattended build/test runs and expires automatically) |

Every sudo command is recorded by sudo in the journal and ingested into Kent's audit chain.

**Kent's broker** (`kent-broker.socket` → `kent-broker@.service`, `User=kent`, `/run/kent-broker.sock`
0600 kent; `docs/design/kent-sandbox.md` §4) is the only way out of Kent's sandbox: it runs
kent-gent, kent-audit (`verify`, `append` as entity `kent` only), kent-digest, kent-qa-audit,
kent-poll-learnings and the notices hook with allowlisted arguments, and audits each request.

---

## 6. Gent container

| Property | Value |
|---|---|
| User | `gent-<id>` uid:gid |
| Filesystem | read-only rootfs; tmpfs `/tmp` (256 MB, nosuid, nodev); `/data` (read/write, own stack); `/inbox` (read-only); `/run/kent/gent_key` (read-only) |
| Privileges | `--cap-drop ALL`, `no-new-privileges`, `--init` |
| Limits | 2 GB memory, 2 CPUs, 256 pids; restart on failure ×3 |
| Network | `kent-gent-net` (internal); reaches only 172.30.0.1:4000 (gateway) and :3129 (proxy) |
| Logs | journald, tag `kent-gent-<id>` |
