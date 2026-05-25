# =============================================================================
# Kent — Environment File Templates
#
# These are EXAMPLES. Copy to the real path and fill in actual values.
# NEVER commit real secrets to version control.
# =============================================================================

# --- /home/kent/secrets/gateway.env ---
# Scope: Global (host). Read by LiteLLM gateway via systemd EnvironmentFile.
# Permissions: 0600 root:root
# Contains: API keys for cloud frontier providers and gateway admin key.

LITELLM_ADMIN_KEY=sk-admin-CHANGE_ME_GENERATE_WITH_openssl_rand_hex_32
DEEPSEEK_API_KEY=sk-CHANGE_ME_YOUR_DEEPSEEK_KEY
ANTHROPIC_API_KEY=sk-ant-CHANGE_ME_YOUR_ANTHROPIC_KEY

# --- /home/kent/secrets/kent_key.txt ---
# Scope: Kent agent only. Mounted via systemd LoadCredential.
# Permissions: 0600 root:root
# Contains: Single line — the LiteLLM API key scoped to router+fast+smart+frontier.
# Generated during Phase 3 install via: curl POST localhost:4000/key/generate

# --- /home/kent/secrets/kent_gitea_pat.txt ---
# Scope: Kent agent only. Mounted via systemd LoadCredential.
# Permissions: 0600 root:root
# Contains: Single line — Gitea Personal Access Token for Kent's service account.
# Generated during Phase 6 install via Gitea API.

# --- /home/kent/secrets/audit_key.txt ---
# Scope: Nightly QA cron only.
# Permissions: 0600 root:root
# Contains: Single line — LiteLLM API key scoped to frontier only.

# --- /home/kent/stacks/<uuid8>/secrets/gent_key.txt ---
# Scope: Per-Gent. Mounted via Docker secrets into container at /run/secrets/gent_key.
# Permissions: 0600 root:root
# Contains: Single line — LiteLLM API key scoped to fast+smart only.
# Generated at spawn time by spawn-gent.sh via: curl POST localhost:4000/key/generate

# --- /home/kent/stacks/<uuid8>/secrets/gitea_pat.txt ---
# Scope: Per-Gent. Mounted via Docker secrets into container at /run/secrets/gitea_pat.
# Permissions: 0600 root:root
# Contains: Single line — Gitea PAT scoped to this Gent's project repo only.
