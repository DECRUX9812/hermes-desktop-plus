# Contributing to Hermes Desktop Plus

Thanks for helping. This document is the working manual: how the repo is laid
out, the one architectural rule we enforce in CI, how to get a real Hermes to
test against, and how a release happens.

## Repo layout

```
packages/hermes_plus/          Python sidecar + CLI (distribution name "hermes-plus")
  src/hermes_plus/
    adapter/                   the ONLY Hermes-aware code (see the adapter rule)
    store/                     plus.db, repositories, migrations/
    rpc/                       JSON-RPC registry and handlers (fork.*, bots_team.*, ...)
    http/                      our own HTTP routes (uploads, share, mobile, plugins, ...)
    server/                    ASGI app: static UI, WS multiplexer, proxy, auth, taps
    cli.py                     install / serve / doctor / update / uninstall
    bridge/                    directory plugin source copied into $HERMES_HOME/plugins/
    extras/                    bundled plugins and skills copied by `install`
    compat/matrix.json         the compatibility matrix (packaged, also published)
  tests/{unit,contract,integration}/
packages/ui/                   React renderer synced from the fork (see below)
packages/shared/               shared TypeScript sources for the renderer
scripts/sync-ui-from-fork.sh   one-shot UI import
scripts/check-no-personal-data.sh
install.sh                     the one-command installer
docker/                        Dockerfile, compose.yaml, entrypoint.sh, README.md
docs/                          ADRs and upstream PR texts
.github/workflows/             ci, upstream-nightly, release
```

## The adapter rule

Only `packages/hermes_plus/src/hermes_plus/adapter/` and `bridge/` may import
Hermes internals. Everything else imports `hermes_plus.adapter`.

`packages/hermes_plus/tests/unit/test_import_boundary.py` walks every source
file outside those two directories and fails on any import whose root module is
`hermes_cli`, `tui_gateway`, `hermes_constants`, `run_agent`, `agent`, `tools`,
`gateway`, `model_tools`, or anything starting with `hermes_state`. If you need a
new Hermes fact, add it to the adapter's public surface (and keep `ADAPTER_API`
in step) rather than importing Hermes from a handler.

Inside the adapter, prefer the CLI and the wire protocol. Today no module in
this package imports Hermes at all: the adapter reaches it through the `hermes`
binary and the dashboard's JSON-RPC WebSocket. If a fact can only be had by
importing Hermes, it belongs in a small helper that runs in the Hermes
interpreter via a subprocess, with every import wrapped in `try/except`, never
in an in-process import from a handler.

## Our data

Each profile gets its own SQLite database at `<HERMES_HOME>/hermes-plus/plus.db`.
It is ours: numbered migrations live in
`src/hermes_plus/store/migrations/000N_*.sql`, the applied version is recorded in
the `plus_meta` table, and opening a database written by a newer schema raises
instead of guessing.

**Never create or alter tables in Hermes's own `state.db`.** If a feature needs
persistence, it goes in `plus.db`. Uploads and generated files go under
`<HERMES_HOME>/hermes-plus/files/`.

To add a migration, add the next `000N_*.sql` file and let the store apply it;
do not edit a migration that has shipped.

## Getting a stock Hermes for tests

Tests run against a real stock Hermes, never a fake. The layout that matters is
the one upstream's installer produces:

```
<root>/                        e.g. ~/.hermes or a temp dir
  hermes-agent/                a git checkout of NousResearch/hermes-agent
  installs/<id>/environments/<gen>/   the pm-managed runtime
  .local/bin/hermes            the launcher (or ~/.local/bin/hermes)
```

`HERMES_HOME` must point at `<root>`, the directory that **owns** the checkout.
The environment-borrow rule: a Hermes home resolves its Python environment from
the checkout it owns. A temp home that does not contain `hermes-agent/` cannot
borrow the install, and `hermes-plus doctor`'s `hermes_env` check fails with a
hint saying so. For tests, either use a home that owns a checkout, or set
`HERMES_PLUS_HERMES_BIN` and let the stock launcher resolve its own runtime.

Provision a fresh stock install with upstream's own tooling, never with pip and
never into an existing install:

```sh
git clone --branch v0.22.1 https://github.com/NousResearch/hermes-agent "$ROOT/hermes-agent"
cd "$ROOT/hermes-agent"
HERMES_HOME="$ROOT" ./setup-hermes.sh --runtime-only
```

Then point the suite at it:

```sh
export HERMES_PLUS_TEST_HERMES="$ROOT/.local/bin/hermes"
```

