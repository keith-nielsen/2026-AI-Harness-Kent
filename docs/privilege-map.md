# Kent — Privilege Map

Security reference for the Kent agentic computing estate. Documents every
filesystem path, its ownership, permissions, and the processes that access
it. Also covers network boundaries, group memberships, and privilege
escalation paths.

This document is the authoritative source for access control decisions.
Any new path, service, or permission change must be reflected here before
implementation.

## Design Principles

1. **Least privilege by default.** Every process runs as a dedicated
   unprivileged user. Root access is granted only through explicit,
   auditable sudoers entries for specific commands.

2. **No ambient authority.** Docker group membership is root-equivalent
   and is never granted to system users. Container lifecycle is managed
   through sudoers-controlled scripts only.

3. **Secrets are opaque to consumers.** Each service reads only the
   secrets it needs. Kent reads kent_key.txt but never gateway.env.
   Gents read their own gent_key.txt but never Kent's key or the
   admin key.

4. **Install as root, chown to runtime user.** Every file created
   during install is immediately chowned to whoever needs it at
   runtime. Files are never left as root-owned if a non-root process
   needs to read or write them.

5. **Containers are read-only.** Gent containers run with read_only: true,
   all capabilities dropped, no-new-privileges set. Writable paths are
   explicit tmpfs mounts or bind-mounted data volumes.

## System Users

| User | UID | Type | Shell | Home | Purpose |
|------|-----|------|-------|------|---------|
| kent | system | system | nologin | /home/kent | Estate manager agent |
| litellm | system | system | nologin | /home/litellm | Inference gateway |
| ollama | system | system | nologin | /usr/share/ollama | Model inference engine |
| gitea | system | system | nologin | /home/gitea | Git server |
| prometheus | system | system | nologin | /var/lib/prometheus | Metrics collection |
| loki | system | system | nologin | /var/lib/loki | Log aggregation |
| promtail | system | system | nologin | (none) | Log shipping agent |
| gent-{id} | system | system | nologin | /home/kent/stacks/{id} | Per-Gent container user |

## Groups and Memberships

| Group | Members | Purpose |
|-------|---------|---------|
| ollama | kent, litellm | Read access to Ollama Unix socket |
| docker | {operator} only | Container management (root-equivalent, never granted to system users) |
| agentic-logs | {operator}, promtail | Cross-service log access |

**Kent is explicitly NOT in the docker group.** Docker socket access is
root-equivalent. Kent manages containers exclusively through sudoers-allowed
spawn-gent and destroy-gent scripts.

## Privilege Escalation

| User | Command | Via | Purpose |
|------|---------|-----|---------|
| kent | /usr/local/bin/spawn-gent | sudoers NOPASSWD | Create Gent stacks (Unix users, Docker containers, API keys) |
| kent | /usr/local/bin/destroy-gent | sudoers NOPASSWD | Archive and remove Gent stacks |

Sudoers file: /etc/sudoers.d/kent-gent (mode 0440, root:root)

No other privilege escalation paths exist or should be added without
updating this document and reviewing the security implications.

## Filesystem — Kent Home (/home/kent)

| Path | Owner | Mode | Created by | Read by | Write by | Notes |
|------|-------|------|------------|---------|----------|-------|
| /home/kent/ | kent:kent | 0755 | phase 0 | kent, operator | kent | Must be 755 for operator CLI access to venv |
| /home/kent/venv/ | kent:kent | 0755 (dirs), o+r (files) | phase 6 | kent, operator | kent | Python venv, operator needs read+exec for kent CLI |
| /home/kent/kent.db | kent:kent | 0600 | phase 6 | kent | kent | Estate registry, stack metadata |
| /home/kent/stacks/ | kent:kent | 0755 | phase 0, phase 7 | kent, operator | spawn-gent (root) | Parent directory for all Gent stacks |
| /home/kent/stacks/{id}/ | root:root | 0755 | spawn-gent | kent, gent-{id} | spawn-gent (root) | Individual stack root |
| /home/kent/stacks/{id}/data/ | kent:kent | 0755 | spawn-gent | kent, gent-{id} | gent-{id} | Bind-mounted into container as /data |
| /home/kent/stacks/{id}/data/stack.db | gent-{id}:gent-{id} | 0600 | spawn-gent | gent-{id}, litestream | gent-{id} | Per-Gent database |
| /home/kent/stacks/{id}/secrets/ | root:root | 0700 | spawn-gent | root only | spawn-gent (root) | Docker secrets source directory |
| /home/kent/stacks/{id}/secrets/gent_key.txt | root:root | 0600 | spawn-gent | Docker secrets mechanism | spawn-gent (root) | Per-Gent LiteLLM API key |
| /home/kent/stacks/{id}/secrets/gitea_pat.txt | root:root | 0600 | spawn-gent | Docker secrets mechanism | spawn-gent (root) | Per-Gent Gitea access token |
| /home/kent/stacks/{id}/docker-compose.yml | root:root | 0644 | spawn-gent | kent, Docker | spawn-gent (root) | Generated from template at spawn time |
| /home/kent/stacks/{id}/litestream.yml | root:root | 0644 | spawn-gent | litestream container | spawn-gent (root) | Replication config |
| /home/kent/stack-template/ | kent:kent | 0755 | phase 7 | kent | kent | Gitea-managed Gent template repo |
| /home/kent/archives/ | kent:kent | 0755 | destroy-gent | kent, operator | destroy-gent (root) | Compressed archives of destroyed stacks |
| /home/kent/replicas/ | kent:kent | 0755 | litestream | kent | litestream | Litestream DB replicas |
| /home/kent/cron/ | kent:kent | 0755 | phase 6 | kent, cron | kent | Cron job scripts |
| /home/kent/logs/ | kent:kent | 0755 | phase 0 | kent, operator | kent | Parent log directory |
| /home/kent/logs/kent/ | kent:agentic-logs | 0750 | phase 0 | kent, promtail | kent | Kent agent logs |
| /home/kent/logs/gateway/ | litellm:agentic-logs | 0750 | phase 0 | litellm, promtail | litellm | LiteLLM gateway logs |

