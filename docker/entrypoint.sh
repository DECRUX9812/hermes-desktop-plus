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

hermes_home="${HERMES_HOME:-/opt/data}"
echo "[hermes-plus] installing into HERMES_HOME=${hermes_home}"
hermes-plus install

echo "[hermes-plus] serving on 0.0.0.0:9129"
exec hermes-plus serve --host 0.0.0.0
