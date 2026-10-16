#!/usr/bin/env bash
#
# S10: clean-install proof on stock Hermes.
#
# Installs hermes-plus the way a user does (uv tool install + `hermes-plus
# install`) into a fresh temp HERMES_HOME, configures a real model provider,
# serves the sidecar on 127.0.0.1:9230, drives the packaged UI in headless
# Chromium (boot, chat, sessions, bot team, bot identity, plugin rail, auth
# gate, /api/plus/info), then uninstalls/reinstalls and leaves the machine
# clean. Re-runnable: everything below is scripted and nothing is stubbed.
#
# Usage:
#   proof/run-proof.sh                 # full run
#   KEEP_TMP=1 proof/run-proof.sh      # keep the temp home and logs for debugging
#   flock /tmp/hermes-build.lock env HERMES_PLUS_PROOF_LOCK=none proof/run-proof.sh
#                                      # one lock hold for the whole proof (the
#                                      # inner heavy steps then run unlocked)
#
# Environment:
#   HERMES_PLUS_PROOF_HERMES  stock hermes binary (required; no default)
#   HERMES_PLUS_PROOF_CHROME  chromium binary (default: newest ~/.cache/ms-playwright chromium)
#   HERMES_PLUS_PROOF_MODEL   cliproxy model id (default gemini-3.8-flash-high; see below)
#   HERMES_PLUS_PROOF_LOCK    lock file (default /tmp/hermes-build.lock; "none" = already held)
#   HERMES_PLUS_PROOF_PORT    sidecar port (default 9230)
#   SHOTS_DIR                 screenshot output (default ~/Videos/ship-shots)
#   TMPDIR                    scratch root for the tool install and the temp home
#                             (default /tmp; point it at disk when /tmp is a tmpfs)
#
# The model id is whatever the local cliproxy actually serves. TASK.md names
# `gemini-3.7-flash-high`, which this proxy rejects with
# `HTTP 400 unknown provider for model ...`; `gemini-3.8-flash-high` is the id
# `GET /v1/models` advertises and the one the proof turn uses.

set -uo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HERMES_BIN=${HERMES_PLUS_PROOF_HERMES:-}
if [ -z "$HERMES_BIN" ]; then
  echo "HERMES_PLUS_PROOF_HERMES is required: set it to the stock hermes binary to prove against." >&2
  echo "The proof never defaults to a machine-specific path." >&2
  exit 2
fi
PORT=${HERMES_PLUS_PROOF_PORT:-9230}
SHOTS_DIR=${SHOTS_DIR:-"$HOME/Videos/ship-shots"}
LOCK=${HERMES_PLUS_PROOF_LOCK:-/tmp/hermes-build.lock}

# The proof installs a real tool and a real HERMES_HOME, so it needs room: honor
# TMPDIR (set it to a disk-backed dir on hosts where /tmp is a small tmpfs).
TMP_ROOT=${TMPDIR:-/tmp}
mkdir -p "$TMP_ROOT"
WORK=$(mktemp -d "$TMP_ROOT/hp-proof-work-XXXXXX")
HOME_DIR=$(mktemp -d "$TMP_ROOT/hp-proof-XXXXXX")
LOG_DIR="$WORK/logs"
mkdir -p "$LOG_DIR" "$SHOTS_DIR"

SERVE_PID=""
FAILURES=0
RESULTS="$WORK/results.txt"
: >"$RESULTS"

say() { printf '\n=== %s\n' "$*"; }

verdict() { # verdict <name> <0|1> <detail>
  local name=$1 status=$2 detail=$3
  if [ "$status" -eq 0 ]; then
    printf 'PASS  %s :: %s\n' "$name" "$detail" | tee -a "$RESULTS"
  else
    printf 'BROKEN %s :: %s\n' "$name" "$detail" | tee -a "$RESULTS"
    FAILURES=$((FAILURES + 1))
  fi
}