## Filesystem — Kent Secrets (/home/kent/secrets)

This directory contains the most sensitive material in the stack. Access
is strictly compartmentalised.

| Path | Owner | Mode | Read by | Contents |
|------|-------|------|---------|----------|
| /home/kent/secrets/ | root:root | 0700 | root only | Secrets directory (not traversable by kent) |
| /home/kent/secrets/gateway.env | root:root | 0600 | root, litellm (via systemd EnvironmentFile) | LITELLM_ADMIN_KEY, DATABASE_URL, DEEPSEEK_API_KEY, ANTHROPIC_API_KEY |
| /home/kent/secrets/kent_key.txt | kent:kent | 0400 | kent | Kent's scoped LiteLLM API key (router, fast, smart, frontier) |
| /home/kent/secrets/audit_key.txt | root:root | 0600 | root | Audit-scoped key (frontier only) |
| /home/kent/secrets/kent_gitea_pat.txt | root:root | 0600 | root | Kent's Gitea personal access token |
| /home/kent/secrets/litellm_database_url.txt | root:root | 0600 | root | PostgreSQL connection string for LiteLLM |

**Design note**: kent_key.txt is the only secret kent can read directly.
All other secrets are consumed by root-owned processes (systemd services,
spawn scripts). This means a compromised kent process cannot extract the
admin key, database credentials, or cloud API keys.

**Apparent contradiction**: The secrets directory is root:root 0700, yet
kent reads kent_key.txt. This works because kent_key.txt is kent:kent 0400
and the file was placed there by root during install. Kent cannot list the
directory but can read a file it owns if it knows the exact path. However,
this depends on filesystem behaviour that varies across configurations.
A more robust approach would be to place kent_key.txt in a kent-readable
location outside the secrets directory (e.g. /home/kent/.kent_key).
This is a known technical debt item.

## Filesystem — Hermes (/home/kent/.hermes)

| Path | Owner | Mode | Read by | Write by | Notes |
|------|-------|------|---------|----------|-------|
| /home/kent/.hermes/ | kent:kent | 0755 | kent | kent | Hermes agent home |
| /home/kent/.hermes/config.yaml | kent:kent | 0644 | kent | kent | Hermes configuration |
| /home/kent/.hermes/SOUL.md | kent:kent | 0644 | kent | install (root, chowned) | Kent personality and capabilities |
| /home/kent/.hermes/.env | kent:kent | 0600 | kent | install (root, chowned) | Hermes environment (API key for LiteLLM) |
| /home/kent/.hermes/skills/ | kent:kent | 0755 | kent | install (root, chowned) | Hermes skill directory (auto-discovered) |
| /home/kent/.hermes/skills/kent/crew-designer/ | kent:kent | 0755 | kent | install (root, chowned) | Crew designer skill and templates |

## Filesystem — LiteLLM (/home/litellm, /etc/litellm)

| Path | Owner | Mode | Read by | Write by | Notes |
|------|-------|------|---------|----------|-------|
| /home/litellm/ | litellm:litellm | 0755 | litellm | litellm | LiteLLM home and venv |
| /etc/litellm/ | root:litellm | 0755 | root, litellm | install (root) | Configuration directory |
| /etc/litellm/config.yaml | root:litellm | 0640 | root, litellm | install (root) | Active model routing config |

