#!/usr/bin/env bash
# =============================================================================
# Kent — install.sh (run from a checkout of the repository)
#
#   sudo ./install.sh [--profile lab|hardened] [--config dev|prod] [--llama-build DIR] [--no-cache]
#                     [--dry-run]
#
# Installs every Kent module in dependency order, each verifying itself, then enrols the
# invoking user as Kent's operator and prints the next steps. Prerequisites: Ubuntu 24.04 or a
# derivative built on it (e.g. Linux Mint 22),
# Docker, an NVIDIA GPU and a llama.cpp build (the llama module installs a copy of its
# llama-server as the on-demand kent-llama service on 127.0.0.1:8080; say where the build is
# with --llama-build DIR/bin the first time). Everything installed is recorded;
# `sudo ./uninstall.sh` reverses it.
#
#   --profile lab       (default) development: Kent keeps a general shell as the kent account
#   --profile hardened  deployment: restricted Kent tools and policies (docs/design, §7)
#   --config dev        (default) all tiers on the local model;  prod: smart/frontier on Claude
#                       (add the key later: sudo ./kent-admin set-anthropic-key)
#   --no-cache          download everything again and leave the download cache untouched
# Re-running without --profile/--config keeps the installed choices; every module is idempotent.
#
# Download cache: /var/cache/kent-install (root-only). Every pinned download (binaries, the
# grafana/alloy .deb packages, Python packages) is kept there and reused by later installs, after
# the same SHA-256 check as a fresh download. A plain uninstall keeps it; `uninstall.sh --purge`
# removes it. To reclaim the space at any time (the next install downloads again):
#   sudo rm -rf /var/cache/kent-install
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PROFILE=""; CONFIG=""; DRY=(); LLAMA=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile) PROFILE="$2"; shift 2 ;;
        --config)  CONFIG="$2"; shift 2 ;;
        --llama-build) LLAMA=(--from "$2"); shift 2 ;;
        --dry-run) DRY=(--dry-run); shift ;;
        --no-cache) export KENT_NO_CACHE=1; shift ;;
        -h|--help) sed -n '2,/^# =====/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done
[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo ./install.sh $*" >&2; exit 1; }
# Re-running keeps the installed profile and gateway config unless told otherwise.
[[ -n "$PROFILE" ]] || PROFILE="$(cat /etc/kent/profile 2>/dev/null || echo lab)"
if [[ -z "$CONFIG" ]]; then
    case "$(head -1 /etc/kent/litellm/config.yaml 2>/dev/null)" in *litellm_config.yaml*) CONFIG=prod ;; *) CONFIG=dev ;; esac
fi
[[ "$PROFILE" == lab || "$PROFILE" == hardened ]] || { echo "--profile must be lab or hardened" >&2; exit 2; }
[[ "$CONFIG" == dev || "$CONFIG" == prod ]] || { echo "--config must be dev or prod" >&2; exit 2; }
OP="${SUDO_USER:-}"; [[ -n "$OP" && "$OP" != root ]] || { echo "run via sudo from the operator's own account" >&2; exit 1; }

MODULES=("llama --profile $PROFILE ${LLAMA[*]}" "litellm --config $CONFIG" prometheus node_exporter loki alloy grafana gitea searxng
         "hermes --profile $PROFILE" kent-core gent)
LOGDIR="/var/lib/kent-install/logs/$(date +%Y%m%dT%H%M%S)"

say()  { printf '%s\n' "$*"; }
fail() { say ""; say "✗ $*"; exit 1; }

# --- Preflight -------------------------------------------------------------------------
say "Kent installer — profile: $PROFILE, gateway config: $CONFIG, operator: $OP"
say "Preflight:"
problems=0
check() { if eval "$2" >/dev/null 2>&1; then say "  ✓ $1"; else say "  ✗ $1 — $3"; problems=$((problems + 1)); fi; }
check "Ubuntu 24.04 (or a noble-based derivative)" \
      '. /etc/os-release && [[ ( "$ID" == ubuntu && "$VERSION_ID" == 24.04 ) || "${UBUNTU_CODENAME:-}" == noble ]]' \
      "other distributions are untested"
check "systemd"                 'pidof systemd || [[ -d /run/systemd/system ]]' "Kent's services are systemd units"
check "Docker running"          'systemctl is-active --quiet docker' "install Docker Engine and start it"
check "python3.12, git, curl, jq, sqlite3, openssl" \
      'command -v python3.12 && command -v git && command -v curl && command -v jq && command -v sqlite3 && command -v openssl' \
      "sudo apt install python3 git curl jq sqlite3 openssl"
check "python3-yaml, python3.12-venv" 'python3 -c "import yaml" && python3.12 -c "import ensurepip, venv"' \
      "sudo apt install python3-yaml python3.12-venv"
check "NVIDIA driver"           'command -v nvidia-smi && nvidia-smi -L' "the local model server needs an NVIDIA GPU and driver"
check "llama.cpp build"         '[[ -x "${LLAMA[1]:-/opt/kent-llama/bin}/llama-server" ]]' \
      "pass --llama-build <llama.cpp>/build/bin (the directory holding llama-server)"
check "port 8080 free (or Kent's)" '! ss -ltnH "( sport = :8080 )" | grep -q . || systemctl is-active --quiet kent-llama' \
      "stop your own llama-server; Kent runs it as the kent-llama service"
