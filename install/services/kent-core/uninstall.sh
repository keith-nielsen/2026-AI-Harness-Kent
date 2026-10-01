#!/usr/bin/env bash
# Kent — kent-core uninstall (manifest-driven): tools, `kent` command, sudoers entry, timers,
# operator's kent-operators membership; Kent's data kept unless --purge-state.
# Usage: sudo ./uninstall.sh [--purge-state] [--dry-run]
set -euo pipefail
SERVICE="kent-core"
source "$(cd "$(dirname "$0")" && pwd)/../lib-service.sh"
uninstall_main "$@"
