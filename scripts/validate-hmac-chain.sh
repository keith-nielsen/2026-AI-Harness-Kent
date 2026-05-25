#!/usr/bin/env bash
# =============================================================================
# Kent — HMAC Chain Validator
# Validates integrity of the append-only audit chain.
# Cron: 04:30 UTC daily. Any break = immediate alert.
# =============================================================================
set -euo pipefail

# Load runtime config (written by install phase 0)
source /etc/kent/kent.conf
HMAC_LOG="${LOGS_DIR}/hmac_chain.log"
HMAC_SECRET="${SECRETS_DIR}/hmac_chain_secret.txt"

if [[ ! -f "$HMAC_LOG" ]]; then
    echo "[hmac] No chain file found. Nothing to validate."
    exit 0
fi

if [[ ! -f "$HMAC_SECRET" ]]; then
    echo "[hmac] WARNING: No HMAC secret file. Cannot validate chain."
    exit 1
fi

SECRET=$(cat "$HMAC_SECRET")
PREV_HMAC="GENESIS"
LINE_NUM=0
ERRORS=0

while IFS= read -r line; do
    LINE_NUM=$((LINE_NUM + 1))

    # Expected format: <timestamp>|<entity>|<action>|<outcome>|<hmac>
    STORED_HMAC=$(echo "$line" | rev | cut -d'|' -f1 | rev)
    PAYLOAD=$(echo "$line" | rev | cut -d'|' -f2- | rev)

    EXPECTED=$(echo -n "${PREV_HMAC}|${PAYLOAD}" | openssl dgst -sha256 -hmac "$SECRET" -hex 2>/dev/null | awk '{print $NF}')

    if [[ "$STORED_HMAC" != "$EXPECTED" ]]; then
        echo "[hmac] CHAIN BREAK at line ${LINE_NUM}"
        echo "  Expected: ${EXPECTED}"
        echo "  Got:      ${STORED_HMAC}"
        ERRORS=$((ERRORS + 1))
    fi

    PREV_HMAC="$STORED_HMAC"
done < "$HMAC_LOG"

if [[ $ERRORS -gt 0 ]]; then
    echo "[hmac] INTEGRITY FAILURE: ${ERRORS} chain breaks in ${LINE_NUM} entries."
    # TODO: Create Gitea issue + alert Human
    exit 1
else
    echo "[hmac] Chain valid: ${LINE_NUM} entries verified."
fi
