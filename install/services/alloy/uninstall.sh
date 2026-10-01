#!/usr/bin/env bash
# Kent — alloy uninstall (manifest-driven). Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="alloy"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
uninstall_main "$@"