## Filesystem — Ollama

| Path | Owner | Mode | Read by | Write by | Notes |
|------|-------|------|---------|----------|-------|
| /usr/share/ollama/ | ollama:ollama | 0755 | ollama | ollama | Ollama home, model storage |
| /run/ollama/ollama.sock | ollama:ollama | 0660 | ollama group (kent, litellm) | ollama | Unix domain socket for inference |

## Filesystem — System Configuration

| Path | Owner | Mode | Read by | Write by | Notes |
|------|-------|------|---------|----------|-------|
| /etc/kent/kent.conf | root:root | 0644 | all (runtime config) | install (root) | Paths, ports, exported env vars |
| /etc/sudoers.d/kent-gent | root:root | 0440 | sudo | install (root) | Kent spawn/destroy privileges |
| /etc/cron.d/kent | root:root | 0644 | cron | install (root) | Scheduled jobs (digest, telemetry, QA) |
| /etc/squid/squid.conf | root:root | 0644 | squid | install (root) | Egress proxy whitelist |

## Filesystem — Observability

| Path | Owner | Mode | Read by | Write by | Notes |
|------|-------|------|---------|----------|-------|
| /var/lib/prometheus/ | prometheus:prometheus | 0755 | prometheus | prometheus | Metrics TSDB |
| /var/lib/loki/ | loki:loki | 0755 | loki | loki | Log storage |
| /var/log/squid/ | proxy:agentic-logs | 0750 | squid, promtail | squid | Egress proxy logs |

## Container Security — Gent Stacks

Each Gent runs in a Docker container with the following security posture:

| Control | Setting | Notes |
|---------|---------|-------|
| Filesystem | read_only: true | Entire rootfs is immutable |
| Capabilities | cap_drop: ALL | No Linux capabilities |
| Privilege escalation | no-new-privileges | Cannot gain capabilities via setuid/setgid |
| Writable paths | /tmp (100M tmpfs) | General temp storage |
| | /app/.local (50M tmpfs, uid=999) | Python/ChromaDB data directory |
| | /app/.cache (50M tmpfs, uid=999) | Python package cache |
| | /app/.config (10M tmpfs, uid=999) | Application config |
| | /data (bind mount) | Stack database and working files |
| Secrets | /run/secrets/gent_key (Docker secrets) | LiteLLM API key (read-only) |
| | /run/secrets/gitea_pat (Docker secrets) | Gitea access token (read-only) |
| Network | kent-gent-net (172.30.0.0/24) | Isolated bridge network |
| Egress | HTTP_PROXY/HTTPS_PROXY → Squid (3128) | All outbound traffic proxied and whitelisted |
| Container user | gent (uid=999, gid=999) | Non-root, no shell |

**UID isolation**: The gent user (UID 999) exists only inside each
container's user namespace. Every Gent container has its own independent
UID 999 — there are no collisions between containers or with the host.
The host-side per-Gent users (gent-{id}) have unique UIDs assigned by
the host's useradd and own the bind-mounted /data directories. The tmpfs
uid=999 settings are container-internal only.

**User namespace remapping**: If Docker userns-remap is ever enabled,
the internal UID 999 gets remapped to a different host UID. The
bind-mount ownership on /data would need to match the remapped UID.
userns-remap is not currently configured.

**Container isolation**: Gent containers cannot see each other's data
volumes, cannot access the host filesystem outside their bind mount,
and cannot make network requests that bypass the egress proxy.

## Network Topology

All services bind to localhost only. No service is directly reachable
from the network. The only external exposure is through the operator's
SSH session.

| Service | Bind address | Port | Protocol | Accessed by |
|---------|-------------|------|----------|-------------|
| Ollama | 127.0.0.1 (+ Unix socket) | 11434 | HTTP | LiteLLM |
| LiteLLM Gateway | 127.0.0.1 | 4000 | HTTP | Kent, operator, Gents (via Docker bridge) |
| Hermes Gateway | 127.0.0.1 | 8642 | HTTP | Operator (via kent CLI) |
| PostgreSQL | 127.0.0.1 | 5432 | PostgreSQL | LiteLLM |
| Gitea | 127.0.0.1 | 3000 | HTTP | Kent, Gents (via Docker bridge) |
| Grafana | 127.0.0.1 | 3001 | HTTP | Operator |
| Prometheus | 127.0.0.1 | 9090 | HTTP | Grafana |
| Loki | 127.0.0.1 | 3100 | HTTP | Promtail, Grafana |
| Squid (egress proxy) | 127.0.0.1 | 3128 | HTTP | Gent containers (via Docker bridge) |
| Node Exporter | 127.0.0.1 | 9100 | HTTP | Prometheus |
| Promtail | 127.0.0.1 | 9080 | HTTP | (push to Loki) |

