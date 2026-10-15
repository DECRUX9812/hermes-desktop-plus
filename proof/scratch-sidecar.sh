#!/usr/bin/env bash
# Scratch helper: start/stop the sidecar straight from the worktree venv.
#   HP_HOME=/tmp/hp-dev-1 proof/scratch-sidecar.sh start [UI_DIR]
#   HP_HOME=/tmp/hp-dev-1 proof/scratch-sidecar.sh stop
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
HP_HOME=${HP_HOME:?set HP_HOME}
PORT=${HP_PORT:-9230}
PIDFILE="$HP_HOME/sidecar.pid"
ACTION=${1:-start}
UI_DIR=${2:-$PWD/packages/hermes_plus/src/hermes_plus/ui_dist}

case "$ACTION" in
  stop)
    if [ -f "$PIDFILE" ]; then
      kill -TERM "$(cat "$PIDFILE")" 2>/dev/null
      for _ in $(seq 1 30); do kill -0 "$(cat "$PIDFILE")" 2>/dev/null || break; sleep 1; done
      kill -KILL "$(cat "$PIDFILE")" 2>/dev/null
      rm -f "$PIDFILE"
    fi
    ss -tln 2>/dev/null | grep -q ":$PORT " && echo "port $PORT still busy" || echo "port $PORT free"
    ;;
  start)
    export HERMES_HOME="$HP_HOME"
    export HERMES_PLUS_HERMES_BIN="${HERMES_PLUS_HERMES_BIN:-$HOME/Code/ship-test/venv/bin/hermes}"
    nohup .venv/bin/hermes-plus serve --host 127.0.0.1 --port "$PORT" \
      --hermes-bin "$HERMES_PLUS_HERMES_BIN" --ui-dir "$UI_DIR" \
      >"$HP_HOME/serve.log" 2>&1 &
    echo $! >"$PIDFILE"
    for _ in $(seq 1 240); do
      grep -q 'Hermes Plus login:' "$HP_HOME/serve.log" && break
      kill -0 "$(cat "$PIDFILE")" 2>/dev/null || { echo "sidecar died"; tail -20 "$HP_HOME/serve.log"; exit 1; }
      sleep 1
    done
    grep -o "http://127.0.0.1:$PORT/?token=[A-Za-z0-9_-]*" "$HP_HOME/serve.log" | head -1 >"$HP_HOME/login-url.txt"
    echo "pid $(cat "$PIDFILE") ui=$UI_DIR url-bytes $(wc -c <"$HP_HOME/login-url.txt")"
    ;;
  *) echo "usage: $0 start|stop [ui_dir]" >&2; exit 2;;
esac
