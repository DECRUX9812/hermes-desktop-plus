#!/bin/sh
# Hermes Desktop Plus installer.
#
#   1. finds stock Hermes Agent (or installs it with upstream's own installer),
#   2. installs the hermes-plus sidecar into its own tool environment,
#   3. installs the bridge plugin + migrations into HERMES_HOME, then runs doctor.
#
# POSIX sh, safe to pipe from `curl ... | sh`. Knobs:
#   HERMES_PLUS_REPO            GitHub repo (default: DECRUX9812/hermes-desktop-plus)
#   HERMES_PLUS_VERSION         package version (default: 0.1.1; matches pyproject.toml)
#   HERMES_PLUS_SOURCE          package source for uv/pipx (default: GitHub release wheel URL)
#   HERMES_PLUS_HERMES_BIN      explicit stock hermes binary (checked first)
#   HERMES_PLUS_INSTALL_HERMES  1 = install stock Hermes when it is missing
#   HERMES_PLUS_FORCE           1 = pass --force to `hermes-plus install`
#   HERMES_PLUS_PROFILE         Hermes profile to install into
#   HERMES_PLUS_EXTRAS          comma-separated extra plugins to copy
set -eu

# Stock Hermes Agent the official installer is pinned to. This is an upstream
# release tag (`git tag vX.Y.Z`), the tag its Docker image is published under,
# and the version the compatibility matrix is measured against.
HERMES_KNOWN_GOOD_TAG="v0.22.1"
HERMES_INSTALLER_URL="https://hermes-agent.nousresearch.com/install.sh"

# Hermes Desktop Plus release location on GitHub.
HERMES_PLUS_REPO="${HERMES_PLUS_REPO:-DECRUX9812/hermes-desktop-plus}"
# Package version: must match version in packages/hermes_plus/pyproject.toml and bridge/plugin.yaml.
HERMES_PLUS_VERSION="${HERMES_PLUS_VERSION:-0.1.1}"
# Default source: the wheel from the GitHub release.
HERMES_PLUS_SOURCE="${HERMES_PLUS_SOURCE:-https://github.com/${HERMES_PLUS_REPO}/releases/download/v${HERMES_PLUS_VERSION}/hermes_plus-${HERMES_PLUS_VERSION}-py3-none-any.whl}"
HERMES_PLUS_PROFILE="${HERMES_PLUS_PROFILE:-}"
HERMES_PLUS_EXTRAS="${HERMES_PLUS_EXTRAS:-}"
HERMES_PLUS_INSTALL_HERMES="${HERMES_PLUS_INSTALL_HERMES:-0}"
HERMES_PLUS_FORCE="${HERMES_PLUS_FORCE:-0}"

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

note() {
    printf '%s\n' "$1"
}

# ---------- stock Hermes Agent ----------

