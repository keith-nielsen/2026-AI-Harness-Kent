#!/usr/bin/env bash
# Kent — kent-core uninstall (manifest-driven; restores prior Hermes SOUL.md/config.yaml). Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="kent-core"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
uninstall_main "$@"
