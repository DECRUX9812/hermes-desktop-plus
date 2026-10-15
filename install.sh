#!/bin/sh
set -eu

# Pinned known-good tag for stock Hermes Agent installer
HERMES_KNOWN_GOOD_TAG="v0.22.1"

# Environment knobs
HERMES_PLUS_SOURCE="${HERMES_PLUS_SOURCE:-hermes-plus}"
HERMES_PLUS_PROFILE="${HERMES_PLUS_PROFILE:-}"
HERMES_PLUS_INSTALL_HERMES="${HERMES_PLUS_INSTALL_HERMES:-0}"
HERMES_PLUS_EXTRAS="${HERMES_PLUS_EXTRAS:-}"

# Step 1: Check OS, python >=3.11, and hermes
OS_NAME="$(uname -s 2>/dev/null || echo "Unknown")"
case "$OS_NAME" in
    Linux*|Darwin*)
        ;;
    *)
        echo "Warning: Operating system '$OS_NAME' may not be fully supported." >&2
        ;;
esac

# Check Python >= 3.11
PYTHON_BIN=""
for candidate in python3 python python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
            PYTHON_BIN="$(command -v "$candidate")"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "Error: Python >= 3.11 is required but not found." >&2
    echo "Please install Python 3.11 or newer and ensure it is in your PATH." >&2
    exit 1
fi

# Check hermes
if ! command -v hermes >/dev/null 2>&1; then
    if [ "$HERMES_PLUS_INSTALL_HERMES" = "1" ]; then
        echo "Hermes binary not found. Installing stock Hermes (${HERMES_KNOWN_GOOD_TAG})..."
        curl -fsSL "https://raw.githubusercontent.com/NousResearch/hermes-agent/${HERMES_KNOWN_GOOD_TAG}/setup-hermes.sh" | bash
        export PATH="${HOME}/.local/bin:${PATH}"
        if ! command -v hermes >/dev/null 2>&1; then
            echo "Error: Stock Hermes installation finished, but 'hermes' is still not found in PATH." >&2
            exit 1
        fi
    else
        echo "Error: 'hermes' command not found in PATH." >&2
        echo "Hermes Desktop Plus requires stock Hermes Agent to be installed." >&2
        echo "To install stock Hermes, see: https://hermes-agent.nousresearch.com/docs" >&2
        echo "Or run this installer with HERMES_PLUS_INSTALL_HERMES=1 to install it automatically." >&2
        exit 1
    fi
fi

# Step 2: Install hermes-plus via uv tool or pipx
HP_BIN=""
if command -v uv >/dev/null 2>&1; then
    echo "Installing hermes-plus via 'uv tool'..."
    uv tool install --force "$HERMES_PLUS_SOURCE"
    if [ -n "${UV_TOOL_BIN_DIR:-}" ] && [ -x "${UV_TOOL_BIN_DIR}/hermes-plus" ]; then
        HP_BIN="${UV_TOOL_BIN_DIR}/hermes-plus"
    fi
elif command -v pipx >/dev/null 2>&1; then
    echo "Installing hermes-plus via 'pipx'..."
    pipx install --force "$HERMES_PLUS_SOURCE"
else
    echo "Error: Neither 'uv' nor 'pipx' was found." >&2
    echo "Please install uv (recommended: curl -LsSf https://astral.sh/uv/install.sh | sh) or pipx." >&2
    exit 1
fi

if [ -z "$HP_BIN" ]; then
    if [ -n "${UV_TOOL_BIN_DIR:-}" ] && [ -x "${UV_TOOL_BIN_DIR}/hermes-plus" ]; then
        HP_BIN="${UV_TOOL_BIN_DIR}/hermes-plus"
    elif command -v hermes-plus >/dev/null 2>&1; then
        HP_BIN="$(command -v hermes-plus)"
    elif [ -x "${HOME}/.local/bin/hermes-plus" ]; then
        HP_BIN="${HOME}/.local/bin/hermes-plus"
    else
        HP_BIN="hermes-plus"
    fi
fi

# Step 3: Run hermes-plus install
INSTALL_CMD="$HP_BIN install"
if [ -n "$HERMES_PLUS_PROFILE" ]; then
    INSTALL_CMD="$INSTALL_CMD --profile $HERMES_PLUS_PROFILE"
fi
if [ -n "$HERMES_PLUS_EXTRAS" ]; then
    INSTALL_CMD="$INSTALL_CMD --extras $HERMES_PLUS_EXTRAS"
fi

echo "Running: $INSTALL_CMD"
set +e
$INSTALL_CMD
INSTALL_STATUS=$?
set -e

if [ "$INSTALL_STATUS" -ne 0 ]; then
    if [ "$INSTALL_STATUS" -eq 2 ]; then
        echo "Note: hermes-plus install reported missing components (code 2)."
    else
        echo "Error: hermes-plus install failed with exit code $INSTALL_STATUS" >&2
        exit "$INSTALL_STATUS"
    fi
fi

# Step 4: Print the serve command and URL hint
echo ""
echo "============================================================"
echo "  Hermes Desktop Plus installed successfully!"
echo "============================================================"
echo "To start the desktop server, run:"
echo "  hermes-plus serve"
echo ""
echo "Then open your browser at:"
echo "  http://127.0.0.1:9129"
echo "============================================================"
