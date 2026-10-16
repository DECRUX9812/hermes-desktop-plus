#!/bin/sh
# Container command: install the bridge plugin + migrations into HERMES_HOME,
# then serve the sidecar. The image's entrypoint (kept from the base image) has
# already prepared /opt/data and dropped to the hermes user.
set -eu

# The container binds 0.0.0.0, so unauthenticated serving is never allowed.
AUTH="${HERMES_PLUS_AUTH:-password}"
if [ "$AUTH" = "none" ]; then
    echo "Error: HERMES_PLUS_AUTH cannot be 'none' in this container: it binds 0.0.0.0." >&2
    echo "Set HERMES_PLUS_AUTH=password with HERMES_PLUS_PASSWORD, or HERMES_PLUS_AUTH=token." >&2
    exit 1
fi

# An empty password would install the bridge and then exit 1, which under
# `restart: unless-stopped` is a silent restart loop; the placeholder from
# docker/.env.example would serve the published port to anyone. Refuse both
# before anything is written to the volume.
if [ "$AUTH" = "password" ]; then
    password="${HERMES_PLUS_PASSWORD:-}"
    password_file="${HERMES_PLUS_PASSWORD_FILE:-}"
    if [ -z "$password" ] && [ -n "$password_file" ] && [ -s "$password_file" ]; then
        password="$(cat "$password_file")"
    fi
    if [ -z "$password" ]; then
        echo "Error: HERMES_PLUS_AUTH=password needs a password, but HERMES_PLUS_PASSWORD is empty." >&2
        echo "Create docker/.env (copy docker/.env.example) and set HERMES_PLUS_PASSWORD in it," >&2
        echo "or pass it for this run with: -e HERMES_PLUS_PASSWORD=..." >&2
        echo "(a mounted secret works too: -e HERMES_PLUS_PASSWORD_FILE=/run/secrets/hermes-plus-password)" >&2
        exit 1
    fi
    if [ "$password" = "change-me-to-a-secure-password" ]; then
        echo "Error: HERMES_PLUS_PASSWORD is still the placeholder shipped in docker/.env.example." >&2
        echo "Set a real password in docker/.env before starting the container." >&2
        exit 1
    fi
fi

hermes_home="${HERMES_HOME:-/opt/data}"
echo "[hermes-plus] installing into HERMES_HOME=${hermes_home}"
hermes-plus install

echo "[hermes-plus] serving on 0.0.0.0:9129"
exec hermes-plus serve --host 0.0.0.0
