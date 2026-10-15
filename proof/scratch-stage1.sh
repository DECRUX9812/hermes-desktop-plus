#!/usr/bin/env bash
# Scratch: stage 1 of the S10 proof (install path) for interactive iteration.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT=$(pwd)
export HERMES_HOME=${HP_HOME:?set HP_HOME}
export HERMES_PLUS_HERMES_BIN="$HOME/Code/ship-test/venv/bin/hermes"
export UV_TOOL_DIR="$HERMES_HOME/.tool"
export UV_TOOL_BIN_DIR="$HERMES_HOME/.toolbin"
export PATH="$UV_TOOL_BIN_DIR:$PATH"

rm -rf "$ROOT/packages/hermes_plus/src/hermes_plus/ui_dist"
mkdir -p "$ROOT/packages/hermes_plus/src/hermes_plus/ui_dist"
cp -a "$ROOT/packages/ui/dist-webapp/." "$ROOT/packages/hermes_plus/src/hermes_plus/ui_dist/"
echo "ui_dist files: $(find "$ROOT/packages/hermes_plus/src/hermes_plus/ui_dist" -type f | wc -l)"

echo "== uv tool install"
uv tool install --force "$ROOT/packages/hermes_plus" || exit 1
echo "== hermes-plus install"
"$UV_TOOL_BIN_DIR/hermes-plus" install || exit 1
echo "== doctor"
"$UV_TOOL_BIN_DIR/hermes-plus" doctor || true
echo "== update --check"
"$UV_TOOL_BIN_DIR/hermes-plus" update --check
echo "== version"
"$UV_TOOL_BIN_DIR/hermes-plus" version