## Running the tests

`HERMES_PLUS_TEST_HERMES=/path/to/hermes ./run-tests.sh` runs all three legs
below, in order, each under the shared build lock. The raw commands are:

```sh
# one-time, in your worktree
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -e 'packages/hermes_plus[test]'

# unit leg (no real processes)
.venv/bin/pytest packages/hermes_plus/tests -q -m "not contract and not integration"

# integration leg (real sidecar/dashboard subprocesses)
HERMES_PLUS_TEST_HERMES=/path/to/hermes .venv/bin/pytest packages/hermes_plus/tests -q -m integration

# contract: exercises the real stock Hermes CLI, dashboard and /api/ws
HERMES_PLUS_TEST_HERMES=/path/to/hermes .venv/bin/pytest packages/hermes_plus/tests -q -m contract
```

Markers (declared in `packages/hermes_plus/pyproject.toml`):

- `contract`: needs `HERMES_PLUS_TEST_HERMES`. If the binary is missing the test
  **skips with a reason**; it never fakes a pass.
- `integration`: starts real processes.
- `model`: calls a real provider; needs `HERMES_PLUS_TEST_MODEL_CONFIG`.

Wrap anything heavy (installs, pytest, node builds, docker) in the shared lock so
parallel worktrees do not fight over the machine:

```sh
flock /tmp/hermes-build.lock .venv/bin/pytest packages/hermes_plus/tests -q
```

### UI tests

```sh
npm ci
npm --prefix packages/ui run typecheck
npm --prefix packages/ui run test        # vitest
npm --prefix packages/ui run build:webapp
```

### Zero-mock policy

No `unittest.mock`, no monkeypatched fakes, no stub servers standing in for
Hermes. Tests use real SQLite files, real subprocesses and the real stock Hermes.
When Hermes is unavailable, `pytest.skip` with a reason. A test that cannot fail
is worse than no test.

## The UI, and the fork

`packages/ui` is imported from the fork (`feat/hermes-next`) during the
transition; after that, UI work happens here. To re-sync:

```sh
scripts/sync-ui-from-fork.sh ~/path/to/fork-src <ref>
```

The script archives the fork's `apps/desktop` and `apps/shared` sources into
`packages/ui` and `packages/shared`, records the imported ref in
`packages/ui/FORK_SOURCE`, then applies the local **overlay**:
`packages/ui/overlay/` is copied over the freshly imported tree. Anything we need
to change in imported files goes in the overlay, not in a direct edit, so a
re-sync cannot silently discard it. The script finishes by running
`scripts/check-no-personal-data.sh`, which fails if a fork fixture leaked a real
name, hostname or home path.

### Dev loop

Run the stock dashboard and the sidecar in one process, and Vite in another:

```sh
hermes-plus serve --dev-ui-url http://127.0.0.1:5174
npm --prefix packages/ui run dev          # vite on 127.0.0.1:5174
```

The sidecar proxies to Vite for UI assets while serving the real API and
WebSocket routes, so the browser talks to one origin.

## Bumping the compatibility matrix

`upstream-nightly.yml` runs the contract and integration suites against
upstream `main` and the two most recent release tags. When a **new tag** passes,
CI opens a pull request that adds the tag to `known_good` in
`packages/hermes_plus/src/hermes_plus/compat/matrix.json`. To bump it by hand:

1. Run the contract suite against the new tag locally.
2. Add the version to `known_good`, its abbreviated commit to
   `known_good_commits`, and the tag → commit mapping to `known_good_refs` if the
   release is what you tested.
3. Update `tested_main` to the commit nightly should track, and `max_minor` if a
   new minor line opened.
4. Update `HERMES_KNOWN_GOOD_TAG` in `install.sh` and `HERMES_TAG` in
   `docker/Dockerfile` (and `docker/.env.example`) to match.
5. If an upstream surface moved, fix `adapter/` only. The contract suite is
   there to tell you exactly which surface broke.

## Release steps

1. Bump `version` in `packages/hermes_plus/pyproject.toml`.
2. Build the UI, then the wheel, and check `ui_dist/index.html` is inside it.
3. Tag `vX.Y.Z` and push the tag. `release.yml` then builds the wheel, pushes a
   multi-arch image to GHCR, and attaches both the wheel and `install.sh` to
   the GitHub release. PyPI publishing is opt-in via the `PUBLISH_PYPI`
   repository variable. GitHub release creation waits on the `release`
   environment, so a human approves it.
4. Announce the tag, and make sure the matrix names it.
