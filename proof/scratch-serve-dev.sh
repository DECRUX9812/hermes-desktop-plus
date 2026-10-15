#!/usr/bin/env bash
# Scratch: run the sidecar straight from the worktree venv (editable install)
# against the temp home, so source fixes are live without a reinstall.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export HERMES_HOME=${HP_HOME:?set HP_HOME}
export HERMES_PLUS_HERMES_BIN="$HOME/Code/ship-test/venv/bin/hermes"
nohup .venv/bin/hermes-plus serve --host 127.0.0.1 --port "${HP_PORT:-9230}" \
  --hermes-bin "$HERMES_PLUS_HERMES_BIN" \
  --ui-dir "$PWD/packages/hermes_plus/src/hermes_plus/ui_dist" \
  >"$HERMES_HOME/serve.log" 2>&1 &
echo "serve pid $!"
for _ in $(seq 1 240); do
  if grep -q 'Hermes Plus login:' "$HERMES_HOME/serve.log"; then break; fi
  sleep 1
done
grep -o "http://127.0.0.1:${HP_PORT:-9230}/?token=[A-Za-z0-9_-]*" "$HERMES_HOME/serve.log" | head -1 >"$HERMES_HOME/login-url.txt"
echo "login url written: $(wc -c <"$HERMES_HOME/login-url.txt") bytes"