heavy() { flock -w 3600 "$LOCK" "$@"; }
# A caller that already holds the lock runs the whole proof in one hold:
#   flock /tmp/hermes-build.lock env HERMES_PLUS_PROOF_LOCK=none proof/run-proof.sh
# (nested flock on the same file would deadlock, so the inner steps must run
# unlocked in that case).
if [ "${HERMES_PLUS_PROOF_LOCK:-}" = "none" ]; then
  heavy() { "$@"; }
fi

cleanup() {
  if [ -n "$SERVE_PID" ] && kill -0 "$SERVE_PID" 2>/dev/null; then
    kill -TERM "$SERVE_PID" 2>/dev/null
    for _ in $(seq 1 30); do kill -0 "$SERVE_PID" 2>/dev/null || break; sleep 1; done
    kill -KILL "$SERVE_PID" 2>/dev/null
  fi
  # The dashboard child is spawned by the sidecar; reap any that outlived it.
  local stray
  stray=$(ps -eo pid,args | grep -F 'hermes dashboard' | grep -F "$HOME_DIR" | awk '{print $1}' || true)
  for pid in $stray; do kill -TERM "$pid" 2>/dev/null; done
  sleep 2
  if [ "${KEEP_TMP:-0}" = "1" ]; then
    printf '\nkept: HOME_DIR=%s WORK=%s\n' "$HOME_DIR" "$WORK"
  else
    rm -rf "$HOME_DIR" "$WORK"
  fi
}
trap cleanup EXIT

say "preflight"
if [ ! -x "$HERMES_BIN" ]; then
  echo "stock Hermes binary is not executable: $HERMES_BIN" >&2
  exit 2
fi
command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }
if ss -tln 2>/dev/null | grep -q ":$PORT "; then
  echo "port $PORT is already in use; stop the process or set HERMES_PLUS_PROOF_PORT" >&2
  exit 2
fi

CHROME=${HERMES_PLUS_PROOF_CHROME:-}
if [ -z "$CHROME" ]; then
  CHROME=$(ls -1 "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | sort -V | tail -1)
fi
if [ -z "$CHROME" ] || [ ! -x "$CHROME" ]; then
  echo "no chromium found; set HERMES_PLUS_PROOF_CHROME" >&2
  exit 2
fi
printf 'hermes=%s\nchrome=%s\nhome=%s\n' "$HERMES_BIN" "$CHROME" "$HOME_DIR"

# A temp HERMES_HOME cannot borrow ~/.local/bin or the ambient PATH: the stock
# CLI is handed explicitly for every step below.
export HERMES_HOME="$HOME_DIR"
export HERMES_PLUS_HERMES_BIN="$HERMES_BIN"
export HERMES_PLUS_TEST_HERMES="$HERMES_BIN"
export HERMES_PLUS_TEST_MODEL_CONFIG="$HOME_DIR/config.yaml"
export UV_TOOL_DIR="$WORK/tools"
export UV_TOOL_BIN_DIR="$WORK/bin"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}"
TOOL="$UV_TOOL_BIN_DIR/hermes-plus"
export PATH="$UV_TOOL_BIN_DIR:$PATH"

say "stage the UI build into the package"
if [ -f "$REPO_ROOT/packages/ui/dist-webapp/index.html" ]; then
  rm -rf "$REPO_ROOT/packages/hermes_plus/src/hermes_plus/ui_dist"
  mkdir -p "$REPO_ROOT/packages/hermes_plus/src/hermes_plus/ui_dist"
  cp -a "$REPO_ROOT/packages/ui/dist-webapp/." "$REPO_ROOT/packages/hermes_plus/src/hermes_plus/ui_dist/"
  entry_count=$(find "$REPO_ROOT/packages/hermes_plus/src/hermes_plus/ui_dist" -type f | wc -l)
  verdict "ui_dist staged" 0 "$entry_count files"
else
  verdict "ui_dist staged" 1 "packages/ui/dist-webapp is missing; run the UI build first"
  exit 1
fi

say "install hermes-plus as a user does (uv tool install + hermes-plus install)"
if heavy uv tool install --force "$REPO_ROOT/packages/hermes_plus" >"$LOG_DIR/tool-install.log" 2>&1; then
  verdict "uv tool install" 0 "$(tail -1 "$LOG_DIR/tool-install.log")"
