#!/usr/bin/env bash
# Kent — node_exporter uninstall (manifest-driven). Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="node_exporter"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
uninstall_main "$@"
