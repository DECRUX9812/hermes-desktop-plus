#!/usr/bin/env bash
# Does Desktop Plus still work on a given stock Hermes release?
#
#   scripts/check-nous-release.sh [tag]        default: newest stable v* tag
#
# Needs: a stock Hermes checkout (HERMES_STOCK_DIR, default ~/Code/hermes-stock) and the
# wheel (HERMES_PLUS_WHEEL, default: newest dist/*.whl). Uses its own HERMES_HOME
# (CHECK_HOME, default: a temp dir) and port (CHECK_PORT, default 9131); never touches
# a live site. Exit 0 = safe to upgrade, non-zero = do not.
set -euo pipefail

STOCK="${HERMES_STOCK_DIR:-$HOME/Code/hermes-stock}"
PORT="${CHECK_PORT:-9131}"
HOME_DIR="${CHECK_HOME:-$(mktemp -d)}"
WHEEL="${HERMES_PLUS_WHEEL:-$(ls -t "$(dirname "$0")"/../dist/hermes_plus-*.whl 2>/dev/null | head -1)}"
TAG="${1:-$(git -C "$STOCK" tag -l 'v[0-9]*' --sort=-v:refname | grep -v canary | head -1)}"
export DBUS_SESSION_BUS_ADDRESS=disabled:
fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo "== $*"; }

[ -n "$WHEEL" ] && [ -f "$WHEEL" ] || fail "no wheel (set HERMES_PLUS_WHEEL)"
step "stock Hermes $TAG in $STOCK"
git -C "$STOCK" fetch -q --tags origin 2>/dev/null || git -C "$STOCK" fetch -q --tags upstream || true
git -C "$STOCK" checkout -q --detach "$TAG"

BIN="$(mktemp -d)/hermes"
printf '#!/bin/sh\nexec %s/scripts/run-in-hermes-env %s/hermes "$@"\n' "$STOCK" "$STOCK" > "$BIN"
chmod +x "$BIN"
export HERMES_HOME="$HOME_DIR" HERMES_PLUS_HERMES_BIN="$BIN"

step "install add-on into a throwaway home ($HOME_DIR)"
HERMES_PLUS_SOURCE="$WHEEL" HERMES_PLUS_FORCE=1 sh "$(dirname "$0")/../install.sh" >/tmp/check-nous-install.log 2>&1 \
  || { tail -20 /tmp/check-nous-install.log; fail "install"; }

step "doctor"
hermes-plus doctor | tee /tmp/check-nous-doctor.log
grep -q '^\[FAIL' /tmp/check-nous-doctor.log && fail "doctor reported FAIL"

step "serve + smoke on :$PORT"
LOG="$(mktemp)"
hermes-plus serve --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 2; done
TOKEN="$(grep -o 'token=[A-Za-z0-9_-]*' "$LOG" | head -1 | cut -d= -f2)"
[ -n "$TOKEN" ] || fail "no login token in serve log"
JAR="$(mktemp)"
curl -sS -L -c "$JAR" -o /dev/null "http://127.0.0.1:$PORT/?token=$TOKEN"
curl -sSf -b "$JAR" "http://127.0.0.1:$PORT/api/plus/info" >/dev/null || fail "/api/plus/info"
curl -sSf -b "$JAR" "http://127.0.0.1:$PORT/api/sessions?limit=5" >/dev/null || fail "/api/sessions"
echo "OK: Desktop Plus works on Hermes $TAG"
