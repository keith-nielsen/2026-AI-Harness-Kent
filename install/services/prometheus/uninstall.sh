#!/usr/bin/env bash
# Kent — prometheus uninstall (manifest-driven). Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="prometheus"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
uninstall_main "$@"