check "disk space (≥ 10 GB free in /opt and /var)" \
      '[[ $(df --output=avail -BG /opt | tail -1 | tr -dc 0-9) -ge 10 && $(df --output=avail -BG /var | tail -1 | tr -dc 0-9) -ge 10 ]]' \
      "free some space"
[[ "$problems" -eq 0 ]] || fail "$problems preflight check(s) failed; fix them and run again."

# --- Modules -------------------------------------------------------------------------------
# Progress on a terminal: elapsed time and the module's latest log line, refreshed every second
# (some steps take minutes: model hashing, image builds, downloads). The module itself runs in
# the foreground, so Ctrl-C reaches it as before; the ticker only reads its log.
TICKER=""
start_ticker() {  # start_ticker MODULE LOG
    [[ -t 1 ]] || return 0
    local mod="$1" log="$2" t0=$SECONDS last shown=0 n cols
    cols=$(tput cols 2>/dev/null || echo 80); (( cols >= 60 )) || cols=60
    ( while sleep 1; do
          # Announcements of slow steps (long_step), in full above the progress line, wrapped at word
          # boundaries under the text column (18) so nothing is cut off.
          n="$(grep -c ' NOTE: ' "$log" 2>/dev/null || true)"
          while (( shown < n )); do
              shown=$((shown + 1))
              printf '\r\033[K'
              grep ' NOTE: ' "$log" | sed -n "${shown}p" | sed 's/.* NOTE: //' \
                  | fold -s -w $((cols - 19)) | sed 's/^/                  /'
          done
          # The progress line: the current step (a note shows only its "what"), kept one column short
          # of the terminal so \r always redraws it in place.
          last="$(grep -v '^+' "$log" 2>/dev/null | tail -1 | sed 's/^\[[0-9:]*\] [^:]*: //; s/^NOTE: \(.*\) — .*/\1/; s/^NOTE: //' \
                  | cut -c1-$((cols - 25)) || true)"
          printf '\r\033[K  %-14s %4ds  %s' "$mod" $((SECONDS - t0)) "$last"
      done ) &
    TICKER=$!
}
stop_ticker() {  # stop_ticker MODULE — clear the progress line, leave the cursor after the name
    [[ -n "$TICKER" ]] || return 0
    kill "$TICKER" 2>/dev/null || true; wait "$TICKER" 2>/dev/null || true; TICKER=""
    printf '\r\033[K  %-14s ' "$1"
}
trap '[[ -z "$TICKER" ]] || kill "$TICKER" 2>/dev/null || true' EXIT
[[ ${#DRY[@]} -eq 0 ]] && install -d -m 0700 "$LOGDIR"
say "Installing modules (logs: ${LOGDIR}):"
done_mods=()
for entry in "${MODULES[@]}"; do
    read -r -a argv <<<"$entry"; mod="${argv[0]}"; set -- "${argv[@]:1}"
    printf '  %-14s ' "$mod"
    # A dry run keeps no log directory: capture to a temp file and show it (reading /dev/stdout blocks on the tty).
    log="${LOGDIR}/${mod}.log"; [[ ${#DRY[@]} -gt 0 ]] && log=$(mktemp)
    t0=$SECONDS; start_ticker "$mod" "$log"
    rc=0; SUDO_USER="$OP" "$ROOT/install/services/$mod/install.sh" "$@" "${DRY[@]}" >"$log" 2>&1 || rc=$?
    stop_ticker "$mod"
    [[ ${#DRY[@]} -gt 0 ]] && { say ""; sed 's/^/    /' "$log"; printf '  %-14s ' "$mod"; }
    if [[ $rc -eq 0 ]]; then
        say "✓ $(grep -E 'OK:' "$log" 2>/dev/null | tail -1 | sed 's/.*OK: //' | cut -c1-80) ($((SECONDS - t0)) s)"
        [[ ${#DRY[@]} -gt 0 ]] && rm -f "$log"
        done_mods+=("$mod")
    else
        say "✗"
        tail -15 "$log" | sed 's/^/      /'
        say ""
        say "Module '$mod' failed (full log: $log)."
        if [[ ${#DRY[@]} -gt 0 ]]; then say "Dry run: nothing was changed."
        else say "Already installed: ${done_mods[*]:-(none)}. To remove them: sudo ./uninstall.sh"; fi
        exit 1
    fi
done

[[ ${#DRY[@]} -gt 0 ]] && { say "Dry run complete."; exit 0; }

# --- Next steps --------------------------------------------------------------------------
say ""
say "Kent is installed (profile: $PROFILE)."
runuser -u "$OP" -- /usr/local/bin/kent status 2>/dev/null | sed 's/^/  /' || true
cat <<EOF

Next:
  1. Log out and back in once (or run: newgrp kent-operators) — you were added to kent-operators.
  2. kent                     talk to Kent        (kent --help for everything else)
  3. Grafana  http://127.0.0.1:3001   user admin, password in ~/.config/kent/grafana_admin_password
     Gitea    http://127.0.0.1:3000   user kentadmin, password in ~/.config/kent/gitea_admin_password
  4. Optional: sudo ./kent-admin set-anthropic-key   (then: sudo ./kent-admin config prod)
EOF
