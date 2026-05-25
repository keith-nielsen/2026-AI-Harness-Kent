#!/usr/bin/env bash
# =============================================================================
# Kent — Phase 7: Gent Template
# Creates: Docker image, compose template, spawn/destroy scripts, Gitea template repo
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib.sh"

TEMPLATES_DIR="${KENT_ROOT}/templates"
SCRIPTS_DIR="${KENT_ROOT}/scripts"
TEMPLATE_REPO="${KENT_HOME}/stack-template"

# ─── 7.1 Build Gent Docker Image ─────────────────────────────────────────────
log "Building Gent Docker image..."
docker build -t kent/gent:latest -f "${TEMPLATES_DIR}/Dockerfile.fractal" "${TEMPLATES_DIR}"
test_gate "Gent image built" "docker image inspect kent/gent:latest >/dev/null 2>&1"

# ─── 7.2 Deploy Operational Scripts ───────────────────────────────────────────
log "Installing operational scripts..."
mkdir -p /usr/local/bin

for script in spawn-gent.sh destroy-gent.sh submit-task.sh; do
    cp "${SCRIPTS_DIR}/${script}" "/usr/local/bin/${script%.sh}"
    chmod +x "/usr/local/bin/${script%.sh}"
    log "  Installed: /usr/local/bin/${script%.sh}"
done

# Deploy cron scripts
for script in nightly-qa-audit.sh daily-digest.sh validate-hmac-chain.sh \
              poll-learnings.sh cleanup-expired-grants.sh; do
    if [[ -f "${SCRIPTS_DIR}/${script}" ]]; then
        cp "${SCRIPTS_DIR}/${script}" "$KENT_HOME/cron/${script}"
        chown kent:kent "$KENT_HOME/cron/${script}"
        chmod +x "$KENT_HOME/cron/${script}"
    fi
done

# ─── 7.3 Gitea Template Repo ─────────────────────────────────────────────────
log "Creating Gitea template repo..."
# This will be pushed to Gitea once Kent's PAT is configured.
# For now, initialise the local template structure with full CrewAI scaffold.

if [[ ! -d "$TEMPLATE_REPO" ]]; then
    mkdir -p "$TEMPLATE_REPO"
    cd "$TEMPLATE_REPO"
    git init
    git config user.email "kent@localhost"
    git config user.name "Kent Agent"

    # Compose and schema
    cp "${TEMPLATES_DIR}/docker-compose.gent.yml" .
    cp "${KENT_ROOT}/schemas/stack.sql" .

    # CrewAI application scaffold
    cp -r "${TEMPLATES_DIR}/app" .
    cp "${TEMPLATES_DIR}/requirements.txt" .
    cp "${TEMPLATES_DIR}/Dockerfile.fractal" ./Dockerfile

    cat > README.md << 'EOF'
# Gent Stack Template

This repository is the template from which all new Gent stacks are spawned.
Kent auto-commits improvements here based on adopted shared learnings.

## Structure

```
app/
├── gent/
│   ├── __init__.py
│   ├── main.py          ← CEO entry point (kanban loop, crew dispatch)
│   ├── crew.py           ← Dynamic crew factory (tier-aware LLM binding)
│   ├── router.py         ← Pre-crew T0 routing via gateway
│   └── tools.py          ← Custom proxy-aware tools (no bundled CrewAI tools)
└── config/
    ├── agents.yaml       ← Worker role definitions (customise per project)
    └── tasks.yaml        ← Default task templates
```

## LLM Binding

agents.yaml defines ROLES, not LLM bindings. The crew factory (`crew.py`)
binds each agent to the gateway tier decided by the Router at runtime.
A researcher might get `smart` for a complex task but `fast` for a simple
extraction — same role, different brain, decided per-task.

## Customisation

At spawn time, Kent can replace `config/agents.yaml` with project-specific
roles. The CEO can also reconfigure its own crew dynamically as the project
evolves by updating `config/agents.yaml` and rebuilding the crew on the
next task cycle.

Do not edit other files manually unless you know what you're doing —
Kent manages template evolution via shared learnings.
EOF

    git add -A
    git commit -m "Initial template with CrewAI scaffold (Kent v2.3)"
    chown -R kent:kent "$TEMPLATE_REPO"
    log "  Template repo initialised at $TEMPLATE_REPO"
else
    log "  Template repo already exists — checking for CrewAI scaffold..."
    if [[ ! -d "$TEMPLATE_REPO/app/gent" ]]; then
        cd "$TEMPLATE_REPO"
        git config user.email "kent@localhost"
        git config user.name "Kent Agent"
        cp -r "${TEMPLATES_DIR}/app" .
        cp "${TEMPLATES_DIR}/requirements.txt" .
        cp "${TEMPLATES_DIR}/Dockerfile.fractal" ./Dockerfile
        git add -A
        git commit -m "Add CrewAI scaffold (Kent v2.3 update)" || true
        chown -R kent:kent "$TEMPLATE_REPO"
        log "  CrewAI scaffold added to existing template repo."
    else
        log "  CrewAI scaffold already present."
    fi
fi

# ─── 7.4 Stacks Directory ────────────────────────────────────────────────────
mkdir -p "$STACKS_DIR"
chown "${ENTERPRISE_USER}:${ENTERPRISE_USER}" "$STACKS_DIR"
chmod 0755 "$STACKS_DIR"

# ─── 7.5 Start Kent ──────────────────────────────────────────────────────────
log "Starting Kent agent..."
# Kent is started in Phase 6 via hermes gateway

# ─── Tests ────────────────────────────────────────────────────────────────────
test_gate "Gent Docker image" "docker image inspect kent/gent:latest >/dev/null 2>&1"
test_gate "spawn-gent installed" "command -v spawn-gent"
test_gate "destroy-gent installed" "command -v destroy-gent"
test_gate "Template repo exists" "test -d '$TEMPLATE_REPO/.git'"
test_gate "CrewAI scaffold in template" "test -f '$TEMPLATE_REPO/app/gent/crew.py'"
test_gate "Agent roles config" "test -f '$TEMPLATE_REPO/app/config/agents.yaml'"

log "Phase 7 complete."