else
  verdict "uv tool install" 1 "$(tail -3 "$LOG_DIR/tool-install.log" | tr '\n' ' ')"
  exit 1
fi

if heavy "$TOOL" install >"$LOG_DIR/install.log" 2>&1; then
  verdict "hermes-plus install" 0 "$(grep -c . "$LOG_DIR/install.log") lines of output"
else
  verdict "hermes-plus install" 1 "$(tail -5 "$LOG_DIR/install.log" | tr '\n' ' ')"
fi
sed -n '1,40p' "$LOG_DIR/install.log"

# The extra plugins are opt-in (`--extras`), so the base install above never
# touches them. This second, idempotent install is what a user runs to get
# backdrops/memory/langfuse; it also proves the extras path itself. stdin is
# /dev/null: `hermes plugins enable` asks for capability consent only on a TTY
# and fails closed otherwise, so this can never hang.
EXTRAS="backdrops,claude-mem,holographic,langfuse"
say "install the opt-in extras"
if heavy "$TOOL" install --extras "$EXTRAS" <"/dev/null" >"$LOG_DIR/extras-install.log" 2>&1; then
  verdict "hermes-plus install --extras" 0 \
    "$(grep -c . "$LOG_DIR/extras-install.log") lines; $(grep -ci 'extra plugin' "$LOG_DIR/extras-install.log") extra plugin line(s)"
else
  verdict "hermes-plus install --extras" 1 "$(tail -5 "$LOG_DIR/extras-install.log" | tr '\n' ' ')"
fi
grep -i 'extra plugin' "$LOG_DIR/extras-install.log" | sed -n '1,8p'

say "stock Hermes sees the extras plugins"
plugins_plain=$("$HERMES_BIN" plugins list --plain --no-bundled 2>/dev/null || true)
extras_missing=""
extras_rows=0
for extra in backdrops claude-mem holographic langfuse; do
  if printf '%s' "$plugins_plain" | grep -qi -- "$extra"; then
    extras_rows=$((extras_rows + 1))
  else
    extras_missing="$extras_missing $extra"
  fi
done
if [ -z "$extras_missing" ]; then
  verdict "extras plugins installed and enabled" 0 \
    "$extras_rows of 4 rows in \`hermes plugins list --plain --no-bundled\` (backdrops, claude-mem, holographic, langfuse)"
else
  verdict "extras plugins installed and enabled" 1 "missing from \`hermes plugins list\`:$extras_missing"
fi

say "configure the model provider in the temp home"
# The model id is whatever the local cliproxy actually serves. TASK.md names
# `gemini-3.7-flash-high`, which this proxy answers with
# "HTTP 400 unknown provider for model ..." (see the S10 report); the id below
# is the one `GET /v1/models` advertises and the one the proof turn uses.
PROOF_MODEL=${HERMES_PLUS_PROOF_MODEL:-gemini-3.8-flash-high}
# MERGE, never clobber: `hermes-plus install` has already written
# `plugins.enabled` (and onboarding state) into this file, and a plain
# overwrite silently disables the bridge plugin — doctor then reports
# `bridge_plugin: warn` even though install succeeded.
if heavy python3 - "$HOME_DIR/config.yaml" "$PROOF_MODEL" <<'PY' >"$LOG_DIR/config-merge.log" 2>&1
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
model = sys.argv[2]
config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
config = config if isinstance(config, dict) else {}
providers = config.setdefault("providers", {})
providers["cliproxy"] = {
    "api": "http://127.0.0.1:8317/v1",
    "name": "Local proxy",
    "key_env": "CLIPROXY_API_KEY",
    "transport": "chat_completions",
    "context_length": 1048576,
    "default_model": model,
}
config["model"] = {"provider": "cliproxy", "default": model}
# The composer-prediction auxiliary task is OFF by default; the backend parity
# proof turns it on in this temp home so `session.predict_next` is exercised for
# real instead of only shape-checked. It never touches the operator's home.
auxiliary = config.setdefault("auxiliary", {})
auxiliary.setdefault("composer_prediction", {})["enabled"] = True
path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
print(f"providers.cliproxy + model.default = {model}; plugins section kept: {'plugins' in config}"
      f"; composer_prediction enabled: {auxiliary['composer_prediction']['enabled']}")
