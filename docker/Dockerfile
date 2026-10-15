# Hermes Desktop Plus image: our sidecar on top of the official Hermes Agent image.
#
# Build from the repository root:
#   docker build -f docker/Dockerfile --build-arg HERMES_TAG=v0.22.1 -t hermes-desktop-plus .

# Stock Hermes Agent release the runtime is based on. This is upstream's own
# release tag; its Docker image is published under the same name and the
# version our compatibility matrix is measured against. Bump it here (and in
# install.sh, HERMES_KNOWN_GOOD_TAG) when a newer tag is verified.
ARG HERMES_TAG=v0.22.1

# ---------- Stage 1: build the web UI (npm workspace root) ----------
FROM node:22-slim AS ui

WORKDIR /app
# `npm ci` needs the workspace manifests: the root package.json + lock, and
# both workspace packages. packages/ui's vite config aliases @hermes/shared to
# ../shared/src, so this tree is self-contained.
COPY package.json package-lock.json ./
COPY packages/ui ./packages/ui
COPY packages/shared ./packages/shared
RUN npm ci --no-audit --no-fund
RUN npm --prefix packages/ui run build:webapp

# ---------- Stage 2: runtime + sidecar ----------
FROM nousresearch/hermes-agent:${HERMES_TAG}

# The official image is root with s6-overlay as PID 1: its entrypoint runs the
# volume bootstrap and then execs the container command as the unprivileged
# `hermes` user (UID 10000) with HERMES_HOME=/opt/data. Build steps need root;
# the entrypoint drops the privilege, so we never declare USER here.
USER root

# The sidecar lives in its own venv. Hermes's sealed /opt/hermes/.venv is never
# written to, so an image upgrade or `hermes update` cannot break us.
#
# /usr/bin/python3 is the image's Debian interpreter (>= 3.11). The pm-managed
# interpreter on PATH is deliberately not used: our venv must not depend on
# Hermes's package manager.
COPY packages/hermes_plus /app/packages/hermes_plus
RUN set -eu; \
    /usr/bin/python3 -c 'import sys; sys.exit("base image python3 is older than 3.11") if sys.version_info < (3, 11) else None'; \
    /usr/bin/python3 -m venv /opt/hermes-plus/venv; \
    test -x /opt/hermes-plus/venv/bin/pip || { echo "the base image's python3-venv provides no pip" >&2; exit 1; }; \
    /opt/hermes-plus/venv/bin/python -m pip install --no-cache-dir --upgrade pip; \
    /opt/hermes-plus/venv/bin/python -m pip install --no-cache-dir /app/packages/hermes_plus

# Built UI -> hermes_plus/ui_dist, the directory the server serves from. Copied
# into the installed package because ui_dist/ is build output, not source.
COPY --from=ui /app/packages/ui/dist-webapp /tmp/ui-dist
RUN set -eu; \
    pkg_dir="$(/opt/hermes-plus/venv/bin/python -c 'import pathlib, hermes_plus; print(pathlib.Path(hermes_plus.__file__).parent)')"; \
    mkdir -p "$pkg_dir/ui_dist"; \
    cp -a /tmp/ui-dist/. "$pkg_dir/ui_dist/"; \
    rm -rf /tmp/ui-dist /app; \
    test -f "$pkg_dir/ui_dist/index.html"

COPY docker/entrypoint.sh /opt/hermes-plus/entrypoint.sh
RUN chmod 0755 /opt/hermes-plus/entrypoint.sh

# Ahead of the image's own PATH entries, so the container command resolves
# `hermes-plus` without an absolute path; `hermes` still resolves to the shim
# the official image ships at /opt/hermes/bin/hermes.
ENV PATH="/opt/hermes-plus/venv/bin:${PATH}"

EXPOSE 9129

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://127.0.0.1:9129/healthz || exit 1

# The image's ENTRYPOINT (/opt/hermes/docker/entrypoint-dispatch.sh) is kept: it
# owns PID 1, prepares /opt/data and drops to the hermes user, then runs this
# CMD. HERMES_UID/HERMES_GID remap that user at boot.
CMD ["/opt/hermes-plus/entrypoint.sh"]