### Docker Bridge Network

Gent containers reach host services via the Docker bridge gateway
at 172.30.0.1. This is the only route from container to host.

| From container | To host service | Via |
|----------------|----------------|-----|
| Gent CEO | LiteLLM Gateway | http://172.30.0.1:4000 |
| Gent CEO | Gitea | http://172.30.0.1:3000 |
| Gent CEO | Internet (whitelisted) | http://172.30.0.1:3128 (Squid) |

## Access Pattern Summary

This matrix shows which user can access which resource category.

| Resource | root | kent | litellm | gent-{id} | operator |
|----------|------|------|---------|-----------|----------|
| Admin API key | ✓ | ✗ | ✓ (env) | ✗ | ✗ |
| Kent API key | ✓ | ✓ | ✗ | ✗ | ✗ |
| Gent API key | ✓ | ✗ | ✗ | ✓ (secret) | ✗ |
| Cloud API keys | ✓ | ✗ | ✓ (env) | ✗ | ✗ |
| Kent database | ✓ | ✓ | ✗ | ✗ | ✗ |
| Gent database | ✓ | ✗ | ✗ | ✓ | ✗ |
| Docker socket | ✓ | ✗ | ✗ | ✗ | ✓ |
| Ollama socket | ✓ | ✓ | ✓ | ✗ | ✗ |
| LiteLLM config | ✓ | ✗ | ✓ | ✗ | ✗ |
| Hermes config | ✓ | ✓ | ✗ | ✗ | ✗ |
| Skills | ✓ | ✓ | ✗ | ✗ | ✗ |
| Spawn containers | ✓ | ✓ (sudo) | ✗ | ✗ | ✓ |
| Destroy containers | ✓ | ✓ (sudo) | ✗ | ✗ | ✓ |
| Observability data | ✓ | ✗ | ✗ | ✗ | ✓ (Grafana) |

## Blast Radius Analysis

If a process is compromised, what can the attacker reach?

| Compromised process | Direct access | Escalation path | Blast radius |
|---------------------|---------------|-----------------|--------------|
| Kent (hermes) | kent.db, skills, kent_key.txt, Ollama socket | sudo spawn/destroy-gent | Can create/destroy Gent stacks, consume inference tokens on all tiers. Cannot read admin key, cloud keys, other Gents' data, or Docker socket. |
| LiteLLM | Model routing config, admin key, cloud API keys, PostgreSQL | None | Can proxy inference to any model, read/revoke API keys. Cannot access Kent's data, Gent data, or Docker. |
| Gent container | Own stack.db, own API key (fast/smart only), egress via proxy | None (all caps dropped, read-only fs) | Can consume inference tokens on fast/smart tiers. Cannot reach other Gents, Kent's data, or the host filesystem. |
| Operator session | Docker socket (root-equivalent), all Grafana data | sudo to root | Full system access. This is by design: the operator is the principal. |

## Known Technical Debt

1. **kent_key.txt in root-owned directory**: Kent owns the file but it
   lives inside /home/kent/secrets/ which is root:root 0700. This works
   on ext4 (file ownership checked, not directory traversal for known
   paths) but is fragile. Consider moving to /home/kent/.kent_key.

2. **Container UID pinned to 999**: The Dockerfile explicitly assigns
   UID/GID 999 to the gent user (`useradd -r -u 999`). The
   docker-compose template references this in tmpfs mounts. If either
   value changes, both files must be updated together.

3. **Squid whitelist is static**: The egress proxy whitelist is set at
   install time. If a Gent needs access to a new domain, the operator
   must manually update squid.conf and reload. Consider a mechanism
   for per-Gent egress rules.

## Validation

Phase 8 (E2E) should verify every entry in this map. Proposed test gates:

```bash
# Ownership checks
test_gate "kent.db owned by kent" "stat -c '%U:%G' '$KENT_DB' | grep -q 'kent:kent'"
test_gate "secrets dir root-only" "stat -c '%a' '$SECRETS_DIR' | grep -q '700'"
test_gate "gateway.env root-only" "stat -c '%U:%a' '$SECRETS_DIR/gateway.env' | grep -q 'root:600'"
test_gate "kent_key readable by kent" "sudo -u kent test -r '$SECRETS_DIR/kent_key.txt'"
test_gate "kent cannot read gateway.env" "! sudo -u kent test -r '$SECRETS_DIR/gateway.env'"
test_gate "kent not in docker group" "! id -nG kent | grep -qw docker"
test_gate "sudoers exists" "test -f /etc/sudoers.d/kent-gent"
test_gate "stacks owned by kent" "stat -c '%U:%G' '$STACKS_DIR' | grep -q 'kent:kent'"
```
