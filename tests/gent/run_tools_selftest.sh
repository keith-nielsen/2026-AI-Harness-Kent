#!/bin/sh
# Run the Gent tools self-test inside the installed Gent image on kent-gent-net,
# unprivileged and locked down like a real Gent (needs the gent module installed).
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
exec docker run --rm --network kent-gent-net --user 65534:65534 --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m --tmpfs /data:rw,nosuid,nodev,size=64m,uid=65534 \
    --tmpfs /run/kent:rw,size=1m,uid=65534 \
    --cap-drop ALL --security-opt no-new-privileges \
    -e SEARXNG_URL=http://172.30.0.1:8888 -e HTTP_PROXY=http://172.30.0.1:3129 -e HTTPS_PROXY=http://172.30.0.1:3129 \
    -e http_proxy=http://172.30.0.1:3129 -e https_proxy=http://172.30.0.1:3129 -e NO_PROXY=172.30.0.1 \
    -v "$here/tools_selftest.py:/selftest.py:ro" --entrypoint sh kent-gent:current \
    -c 'echo sk-fake-key > /run/kent/gent_key && python /selftest.py'
