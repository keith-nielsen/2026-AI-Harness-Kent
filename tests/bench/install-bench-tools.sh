#!/usr/bin/env bash
# install-bench-tools.sh [--gateway dev|sim-routed] [--uninstall] — run as root (sudo). Installs the bench's privileged part:
#   /opt/kent-bench/src            root-owned copy of this repo (no .git, venvs or results): the only
#                                  code kent-bench runs as root, so editing the repo changes nothing
#                                  until this installer is run again
#   /opt/kent-bench/bin/kent-bench root-owned copy of tests/bench/benchmode.sh
#   /etc/sudoers.d/93-kent-bench   %kent-operators may run exactly `kent-bench on|off|reset|status`
#                                  as root without a password; nothing else (`golden` needs real sudo)
#   /opt/kent-bench/gateway        the gateway flavour `kent-bench on` installs (--gateway; default dev:
#                                  every tier local; sim-routed: production routing, frontier -> the
#                                  oracle on 127.0.0.1:4010, which someone must be answering)
# --uninstall removes all three (bench mode must be off; /var/lib/kent-bench, which holds the golden
# Kent state, is kept unless --purge is also given).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OPT=/opt/kent-bench; SUDOERS=/etc/sudoers.d/93-kent-bench; BIN=$OPT/bin/kent-bench
die() { echo "install-bench-tools: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run as root (sudo)"
GATEWAY=dev
if [[ "${1:-}" == --gateway ]]; then
  GATEWAY=${2:-}; shift 2 || die "--gateway needs dev or sim-routed"
  [[ $GATEWAY == dev || $GATEWAY == sim-routed ]] || die "--gateway must be dev or sim-routed"
  [[ -f /var/lib/kent-bench/on ]] && die "bench mode is on; run kent-bench off before changing its gateway"
fi

if [[ "${1:-}" == --uninstall ]]; then
  [[ -f /var/lib/kent-bench/on || -f /var/lib/kent-bench/orig.sha256 ]] && die "bench mode is on; run kent-bench off first"
  rm -f "$SUDOERS"; rm -rf "$OPT"
  [[ "${2:-}" == --purge ]] && rm -rf /var/lib/kent-bench
  logger -t kent-bench -- "human:${SUDO_USER:-root} bench tools uninstalled"
  echo "install-bench-tools: removed $OPT and $SUDOERS"; exit 0
fi

install -d -m 0755 -o root -g root "$OPT" "$OPT/bin"
rm -rf "$OPT/src.new"; install -d -m 0755 -o root -g root "$OPT/src.new"
tar -C "$REPO" --exclude=.git --exclude=.venv --exclude='__pycache__' --exclude=.pytest_cache \
    --exclude='tests/bench/runs' -cf - . | tar -C "$OPT/src.new" --no-same-owner -xf -
chown -R root:root "$OPT/src.new"; chmod -R go-w "$OPT/src.new"
rm -rf "$OPT/src"; mv "$OPT/src.new" "$OPT/src"
install -m 0755 -o root -g root "$OPT/src/tests/bench/benchmode.sh" "$BIN"
printf '%s\n' "$GATEWAY" > "$OPT/gateway.new"; chmod 0644 "$OPT/gateway.new"; mv "$OPT/gateway.new" "$OPT/gateway"

tmp=$(mktemp)
cat > "$tmp" <<EOF
# Kent bench (tests/bench/install-bench-tools.sh): operators switch bench mode and reset Kent's
# state between test items. Only these exact commands; the code is root-owned under $OPT.
%kent-operators ALL=(root) NOPASSWD: $BIN on, $BIN off, $BIN reset, $BIN status
EOF
chmod 0440 "$tmp"
visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; die "visudo rejected the rule"; }
mv "$tmp" "$SUDOERS"; chown root:root "$SUDOERS"
logger -t kent-bench -- "human:${SUDO_USER:-root} bench tools installed from $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "install-bench-tools: installed $BIN (source $(cd "$OPT/src" && find . -type f | wc -l) files, bench gateway $GATEWAY) and $SUDOERS"
