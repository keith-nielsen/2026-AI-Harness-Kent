# Kent — Privilege Map

Every account, path, port, credential and privileged action a Kent install
creates, with owner and mode. Source of truth: the module manifests in
`/var/lib/kent/manifest/<module>` (what uninstall removes) and the installers in
`install/services/`. Verified on the reference machine for v0.1.0-rc;
`tests/conformance/conformance.py` re-checks accounts, listeners, hardening and
secret file modes.

`<op>` = the operator account that ran the installers (e.g. `administrator`).

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
| `gent-<id>:gent-<id>` | `kent-spawn-gent` | `/nonexistent` / nologin | container `kent-gent-<id>` | `kent-destroy-gent` |
| DynamicUser | systemd | — | `kent-gent-egress.service`, `kent-gent-gateway.service` | automatic |
| `<op>` | — (existing) | existing | Kent (Hermes `kent` profile), Kent user timers, llama-server | — |

No account is shared between services. The installers refuse to adopt an
existing account or group they did not create.

Group memberships Kent relies on (not created by Kent): `<op>` in `docker` (Kent
inspects and stops Gent containers) and `adm` (reads the journal).

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
| 172.30.0.1:4000 | gateway bridge (socket proxy) | DynamicUser | Gent network only |
| 172.30.0.1:3129 | egress bridge (socket proxy) | DynamicUser | Gent network only |
| 127.0.0.1:8080 | llama-server (not managed by Kent) | `<op>` | host |

UFW (if active): `allow in on kent-gent0 to 172.30.0.1 port 3129,4000 proto tcp`, both recorded and removed on uninstall.

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
| `/opt/kent-core/` | root:root | 0755 | `bin/*.py` Kent tools, schemas |
| `/opt/kent-gent/bin/kent-spawn-gent`, `kent-destroy-gent` | root:root | 0755 | root tools (sudo, §5) |

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
| `/etc/grafana/provisioning/{datasources,dashboards}/kent.yaml` | root:grafana | 0640 | provisioning (vendor dirs, Kent files) |
| `/etc/systemd/system/kent-*.service`, `*.socket` | root:root | 0644 | Kent units |
| `/etc/systemd/system/{alloy,grafana-server}.service.d/kent.conf` | root:root | 0644 | drop-ins (vendor units untouched) |
| `/etc/sudoers.d/91-kent-gent` | root:root | 0440 | §5 |

### 3.3 State

| Path | Owner | Mode | Kept on uninstall? |
|---|---|---|---|
| `/var/lib/litellm/` | litellm | 0750 | only with `--purge-state` removed |
| `/var/lib/prometheus/`, `/var/lib/loki/`, `/var/lib/gitea/` | own account | 0750 | same |
| `/var/lib/alloy/` | alloy | 0750 | same |
| `/var/lib/grafana/` | grafana | 0755 | pre-existing content moved aside to `/var/lib/kent/backup/grafana/`, restored on uninstall |
| `/var/lib/kent-squid/`, `/var/log/kent-squid/` | kent-squid | 0750 | same |
| `/var/lib/kent-gent/` | root | 0755 | same |
| `/var/lib/kent-gent/stacks/<id>/data/` | gent-<id>:`<op group>` | 2750 (setgid) | Gent read/write, Kent read-only |
| `/var/lib/kent-gent/stacks/<id>/inbox/` | `<op>`:gent-<id> | 0750 | Kent writes, Gent reads (mounted read-only) |
| `/var/lib/kent-gent/keys/<id>.key` | root:gent-<id> | 0440 | mounted read-only at `/run/kent/gent_key` |
| `/var/lib/kent-gent/registry/` | root | 0700 | Gents `kent-destroy-gent` may remove |
| `/var/lib/kent-gent/archive/<id>/` | root:`<op group>` | 0750 / files 0640 | retired Gents, read-only |
| `/var/lib/kent/manifest/<module>` | root | 0644 | install records (removed with `--purge-state`) |
| `/var/lib/kent/backup/<module>/…` | root | 0700 | moved-aside pre-existing paths |

### 3.4 Operator (Kent) files

| Path | Mode | Contents |
|---|---|---|
| `~/.config/kent/` | 0700 | `kent.conf`; `litellm_operator_key`, `litellm_kent_key`, `gitea_kent_token`, `gitea_admin_password`, `grafana_admin_password`, `audit_hmac_secret` (all 0600) |
| `~/.local/share/kent/` | 0700 | `kent.db` (0600), `audit/hmac_chain.log`, `digests/` |
| `~/.local/bin/kent*` | 0755 | launchers: `kent`, `kent-audit`, `kent-digest`, `kent-poll-learnings`, `kent-qa-audit`, `kent-gent` |
| `~/.config/systemd/user/kent-*.{service,timer}` | 0644 | 5 timers (§4) |
| `~/.hermes/profiles/kent/` | Hermes-owned | `SOUL.md`, `skills/kent/crew-designer`, `config.yaml` (previous versions moved aside and restored on uninstall) |

---

## 4. Scheduled jobs (operator's systemd --user, linger enabled)

| Timer | Schedule | Does |
|---|---|---|
| `kent-poll-learnings` | every 1 min | escalation relay, learning review, template commits, circuit breaker |
| `kent-audit-ingest` | every 5 min | gateway denials, install events, sudo commands → audit chain |
| `kent-qa-audit` | 02:00 | nightly QA sample on frontier |
| `kent-audit-anchor` | 04:30 | verify + anchor the audit chain in the journal |
| `kent-digest` | 07:00 | daily digest |

---

## 5. Privileged actions

| Who | May run as root | Constraint |
|---|---|---|
| `<op>` (and Kent, as `<op>`) | `/opt/kent-gent/bin/kent-spawn-gent`, `/opt/kent-gent/bin/kent-destroy-gent` (NOPASSWD, `/etc/sudoers.d/91-kent-gent`) | root-owned tools; stack id must be 8 hex; project files read with `O_NOFOLLOW`, regular files only, 256 KiB max; destroy only touches Gents in the root-only registry |
| `<op>` | the module installers/uninstallers | normal sudo with password (a time-limited development grant, `install/dev/agent-sudo.sh`, exists for unattended build/test runs and expires automatically) |

Every sudo command is recorded by sudo in the journal and ingested into Kent's audit chain.

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
