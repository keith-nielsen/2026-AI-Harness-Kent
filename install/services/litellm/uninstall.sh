#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/litellm/uninstall.sh
# Removes only what install.sh recorded in /var/lib/kent/manifest/litellm.
# State (/var/lib/litellm) is kept unless --purge-state is given.
#
# Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
# =============================================================================
set -euo pipefail
SERVICE="litellm"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
uninstall_main "$@"
