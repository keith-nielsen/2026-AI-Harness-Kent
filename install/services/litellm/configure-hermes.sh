#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/litellm/configure-hermes.sh
# Run as the operator (no sudo). Points the Hermes "kent" profile at the local
# LiteLLM gateway's classify-then-route entry point ("auto"), so all Kent
# traffic is classified by the router tier before it reaches a model.
#
# Usage: ./configure-hermes.sh [--profile kent] [--context-length 262144]
# =============================================================================
set -euo pipefail

PROFILE="kent"
CONTEXT_LENGTH=262144   # smallest window across current tiers (local Qwen3.6)
KEY_FILE="${HOME}/.config/kent/litellm_kent_key"   # identity "kent"
GATEWAY="http://127.0.0.1:4000/v1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)        PROFILE="$2"; shift 2 ;;
        --context-length) CONTEXT_LENGTH="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ $EUID -ne 0 ]] || { echo "run as your own user, not root" >&2; exit 1; }
command -v hermes >/dev/null || { echo "hermes not found on PATH (Hermes is a prerequisite)" >&2; exit 1; }
[[ -s "$KEY_FILE" ]] || { echo "missing $KEY_FILE; run sudo install.sh first" >&2; exit 1; }
curl -sf -m 5 "${GATEWAY%/v1}/health/liveliness" >/dev/null \
    || { echo "gateway not answering at ${GATEWAY}; check: systemctl status kent-litellm" >&2; exit 1; }

hermes profile show "$PROFILE" >/dev/null 2>&1 \
    || hermes profile create "$PROFILE" --no-skills --description "Kent (via LiteLLM auto router)"

CFG="$(hermes -p "$PROFILE" config path)"
cp -p "$CFG" "${CFG}.bak-$(date +%Y%m%d-%H%M%S)"

H=(hermes -p "$PROFILE" config)
"${H[@]}" set model.provider custom
"${H[@]}" set model.base_url "$GATEWAY"
"${H[@]}" set model.default auto
"${H[@]}" set model.api_key "$(cat "$KEY_FILE")"
"${H[@]}" set model.context_length "$CONTEXT_LENGTH" --force
"${H[@]}" set auth.adopt_external_logins false

echo "Profile '$PROFILE' now sends all traffic to ${GATEWAY} (model: auto)."
echo "Previous config backed up next to $CFG"
