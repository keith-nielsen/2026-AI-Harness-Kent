# Kent Gent image: CrewAI CEO + workers. Built by install/services/gent/install.sh
# from a context holding this file, requirements.lock and the gent/ package.
# Base pinned by digest (python:3.12-slim); dependencies hash-locked.
FROM python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OTEL_SDK_DISABLED=true \
    CREWAI_TRACING_ENABLED=false \
    CREWAI_DISABLE_TELEMETRY=true \
    HOME=/tmp \
    CREWAI_STORAGE_DIR=/data/crewai

COPY requirements.lock /tmp/requirements.lock
RUN pip install --require-hashes --no-deps -r /tmp/requirements.lock && rm /tmp/requirements.lock

COPY gent /app/gent
RUN python -m compileall -q /app/gent && python -c "import sys; sys.path.insert(0,'/app'); import gent.main"
WORKDIR /app
# The container always runs as the spawning Gent's own uid (docker run --user).
USER 65534:65534
ENTRYPOINT ["python", "-m", "gent.main"]