PY
then
  verdict "provider configured" 0 "$(tail -1 "$LOG_DIR/config-merge.log")"
else
  verdict "provider configured" 1 "$(tail -3 "$LOG_DIR/config-merge.log" | tr '\n' ' ')"
fi
if grep '^CLIPROXY_API_KEY=' "$HOME/.hermes/.env" >"$HOME_DIR/.env" 2>/dev/null && [ -s "$HOME_DIR/.env" ]; then
  chmod 600 "$HOME_DIR/.env"
  verdict "provider key copied" 0 "1 line into the temp .env (never printed)"
else
  verdict "provider key copied" 1 "no CLIPROXY_API_KEY line available; model turns will be blocked"
fi

say "doctor"
if heavy "$TOOL" doctor --json >"$LOG_DIR/doctor.json" 2>"$LOG_DIR/doctor.err"; then
  doctor_rc=0
else
  doctor_rc=$?
fi
# `doctor --json` prints the check list itself (a JSON array), not an envelope.
if python3 - "$LOG_DIR/doctor.json" <<'PY' | tee -a "$RESULTS"
import json
import sys

checks = json.load(open(sys.argv[1]))
bad = [c for c in checks if c["status"] != "ok"]
for check in checks:
    print(f'{check["status"].upper():4} {check["name"]}: {check["message"]}')
print("PASS  doctor all-ok" if not bad else "BROKEN doctor: " + ", ".join(c["name"] for c in bad))
sys.exit(1 if bad else 0)
PY
then
  verdict "doctor all-ok" 0 "exit code $doctor_rc, $(grep -c . "$LOG_DIR/doctor.json") bytes of JSON"
else
  verdict "doctor all-ok" 1 "exit code $doctor_rc, see the check list above"
fi

say "update --check"
if heavy "$TOOL" update --check >"$LOG_DIR/update-check.log" 2>&1; then
  verdict "update --check" 0 "$(head -2 "$LOG_DIR/update-check.log" | tr '\n' ' ')"
else
  verdict "update --check" 1 "$(tail -3 "$LOG_DIR/update-check.log" | tr '\n' ' ')"
fi

say "a neutral workspace for the shots"
# The composer chip and the project label read the session workspace, which is
# the backend's cwd. Running serve from the worktree put `ship/s27` and
# `hermes-desktop-plus` in every screenshot; a throwaway repo under the temp
# root makes them read `main` and `demo-project` instead.
DEMO_PROJECT="$WORK/demo-project"
mkdir -p "$DEMO_PROJECT"
printf '# demo-project\n\nThrowaway repository for the proof screenshots.\n' >"$DEMO_PROJECT/README.md"
# The tool-card turn needs something only a tool can reveal: a run-unique
# token in a file inside the session workspace (serve's cwd). The model cannot
# quote it from memory, so a reply carrying the token proves a tool ran.
TOOL_FILE=proof-token.txt
TOOL_TOKEN="hp-$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
printf 'The proof token is %s.\n' "$TOOL_TOKEN" >"$DEMO_PROJECT/$TOOL_FILE"
git -C "$DEMO_PROJECT" init -q -b main
git -C "$DEMO_PROJECT" add README.md "$TOOL_FILE"
if git -C "$DEMO_PROJECT" commit -q -m "demo project" >"$LOG_DIR/demo-project.log" 2>&1; then
  verdict "neutral workspace" 0 \
    "$DEMO_PROJECT on branch $(git -C "$DEMO_PROJECT" rev-parse --abbrev-ref HEAD) is serve's cwd"
else
  verdict "neutral workspace" 1 "git commit failed: $(tail -3 "$LOG_DIR/demo-project.log" | tr '\n' ' ')"
fi

