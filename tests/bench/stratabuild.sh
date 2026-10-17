#!/usr/bin/env bash
# stratabuild.sh [VISION] — build the Strata container image for the Codacus replication (operator, 2026-10-07:
# Strata in Docker for isolation). No GPU, no model server, no host changes; Docker only.
#   Source: bench/strata at the reviewed commit (STRATA_COMMIT below; the build refuses any other checkout).
#   Base image pinned by digest (Strata's Dockerfile names a tag: the FROM line is swapped, nothing else changes).
#   CUDA_ARCHITECTURES=75 (RTX 2060 SUPER only). VISION: 0 (default, no image encoder) or 1.
#   Then a thin layer on top: an unprivileged user (uid 64010) owning /opt/strata, which the entrypoint and setup.py
#   write to (the config link, the engine log), so the container never runs as root.
#   Images: strata:82f46a8-base (Strata's own build), strata:82f46a8 (with the user). IDs logged.
# Start detached:  tests/bench/submit.sh stratabuild stratabuild.sh
set -uo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
BENCH=$HOME/Documents/repo/bench
SRC=$BENCH/strata
RUN=$BENCH/runs/stratabuild-$(date +%m%d%H%M)
mkdir -p "$RUN"
exec >>"$RUN/stratabuild.log" 2>&1
log() { echo "$(date '+%F %T') stratabuild: $*"; }
STRATA_COMMIT=82f46a8
BASE_TAG='nvidia/cuda:13.0.0-devel-ubuntu24.04'
BASE_PIN="$BASE_TAG@sha256:1e8ac7a54c184a1af8ef2167f28fa98281892a835c981ebcddb1fad04bdd452d"
VISION=${1:-0}
UID_STRATA=64010
[[ $VISION == 0 || $VISION == 1 ]] || { log "VISION must be 0 or 1"; exit 2; }
head=$(git -C "$SRC" rev-parse --short=7 HEAD 2>/dev/null)
[[ $head == "$STRATA_COMMIT" ]] || { log "refused: $SRC is at '$head', not the reviewed $STRATA_COMMIT"; exit 1; }
[[ -z $(git -C "$SRC" status --porcelain) ]] || { log "refused: $SRC has local changes"; exit 1; }
grep -qx "FROM $BASE_TAG" "$SRC/Dockerfile" || { log "refused: Strata's FROM line is not '$BASE_TAG'"; exit 1; }
sed "s|^FROM $BASE_TAG\$|FROM $BASE_PIN|" "$SRC/Dockerfile" > "$RUN/Dockerfile.pinned"
log "start: Strata $STRATA_COMMIT, base $BASE_PIN, CUDA arch 75, vision $VISION"
docker build -f "$RUN/Dockerfile.pinned" -t "strata:$STRATA_COMMIT-base" \
  --build-arg CUDA_ARCHITECTURES=75 --build-arg BUILD_VISION="$VISION" "$SRC" > "$RUN/build-base.log" 2>&1 \
  || { log "base build FAILED: $(tail -5 "$RUN/build-base.log" | tr '\n' ' ')"; exit 1; }
log "base image: $(docker image inspect -f '{{.Id}} {{.Size}}' "strata:$STRATA_COMMIT-base")"
cat > "$RUN/Dockerfile.user" <<EOF
FROM strata:$STRATA_COMMIT-base
RUN useradd --uid $UID_STRATA --no-create-home --home-dir /opt/strata --shell /usr/sbin/nologin strata \\
    && chown -R strata:strata /opt/strata
USER $UID_STRATA:$UID_STRATA
EOF
docker build -f "$RUN/Dockerfile.user" -t "strata:$STRATA_COMMIT" "$RUN" > "$RUN/build-user.log" 2>&1 \
  || { log "user layer FAILED: $(tail -5 "$RUN/build-user.log" | tr '\n' ' ')"; exit 1; }
log "image: $(docker image inspect -f '{{.Id}} {{.Size}} user={{.Config.User}}' "strata:$STRATA_COMMIT")"
log "engine: $(docker run --rm --network none --entrypoint cat "strata:$STRATA_COMMIT" /opt/strata/engine/BUILD.json | tr -d '\n ' | cut -c1-200)"
log "done: $RUN"
