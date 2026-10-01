#!/usr/bin/env bash
# Kent — loki uninstall (manifest-driven). Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="loki"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
uninstall_main "$@"