say "install a desktop plugin through the disk door"
# The second plugin door: `<hermes home>/desktop-plugins/<id>/plugin.js`, which
# the app's watcher loads at runtime (the door an agent or a user writes
# through). The repo's hello-runtime example is written FOR this door — it is
# deliberately not a bundled module (`plugin.runtime.js`, and the boot glob is
# `plugin.{js,ts,tsx}`) — so installing it is the honest way to prove the door,
# the loader, and the `/api/desktop-plugins/*` routes against a real install.
DESKTOP_PLUGIN_ID=hello-runtime
DESKTOP_PLUGIN_DIR="$HOME_DIR/desktop-plugins/$DESKTOP_PLUGIN_ID"
DESKTOP_PLUGIN_SRC="$REPO_ROOT/packages/ui/src/plugins/$DESKTOP_PLUGIN_ID/plugin.runtime.js"
mkdir -p "$DESKTOP_PLUGIN_DIR"
if [ -f "$DESKTOP_PLUGIN_SRC" ] && cp "$DESKTOP_PLUGIN_SRC" "$DESKTOP_PLUGIN_DIR/plugin.js"; then
  verdict "desktop plugin installed through the disk door" 0 \
    "$DESKTOP_PLUGIN_ID/plugin.js ($(wc -c <"$DESKTOP_PLUGIN_DIR/plugin.js") bytes)"
else
  verdict "desktop plugin installed through the disk door" 1 "no $DESKTOP_PLUGIN_SRC"
fi

say "serve"
( cd "$DEMO_PROJECT" && exec "$TOOL" serve --host 127.0.0.1 --port "$PORT" --hermes-bin "$HERMES_BIN" ) \
  >"$LOG_DIR/serve.log" 2>&1 &
SERVE_PID=$!
LOGIN_URL=""
for _ in $(seq 1 180); do
  LOGIN_URL=$(grep -o "http://127.0.0.1:$PORT/?token=[A-Za-z0-9_-]*" "$LOG_DIR/serve.log" | head -1)
  [ -n "$LOGIN_URL" ] && break
  if ! kill -0 "$SERVE_PID" 2>/dev/null; then break; fi
  sleep 1
done
if [ -z "$LOGIN_URL" ]; then
  verdict "hermes-plus serve" 1 "no login URL; serve.log tail: $(tail -5 "$LOG_DIR/serve.log" | tr '\n' ' ')"
  grep -o 'token=[A-Za-z0-9_-]*' "$LOG_DIR/serve.log" | head -1 >/dev/null || true
  TOKEN=""
