# syntax=docker/dockerfile:1
# GUAI server image: builds the Zola frontend, installs the Python package
# (dependencies pinned from uv.lock), runs as non-root, data in /data.
# See spec/operations.md.

ARG ZOLA_VERSION=0.23.6

# --- Frontend stage: build the Zola site ------------------------------------
FROM debian:bookworm-slim AS frontend
ARG ZOLA_VERSION
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && ARCH=$(uname -m) && case "$ARCH" in \
         x86_64)  ZARCH="x86_64-unknown-linux-gnu" ;; \
         aarch64) ZARCH="aarch64-unknown-linux-gnu" ;; \
         *) echo "unsupported arch $ARCH" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/getzola/zola/releases/download/v${ZOLA_VERSION}/zola-v${ZOLA_VERSION}-${ZARCH}.tar.gz" \
       | tar xz -C /usr/local/bin \
    && apt-get purge -y curl && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY frontend/ ./frontend/
RUN zola --root frontend build

# --- Runtime stage ---------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
LABEL org.opencontainers.image.source="https://github.com/${GITHUB_REPOSITORY}"

ENV GUAI_STATIC_DIR=/app/static \
    GUAI_DB_PATH=/data/guai.sqlite3 \
    GUAI_MIGRATIONS_DIR=/app/migrations \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Locked, deterministic install: export pinned requirements from uv.lock.
COPY server/ ./server/
RUN cd /app/server \
    && uv export --frozen --no-dev -o /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt \
    && uv pip install --system --no-deps /app/server \
    && cd /app && rm -rf /tmp/requirements.txt server

COPY --from=frontend /build/frontend/public /app/static
COPY server/migrations /app/migrations

# Non-root runtime user (uid 10001); /data is the only writable path.
RUN useradd --uid 10001 --create-home guai \
    && mkdir -p /data \
    && chown -R guai:guai /data /app

USER guai
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import sys, urllib.request; ok=urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200; sys.exit(0 if ok else 1)"

# The console script runs uvicorn (host 0.0.0.0, port 8000, one worker); migrations
# run automatically in the app lifespan before serving (idempotent at head).
ENTRYPOINT ["guai-hub"]