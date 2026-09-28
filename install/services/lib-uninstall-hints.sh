#!/usr/bin/env bash
# =============================================================================
# Kent — install/services/lib-uninstall-hints.sh
# Plain-language "likely cause" for a failed module uninstall, from the error text it printed.
# Sourced by uninstall.sh; no root needed (unit-tested in tests/install/test_uninstall_hints.py).
#
#   uninstall_hint FILE    print one advice line for the module output in FILE
# =============================================================================

uninstall_hint() {  # uninstall_hint <file with the module's output>
    local f="$1" who
    if who="$(grep -oE "user [A-Za-z0-9_.-]+ is currently used by process [0-9]+" "$f" | head -1)" && [[ -n "$who" ]]; then
        local user pid; user="$(awk '{ print $2 }' <<<"$who")"; pid="$(awk '{ print $NF }' <<<"$who")"
        echo "a process still runs as '$user' (pid $pid): see it with 'ps -u $user', stop it (or its service), then resume"
    elif grep -qE "Could not get lock|dpkg frontend lock|Unable to acquire the dpkg" "$f"; then
        echo "apt/dpkg is busy (probably automatic updates): wait until it finishes, then resume"
    elif grep -qE "Cannot connect to the Docker daemon|docker.sock.*(connect|refused|no such file)" "$f"; then
        echo "Docker is not running: start it (sudo systemctl start docker), then resume"
    elif grep -qE "Permission denied|must run as root|Operation not permitted" "$f"; then
        echo "missing privileges: run it with sudo (as root), then resume"
    else
        echo "no known cause recognised: see the module's output above and the full log"
    fi
}