else
  TOKEN=${LOGIN_URL##*token=}
  verdict "hermes-plus serve" 0 "listening on 127.0.0.1:$PORT with a one-time token URL"
fi

say "browser proof"
if [ -n "$LOGIN_URL" ]; then
  # Drop last run's artefacts so a stale png can never be mistaken for this run.
  rm -f "$SHOTS_DIR"/[0-9][0-9]-*.png "$SHOTS_DIR/browser-report.json"
  heavy node "$REPO_ROOT/proof/playwright/proof.mjs" \
    --base "http://127.0.0.1:$PORT" \
    --token-url "$LOGIN_URL" \
    --out "$SHOTS_DIR" \
    --chrome "$CHROME" \
    --tool-file "$TOOL_FILE" \
    --tool-token "$TOOL_TOKEN" \
    --desktop-plugin "$DESKTOP_PLUGIN_ID" \
    --json "$WORK/browser-report.json" 2>&1 | tee "$LOG_DIR/browser.log"
  browser_rc=${PIPESTATUS[0]}
  verdict "browser proof" "$browser_rc" "see $SHOTS_DIR and $WORK/browser-report.json"
  python3 - "$WORK/browser-report.json" <<'PY' | tee -a "$RESULTS"
import json, sys
try:
    report = json.load(open(sys.argv[1]))
except Exception as exc:
    print(f"BROKEN browser report: {exc}")
    sys.exit(0)
for item in report["results"]:
    print(f'{item["status"].upper():8} {item["name"]} :: {item["detail"]}')
PY
else
  verdict "browser proof" 1 "skipped: the sidecar never printed a login URL"
fi

say "GET /api/plus/info"
if [ -n "${TOKEN:-}" ]; then
  http_code=$(curl -s -o "$WORK/plus-info.json" -w '%{http_code}' \
    -H "X-Hermes-Session-Token: $TOKEN" "http://127.0.0.1:$PORT/api/plus/info")
  cat "$WORK/plus-info.json" | tee -a "$RESULTS"
  echo
  if python3 - "$WORK/plus-info.json" <<'PY' | tee -a "$RESULTS"
import json
import sys

body = json.load(open(sys.argv[1]))
assert body.get("hermes_version") or body.get("hermes_commit"), body
compat = (body.get("compat") or {}).get("status")
assert compat in ("supported", "untested", "unsupported"), body
print(
    f'PASS  /api/plus/info :: hermes {body.get("hermes_version")} @ {body.get("hermes_commit")},'
    f" compat {compat}"
)
PY
  then
    verdict "api plus info" 0 "HTTP $http_code"
  else
    verdict "api plus info" 1 "HTTP $http_code, body: $(head -c 200 "$WORK/plus-info.json")"
  fi
fi

say "backend parity proof"
# The surfaces a user does not click: our RPC namespaces, our HTTP routes, the
# host-terminal WS, and the stock cron cross-check for routines. It uses the
# same session credential the browser pass holds. Whether a model turn works is
# read back from the browser report, so a run with no provider marks
# session.ask BLOCKED instead of BROKEN.
if [ -n "${TOKEN:-}" ]; then
  model_expected=$(python3 - "$WORK/browser-report.json" <<'PY'
import json
import sys
try:
    report = json.load(open(sys.argv[1]))
except Exception:
    print("no")
    raise SystemExit
for item in report.get("results", []):
    if item.get("name") == "chat reply streams from the configured model":
        print("yes" if item.get("status") == "pass" else "no")
        break
else:
    print("no")
PY
)
  heavy "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/proof/parity-proof.py" \
    --base "http://127.0.0.1:$PORT" \
    --token "$TOKEN" \
    --hermes-bin "$HERMES_BIN" \
    --hermes-home "$HOME_DIR" \
    --demo-project "$DEMO_PROJECT" \
    --worktree-path "$WORK/proof-worktree" \
    --model-expected "$model_expected" \
    --json "$WORK/parity-report.json" 2>&1 | tee "$LOG_DIR/parity.log"
  parity_rc=${PIPESTATUS[0]}
  grep -E '^\[(PASS|BROKEN|BLOCKED)\]' "$LOG_DIR/parity.log" | tee -a "$RESULTS" >/dev/null
  verdict "backend parity proof" "$parity_rc" \
    "$(grep -c '^\[PASS\]' "$LOG_DIR/parity.log" || true) passed, $(grep -c '^\[BLOCKED\]' "$LOG_DIR/parity.log" || true) blocked, $(grep -c '^\[BROKEN\]' "$LOG_DIR/parity.log" || true) broken (see $WORK/parity-report.json)"
else
  verdict "backend parity proof" 1 "skipped: the sidecar never printed a login URL"
fi

say "shut the sidecar down"
if [ -n "$SERVE_PID" ]; then
  kill -TERM "$SERVE_PID" 2>/dev/null
  for _ in $(seq 1 30); do kill -0 "$SERVE_PID" 2>/dev/null || break; sleep 1; done
  if kill -0 "$SERVE_PID" 2>/dev/null; then
    kill -KILL "$SERVE_PID" 2>/dev/null
    sleep 1
  fi
  SERVE_PID=""
fi
port_free=0
ss -tln 2>/dev/null | grep -q ":$PORT " && port_free=1
verdict "port $PORT released" "$port_free" "$(ss -tln 2>/dev/null | grep ":$PORT " || echo 'nothing listening')"
dash_left=$(pgrep -af 'hermes dashboard' 2>/dev/null | grep -F "$HOME_DIR" || true)
if [ -z "$dash_left" ]; then
  verdict "no dashboard child left" 0 "pgrep found no stock dashboard for $HOME_DIR"
else
  verdict "no dashboard child left" 1 "$dash_left"
fi

say "uninstall then reinstall is idempotent"
schema_before=$(python3 - "$HOME_DIR/hermes-plus/plus.db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print(con.execute("select value from plus_meta where key='schema_version'").fetchone()[0])
PY
)
heavy "$TOOL" uninstall >"$LOG_DIR/uninstall.log" 2>&1
uninstall_rc=$?
[ -d "$HOME_DIR/plugins/hermes-plus" ] && plugin_gone=1 || plugin_gone=0
verdict "uninstall removes the bridge plugin" "$plugin_gone" \
  "exit $uninstall_rc, $(grep -c . "$LOG_DIR/uninstall.log") lines, plugin dir $([ "$plugin_gone" = 0 ] && echo gone || echo present)"

heavy "$TOOL" install >"$LOG_DIR/reinstall.log" 2>&1
reinstall_rc=$?
schema_after=$(python3 - "$HOME_DIR/hermes-plus/plus.db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print(con.execute("select value from plus_meta where key='schema_version'").fetchone()[0])
PY
)
[ -f "$HOME_DIR/plugins/hermes-plus/plugin.yaml" ] && plugin_back=0 || plugin_back=1
verdict "reinstall restores the plugin" "$(( plugin_back + reinstall_rc ))" \
  "exit $reinstall_rc, plugin.yaml $([ "$plugin_back" = 0 ] && echo present || echo missing)"
if [ "$schema_before" = "$schema_after" ]; then
  verdict "plus.db schema unchanged by reinstall" 0 "schema_version $schema_before -> $schema_after"
else
  verdict "plus.db schema unchanged by reinstall" 1 "schema_version $schema_before -> $schema_after"
fi

say "stock Hermes still works standalone"
if "$HERMES_BIN" --version >"$LOG_DIR/stock-version.log" 2>&1; then
  verdict "stock hermes --version" 0 "$(head -1 "$LOG_DIR/stock-version.log")"
else
  verdict "stock hermes --version" 1 "exit $?"
fi
plugins_line=$("$HERMES_BIN" plugins list --plain --no-bundled 2>&1 | grep -i 'hermes-plus' | head -3)
if printf '%s' "$plugins_line" | grep -qi 'hermes-plus'; then
  verdict "stock hermes sees the plugin enabled" 0 "$(printf '%s' "$plugins_line" | tr '\n' ' ')"
else
  verdict "stock hermes sees the plugin enabled" 1 "plugins list has no hermes-plus row"
fi

say "model-marked B1 contract tests against the temp home's config"
if heavy "$REPO_ROOT/.venv/bin/pytest" "$REPO_ROOT/packages/hermes_plus/tests/contract/test_b1_stock.py" \
  -m model -q -rs >"$LOG_DIR/model-tests.log" 2>&1; then
  model_rc=0
else
  model_rc=$?
fi
tail -12 "$LOG_DIR/model-tests.log" | tee -a "$RESULTS"
verdict "B1 model-marked contract tests" "$model_rc" "$(grep -E '^[0-9]+ (passed|failed)|passed|failed' "$LOG_DIR/model-tests.log" | tail -1)"

say "summary"
printf '\n' 
cat "$RESULTS"
printf '\nscreenshots in %s:\n' "$SHOTS_DIR"
ls -1 "$SHOTS_DIR"/*.png 2>/dev/null | tail -10
printf '\nlogs in %s\n' "$LOG_DIR"
if [ -f "$WORK/browser-report.json" ]; then
  cp "$WORK/browser-report.json" "$SHOTS_DIR/browser-report.json" 2>/dev/null || true
fi

if [ "$FAILURES" -gt 0 ]; then
  printf '\n%d step(s) broken; see above.\n' "$FAILURES"
  [ "${KEEP_TMP:-0}" = "1" ] || printf '(temp home deleted; re-run with KEEP_TMP=1 to inspect)\n'
  exit 1
fi
printf '\nall steps passed.\n'
