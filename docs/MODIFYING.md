# Modifying Hermes Desktop Plus

A short, plain-language guide to changing the web desktop app.

## The big picture

Stock Hermes Agent (from Nous) is installed and updated **as-is**. Hermes Desktop Plus
is an add-on next to it. It never edits stock files. That way the normal
updater (`hermes update`, or the update chip in the app) keeps working. After an update the
add-on is still there, untouched, and `hermes-plus doctor` checks that it still fits.

There are three parts to the add-on:

| Part | Where | What it is |
|---|---|---|
| Web UI | `packages/ui/` | The React app you see in the browser |
| UI customizations | `packages/ui/overlay/` | Our own changes that survive a UI re-sync |
| Server ("sidecar") | `packages/hermes_plus/` | `hermes-plus serve`: serves the UI and talks to stock Hermes |
| Bridge plugin | `packages/hermes_plus/src/hermes_plus/bridge/` | A normal Hermes plugin, installed into `HERMES_HOME/plugins` |
| Extra plugins/skills | `packages/hermes_plus/src/hermes_plus/extras/` | Optional add-ons, copied into `HERMES_HOME` |

**Rule of thumb:** put new behaviour in a plugin, a skill, or `packages/ui/overlay/`. Do not
patch stock Hermes. A core patch is lost on the next `hermes update`.

## Changing the UI

1. Edit files under `packages/ui/src/` (or `packages/ui/overlay/` for add-on-only features).
2. Run the checks:
   ```sh
   cd packages/ui
   npm run typecheck
   npm test            # vitest, the "ui" project
   ```
3. Build the static site:
   ```sh
   npm --prefix packages/ui run build:webapp      # output: packages/ui/dist-webapp
   ```

`packages/ui/src` is periodically refreshed from the desktop app source with
`scripts/sync-ui-from-fork.sh <repo> <ref>`. The sync replaces `src/`. It keeps `overlay/`,
the package manifests and `RUNTIME.md`, so your lasting customizations belong in `overlay/`.

## Previewing on a test port (never on the live port)

Use a throwaway home, so your real sessions and settings are untouched:

```sh
cp -a ~/.hermes ~/.hermes-preview          # once; or start from an empty dir
HERMES_HOME=~/.hermes-preview hermes-plus serve \
    --host 127.0.0.1 --port 9140 --auth token \
    --ui-dir packages/ui/dist-webapp
```

Open the printed URL. Stop it with Ctrl-C. Delete `~/.hermes-preview` when you are done.

For a hot-reloading dev server while you edit: `npm --prefix packages/ui run dev` (port 5174).

## Shipping a change

```sh
scripts/check-no-personal-data.sh            # must pass before every push
uv build --wheel packages/hermes_plus --out-dir dist
HERMES_PLUS_SOURCE=dist/hermes_plus-*.whl ./install.sh   # reinstall locally
hermes-plus doctor
```

Then restart whatever runs `hermes-plus serve` (for example its systemd unit), and reload the page.

## Updating stock Hermes

Use the normal updater: `hermes update`, or the update chip in the app. Afterwards:

```sh
hermes-plus doctor        # confirms the bridge plugin and sidecar still fit the new version
```

To try a new Nous release before updating a real install, run
`scripts/check-nous-release.sh <tag>`. It installs the add-on on that release in a temporary
home and smoke-tests `serve`. This step is optional.
