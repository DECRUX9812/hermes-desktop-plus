#!/usr/bin/env bash
# Scratch: stage 2 — configure the provider and start the sidecar for interactive iteration.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export HERMES_HOME=${HP_HOME:?set HP_HOME}
export HERMES_PLUS_HERMES_BIN="$HOME/Code/ship-test/venv/bin/hermes"
TOOL="$HERMES_HOME/.toolbin/hermes-plus"

cat >"$HERMES_HOME/config.yaml" <<'YAML'
providers:
  cliproxy:
    api: http://127.0.0.1:8317/v1
    name: Local proxy
    key_env: CLIPROXY_API_KEY
    transport: chat_completions
    context_length: 1048576
    default_model: gemini-3.7-flash-high
model:
  provider: cliproxy
  default: gemini-3.7-flash-high
YAML
grep '^CLIPROXY_API_KEY=' "$HOME/.hermes/.env" >"$HERMES_HOME/.env"
chmod 600 "$HERMES_HOME/.env"

nohup "$TOOL" serve --host 127.0.0.1 --port "${HP_PORT:-9230}" --hermes-bin "$HERMES_PLUS_HERMES_BIN" \
  >"$HERMES_HOME/serve.log" 2>&1 &
echo "serve pid $!"
for _ in $(seq 1 240); do
  if grep -q 'Hermes Plus login:' "$HERMES_HOME/serve.log"; then break; fi
  sleep 1
done
grep -o "http://127.0.0.1:${HP_PORT:-9230}/?token=[A-Za-z0-9_-]*" "$HERMES_HOME/serve.log" | head -1 >"$HERMES_HOME/login-url.txt"
echo "login url: $(cat "$HERMES_HOME/login-url.txt")"
tail -5 "$HERMES_HOME/serve.log"
