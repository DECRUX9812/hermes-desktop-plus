# Changelog

All notable changes to Hermes Desktop Plus are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-24

### Added

- **Five new opt-in UI plugins**, each paired with a Python-side extra plugin
  that owns its data and its JSON-RPC surface:
  - **Group Rooms** — rooms with bot seats, threads and a shared composer;
    handoffs move an open thread between seats.
  - **Command Room** — escalations, the CEO board (needs you / waiting /
    working / answered), the 24h brief and catch-up, seat budgets, and fork
    consensus semantics: votes collect asked-roster ballots in chat
    (`decide:`), show waiting-on and tally bars with a tone chip and message
    anchoring, and only the room owner can call a vote, which writes the
    decided option. Votes never auto-settle.
  - **Home Feed** — action cards over what needs you (escalations, open
    handoffs, overdue scheduled jobs) plus the Recent tasks row.
  - **Activity** — recent activity rows and the Now/Next/Done bot rail;
    `last_active` is an int number of seconds (millisecond values are
    normalised, and a zero epoch is never reported as an event).
  - **VS Code** — the Code pane: a code-server embed beside the chat, or a
    `vscode://` deep link, opened on the session workspace. No process is
    ever spawned.
- **Plugin RPC hook** (`register_plugin_method`): extras plugins register
  their own JSON-RPC methods at `register(ctx)` time under the
  `plugin.<plugin_name>.*` namespace. The hook keeps a plugin's RPC surface in
  the plugin: methods are absent until the plugin registers, are dropped when
  it unregisters or closes, never collide with the contracted core surface,
  and the gateway contract does not list `plugin.*` methods because they are
  runtime-registered.

### Changed

- The five new UI plugins ship **opt-in** (`defaultEnabled: false`), matching
  the Python side, where extras are installed only on request.
- Version 0.2.0 across the package, the bridge manifest and the installer.

### Enabling the new plugins

```sh
hermes-plus install --extras group-rooms,command-room,home-feed,activity,vscode
```

Then toggle them on in the desktop's plugin settings (Capabilities ▸
Plugins). They are off until you enable them.

### Fixed

- Doctor's `bridge_version` check no longer warns about a version mismatch on
  every run: the bridge now reports the version from its own `plugin.yaml`
  (previously a stale hardcoded constant).
- Doctor no longer warns "could not parse the installed Hermes version" when
  the compatibility probe times out on a cold runtime: the raw
  `hermes --version` output is re-parsed and the compatibility check is
  re-run with the recovered version.
