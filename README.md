# Hermes Desktop Plus

The Hermes desktop app (sessions, group chat, tool cards, bot mode, team mode,
plugin rail) as an add-on for stock [Hermes Agent](https://github.com/NousResearch/hermes-agent),
served to any browser.

Hermes Desktop Plus never patches Hermes. A sidecar in its own virtualenv serves
the web UI, multiplexes the gateway connection, and fronts a loopback-only stock
`hermes dashboard`. Upgrading Hermes stays one command.

## Install in one command

```sh
curl -fsSL https://raw.githubusercontent.com/DECRUX9812/hermes-desktop-plus/v0.1.0/install.sh | sh
```

The script finds stock Hermes on `PATH`, installs the sidecar into its own tool
environment, copies the bridge plugin into your Hermes home, runs migrations,
and finishes with `hermes-plus doctor`.

Without the script:

```sh
uv tool install https://github.com/DECRUX9812/hermes-desktop-plus/releases/download/v0.1.0/hermes_plus-0.1.0-py3-none-any.whl
hermes-plus install
```

From a clone (development):

```sh
cd /path/to/hermes-desktop-plus

# Build the UI first (requires Node.js and pnpm)
cd packages/ui
pnpm install
pnpm build

# Stage the built UI into the package
rm -rf ../hermes_plus/src/hermes_plus/ui_dist
mkdir -p ../hermes_plus/src/hermes_plus/ui_dist
cp -a dist-webapp/. ../hermes_plus/src/hermes_plus/ui_dist/

# Build the wheel and install from it
cd ../..
uv build --wheel packages/hermes_plus --out-dir /tmp/hermes-plus-build
HERMES_PLUS_SOURCE=/tmp/hermes-plus-build/hermes_plus-*.whl ./install.sh
```

## Serve over the web

```sh
hermes-plus serve --host 0.0.0.0 --port 9129 --auth password --password-file ~/.hermes-plus-password
```

`--auth` is `token` (default), `password`, or `none`. `none` is refused on a
non-loopback host. Add `--trust-proxy-headers` when a reverse proxy terminates
authentication.

For a quick public URL:

```sh
cloudflared tunnel --url http://127.0.0.1:9129
```

The Docker compose file ships the same tunnel as a sidecar:

```sh
docker compose -f docker/compose.yaml --profile tunnel up -d
```

Put **Cloudflare Access** (Zero Trust SSO/MFA) in front of the hostname. The
sidecar is the only process reachable from the internet; stock Hermes stays on
loopback.

## Docker

```sh
cp docker/.env.example docker/.env      # set HERMES_PLUS_PASSWORD
docker compose -f docker/compose.yaml up -d --build
```

Then open `http://127.0.0.1:9129`. The image is a thin layer on the official
`nousresearch/hermes-agent` image; `HERMES_TAG` picks the Hermes release. See
`docker/README.md`.

## Upgrading

```sh
hermes update          # upstream's own command; never touches this add-on
hermes-plus update     # upgrade the sidecar, re-copy the bridge, migrate
hermes-plus doctor     # re-check compatibility
```

Upgrades are safe by design: Hermes's files and virtualenv are never modified,
`hermes-plus` lives in its own environment, and the bridge plugin uses only
Hermes's public plugin API. `doctor` re-runs the compatibility check afterwards.

## Compatibility

Generated from `packages/hermes_plus/src/hermes_plus/compat/matrix.json`:

| Field | Value | Meaning |
| --- | --- | --- |
| Adapter API | `1` | Wire contract between the sidecar and the adapter. |
| Minimum | `0.22.0` | Below this, `serve` refuses and `doctor` explains the fix. |
| Known good releases | `0.22.0`, `0.22.1` | Full feature set, contract-tested in CI. |
| Known good commits | `379cf12` | Abbreviated upstream shas accepted as known-good. |
| Tested main | `379cf122` | The upstream main commit nightly tracks. |
| Newest tested minor | `0.22` | A newer minor is served with a banner, features gated on capability probes. |

A version above the minimum that is not listed is **untested**: the server runs,
prints a banner, and hides any feature whose upstream methods or events are
missing. A version below the minimum is **unsupported** and `serve` refuses.

## How it works

```
browser ──> hermes-plus sidecar :9129  (our process, our venv, our auth)
              ├─ serves the web UI + our own HTTP routes (uploads, share, mobile, plugins)
              ├─ /api/ws  multiplexer: our method namespaces here, everything else forwarded
              └─ adapter ──loopback WS + CLI──> stock `hermes dashboard` (child, 127.0.0.1)
                                                   └─ loads $HERMES_HOME/plugins/hermes-plus/
                                                      bridge plugin (stdlib-only, public ctx API)
```

## Screenshots

To be captured into `docs/screenshots/`:

- `chat-tool-cards.png`: a chat turn with tool cards, diffs and todos
- `sessions-sidebar.png`: the sessions sidebar with the ask dialog
- `group-chat.png`: a bot room with the mailbox and roster
- `team-mode.png`: a team goal with tasks, budget and audit
- `plugin-rail.png`: the desktop plugin rail
- `mobile-companion.png`: the mobile companion page on a phone

## FAQ

**Does it modify Hermes?**
No. Hermes is stock and untouched. The sidecar and the bridge plugin are the
only additions, and the bridge plugin uses Hermes's documented plugin API.

**Where is my data?**
Everything of ours lives under your Hermes home, in
`<HERMES_HOME>/hermes-plus/`: `plus.db` (SQLite) plus uploaded files under
`hermes-plus/files/`. Hermes's own sessions, config and credentials stay in
their usual places and are never copied into our database.

**Can I uninstall cleanly?**

```sh
hermes-plus uninstall                 # disable + remove the bridge plugin
hermes-plus uninstall --purge --yes   # also delete plus.db and uploaded files
```

Then `uv tool uninstall hermes-plus` (or `pipx uninstall hermes-plus`) removes
the sidecar itself. Hermes is left exactly as it was.

## Contributing

See `CONTRIBUTING.md`. The short version: only `adapter/` and `bridge/` may know
anything about Hermes internals, and tests use real processes and a real stock
Hermes, never mocks.

## License

MIT. See `LICENSE`. The web UI is derived from Hermes Agent's MIT-licensed
desktop app; see `NOTICE`.
