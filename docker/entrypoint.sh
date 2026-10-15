#!/bin/sh
set -eu

# Authentication comes from environment (HERMES_PLUS_AUTH)
# Refuse auth='none' when running in container since it binds to 0.0.0.0
AUTH="${HERMES_PLUS_AUTH:-password}"
if [ "$AUTH" = "none" ]; then
    echo "Error: HERMES_PLUS_AUTH cannot be 'none' when binding to 0.0.0.0 in Docker." >&2
    echo "Please set HERMES_PLUS_AUTH to 'password' (with HERMES_PLUS_PASSWORD) or 'token'." >&2
    exit 1
fi

echo "Running hermes-plus install..."
hermes-plus install

echo "Starting hermes-plus serve on 0.0.0.0:9129..."
exec hermes-plus serve --host 0.0.0.0
