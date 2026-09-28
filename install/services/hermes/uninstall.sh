#!/usr/bin/env bash
# Kent — hermes uninstall (manifest-driven). Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
# Removes Kent's Hermes, its managed policy and (last) the kent account and kent-operators group.
# Uninstall kent-core and gent first. The operator's own ~/.hermes is never touched.
set -euo pipefail
SERVICE="hermes"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
uninstall_main "$@"
