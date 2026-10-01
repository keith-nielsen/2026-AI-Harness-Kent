#!/usr/bin/env bash
# Kent — grafana uninstall (manifest-driven; restores prior data dir and unit state). Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="grafana"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
uninstall_main "$@"