# Prints the resolved binary. Exit 2 = HERMES_PLUS_HERMES_BIN was set but unusable.
find_hermes() {
    if [ -n "${HERMES_PLUS_HERMES_BIN:-}" ]; then
        if [ -x "$HERMES_PLUS_HERMES_BIN" ]; then
            printf '%s\n' "$HERMES_PLUS_HERMES_BIN"
            return 0
        fi
        return 2
    fi
    if command -v hermes >/dev/null 2>&1; then
        command -v hermes
        return 0
    fi
    for candidate in "$HOME/.local/bin/hermes" "${HERMES_HOME:-$HOME/.hermes}/.local/bin/hermes"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

install_hermes() {
    command -v curl >/dev/null 2>&1 || fail "curl is required to run the official Hermes installer"
    command -v bash >/dev/null 2>&1 || fail "bash is required to run the official Hermes installer"
    installer_dir="$(mktemp -d "${TMPDIR:-/tmp}/hermes-plus-install.XXXXXX")"
    trap 'rm -rf "$installer_dir"' EXIT
    installer="$installer_dir/install.sh"
    note "Stock Hermes not found; installing Hermes Agent ${HERMES_KNOWN_GOOD_TAG} from ${HERMES_INSTALLER_URL}"
    # Download first: a failed download must not look like a successful pipe.
    curl -fsSL "$HERMES_INSTALLER_URL" -o "$installer" || fail "could not download ${HERMES_INSTALLER_URL}"
    # Upstream's bootstrap is a bash script. --branch selects the release tag;
    # --non-interactive skips the stages that need a terminal (setup, gateway).
    bash "$installer" --branch "$HERMES_KNOWN_GOOD_TAG" --non-interactive \
        || fail "the official Hermes installer failed"
    rm -rf "$installer_dir"
}

HERMES_BIN=""
hermes_status=0
HERMES_BIN="$(find_hermes)" || hermes_status=$?
if [ "$hermes_status" = "2" ]; then
    fail "HERMES_PLUS_HERMES_BIN=${HERMES_PLUS_HERMES_BIN} is not an executable file"
fi
if [ "$hermes_status" != "0" ]; then
    if [ "$HERMES_PLUS_INSTALL_HERMES" != "1" ]; then
        printf "Error: no 'hermes' executable found.\n" >&2
        note "Hermes Desktop Plus needs stock Hermes Agent (${HERMES_KNOWN_GOOD_TAG} is the tested tag)."
        note "Install it with:  curl -fsSL ${HERMES_INSTALLER_URL} | bash"
        note "Or re-run this installer with HERMES_PLUS_INSTALL_HERMES=1 to do that here."
        exit 1
    fi
    install_hermes
    hermes_status=0
    HERMES_BIN="$(find_hermes)" || hermes_status=$?
    if [ "$hermes_status" != "0" ]; then
        fail "stock Hermes installed but no 'hermes' executable was found; add its launcher directory to PATH"
    fi
fi
note "Using stock Hermes at ${HERMES_BIN}"

# ---------- hermes-plus sidecar ----------

TOOL_BIN_DIR=""
if command -v uv >/dev/null 2>&1; then
    note "Installing hermes-plus into its own uv tool environment..."
    uv tool install --force "$HERMES_PLUS_SOURCE" || fail "uv tool install ${HERMES_PLUS_SOURCE} failed"
elif command -v pipx >/dev/null 2>&1; then
    python_ok=0
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            python_ok=1
            break
        fi
    done
    [ "$python_ok" = "1" ] || fail "pipx needs a python3 >= 3.11 on PATH; install uv instead (https://astral.sh/uv)"
    note "Installing hermes-plus with pipx..."
    pipx install --force "$HERMES_PLUS_SOURCE" || fail "pipx install ${HERMES_PLUS_SOURCE} failed"
    TOOL_BIN_DIR="${PIPX_BIN_DIR:-$HOME/.local/bin}"
else
    printf 'Error: neither uv nor pipx was found.\n' >&2
    note "Install uv (recommended):  curl -LsSf https://astral.sh/uv/install.sh | sh"
    note "Or install pipx, then re-run this installer."
    exit 1
fi

# The tool just installed may not be on this shell's PATH yet.
HP_BIN=""
for candidate in \
    "${UV_TOOL_BIN_DIR:-}/hermes-plus" \
    "${TOOL_BIN_DIR}/hermes-plus" \
    "$HOME/.local/bin/hermes-plus"
do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        HP_BIN="$candidate"
        break
    fi
done
if [ -z "$HP_BIN" ] && command -v hermes-plus >/dev/null 2>&1; then
    HP_BIN="$(command -v hermes-plus)"
fi
[ -n "$HP_BIN" ] || fail "hermes-plus was installed but its executable was not found; add the uv or pipx bin directory to PATH"

# ---------- install into HERMES_HOME ----------

set -- install --hermes "$HERMES_BIN"
if [ "$HERMES_PLUS_FORCE" = "1" ]; then
    set -- "$@" --force
fi
if [ -n "$HERMES_PLUS_PROFILE" ]; then
    set -- "$@" --profile "$HERMES_PLUS_PROFILE"
fi
if [ -n "$HERMES_PLUS_EXTRAS" ]; then
    set -- "$@" --extras "$HERMES_PLUS_EXTRAS"
fi

install_status=0
"$HP_BIN" "$@" || install_status=$?
if [ "$install_status" != "0" ]; then
    printf "Error: '%s install' failed with exit code %s.\n" "$HP_BIN" "$install_status" >&2
    note "Hermes Desktop Plus is NOT installed. Fix the problem above and re-run this installer."
    exit "$install_status"
fi

echo ""
echo "============================================================"
echo "  Hermes Desktop Plus is installed."
echo "============================================================"
echo "  HERMES_HOME: ${HERMES_HOME:-$HOME/.hermes}"
echo ""
echo "  Start the desktop server:  hermes-plus serve"
echo "  Then open:                 http://127.0.0.1:9129"
echo ""
echo "  Doctor below checks this machine; every [FAIL] line prints its fix."
echo ""

# The install arguments above are done with; reuse the positional slots so a
# binary path containing spaces stays one argument.
set -- doctor --hermes "$HERMES_BIN"
if [ -n "$HERMES_PLUS_PROFILE" ]; then
    set -- "$@" --profile "$HERMES_PLUS_PROFILE"
fi

doctor_status=0
"$HP_BIN" "$@" || doctor_status=$?
if [ "$doctor_status" != "0" ]; then
    echo ""
    note "Note: hermes-plus doctor reported problems (exit code ${doctor_status})."
    note "      The installation itself succeeded; see the fix hints above."
fi
