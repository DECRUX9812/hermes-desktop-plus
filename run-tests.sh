#!/usr/bin/env bash
# Dev test runner: the three pytest legs, in order, each under the shared
# build lock. Replaces the old profiles-only debug runner.
#
# Refuses to start unless HERMES_PLUS_TEST_HERMES names a stock hermes
# binary: every leg here may reach Hermes, and resolving `hermes` from PATH
# silently tests a personal install instead (see CONTRIBUTING.md,
# "Getting a stock Hermes for tests").
#
# Legs:
#   unit         no real processes (tests/unit + the unmarked top-level tests)
#   integration  real sidecar/dashboard subprocesses (marker: integration)
#   contract     real stock Hermes CLI, dashboard and /api/ws (marker: contract)
#
# Optional, test-only: HERMES_PLUS_TEST_SHARED_INSTALLS=<provisioned HERMES_HOME>
# lets every fresh fixture home borrow that home's installs/, so stock does not
# build a dependency environment per home (387-645s each). The named home must be
# the one this binary was provisioned with; see CONTRIBUTING.md.
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${HERMES_PLUS_TEST_HERMES:-}" ]; then
  echo "HERMES_PLUS_TEST_HERMES is required: set it to a stock hermes binary," >&2
  echo "e.g. export HERMES_PLUS_TEST_HERMES=\"\$HOME/.hermes/hermes-agent/.venv/bin/hermes\"" >&2
  exit 2
fi
if [ -n "${HERMES_PLUS_TEST_SHARED_INSTALLS:-}" ]; then
  echo "shared installs: $HERMES_PLUS_TEST_SHARED_INSTALLS/installs"
fi
if [ ! -x .venv/bin/pytest ]; then
  echo "no .venv/bin/pytest; run:" >&2
  echo "  uv venv .venv --python 3.11 && uv pip install --python .venv/bin/python -e 'packages/hermes_plus[test]'" >&2
  exit 2
fi

rc=0
for leg in unit integration contract; do
  case "$leg" in
    unit)        marker="not contract and not integration" ;;
    integration) marker="integration" ;;
    contract)    marker="contract" ;;
  esac
  echo ""
  echo "=== leg: $leg (marker: $marker) ==="
  flock /tmp/hermes-build.lock \
    .venv/bin/pytest packages/hermes_plus/tests -q -m "$marker" --tb=short -p no:cacheprovider || rc=1
done
echo ""
if [ "$rc" -eq 0 ]; then
  echo "all three legs passed."
else
  echo "at least one leg failed." >&2
fi
exit "$rc"
