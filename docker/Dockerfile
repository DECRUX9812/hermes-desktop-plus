# Multi-stage Dockerfile for Hermes Desktop Plus
# Stage 1: Build the Web UI bundle from packages/ui
FROM node:22-slim AS ui

WORKDIR /app
COPY packages/ui ./packages/ui
WORKDIR /app/packages/ui
RUN npm ci && npm run build:webapp

# Stage 2: Runtime image with stock Hermes and hermes-plus sidecar
FROM python:3.11-slim AS runtime

ARG HERMES_REF=v0.22.1

# Install runtime dependencies: git for repository interaction, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install stock Hermes Agent pinned to known-good tag
RUN pip install --no-cache-dir "hermes-agent @ git+https://github.com/NousResearch/hermes-agent@${HERMES_REF}"

# Setup unprivileged hermes user and persistent data directory
RUN useradd -m -u 1000 -s /bin/bash hermes && \
    mkdir -p /data/hermes && \
    chown -R hermes:hermes /data/hermes

WORKDIR /app

# Copy hermes-plus Python package
COPY --chown=hermes:hermes packages/hermes_plus ./packages/hermes_plus

# Copy built web UI assets into package ui_dist
COPY --from=ui --chown=hermes:hermes /app/packages/ui/dist ./packages/hermes_plus/src/hermes_plus/ui_dist

# Install hermes-plus into Python environment
RUN pip install --no-cache-dir ./packages/hermes_plus

# Copy entrypoint script
COPY --chown=hermes:hermes docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

USER hermes

ENV HERMES_HOME=/data/hermes \
    PYTHONUNBUFFERED=1

VOLUME ["/data/hermes"]

EXPOSE 9129

# Healthcheck targeting /healthz endpoint provided by hermes-plus server (S5)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:9129/healthz || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
