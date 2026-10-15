# Hermes Desktop Plus: Shipping Report and Handoff

Date: 2026-10-15
Status: Ready for release review
Target upstream: NousResearch/hermes-agent (proven on v0.22.1 at 297552a, main at 379cf122)

---

## 1. What this is

Hermes Desktop Plus is a standalone add-on that runs the full Hermes desktop web application (sessions, group chat, tool cards, bot mode, team mode, and the plugin rail) on top of stock Hermes Agent. It requires zero modifications or core patches to stock Hermes, running the desktop backend as an isolated sidecar process that fronts an untouched, loopback-only Hermes dashboard child. Installation is a single shell command or package install that sets up the sidecar in its own environment and registers a standard-library-only bridge plugin in the Hermes home directory.

---

## 2. Install

### One-line installer

The primary install path executes upstream detection, installs the sidecar into an isolated tool environment via `uv` or `pipx`, registers the bridge plugin and extras, runs database migrations, and performs health diagnostics:

```sh
curl -fsSL https://<INSTALL_URL>/install.sh | sh
```

The URL `<INSTALL_URL>` is a placeholder until a public distribution host is published. The script checks for stock Hermes on `PATH`, `$HOME/.local/bin/hermes`, or `${HERMES_HOME}/.local/bin/hermes`. If Hermes is absent and `HERMES_PLUS_INSTALL_HERMES=1` is set, it invokes the official upstream installer pinned to known-good release tag `v0.22.1`.

### Package manager alternative

Users can install the package directly into an isolated tool environment:

```sh
uv tool install hermes-plus    # or: pipx install hermes-plus
hermes-plus install
```

`hermes-plus install` copies the bridge plugin into `<HERMES_HOME>/plugins/hermes-plus/`, enables it in Hermes config via the Hermes CLI, copies bundled extras and skills into `<HERMES_HOME>/skills/`, and migrates `plus.db`.

### Docker Compose with Cloudflare Tunnel

A multi-stage Docker image builds on top of the official `nousresearch/hermes-agent:v0.22.1` image. A compose file provides the sidecar service and an optional Cloudflare Tunnel sidecar:

```sh
cp docker/.env.example docker/.env      # configure HERMES_PLUS_PASSWORD and optional TUNNEL_TOKEN
docker compose -f docker/compose.yaml up -d --build
```

To run with the public Cloudflare tunnel:

```sh
docker compose -f docker/compose.yaml --profile tunnel up -d
```

### Serving over the web

Run the sidecar directly with host and authentication options:

```sh
hermes-plus serve --host 0.0.0.0 --port 9129 --auth password --password-file ~/.hermes-plus-password
```

For quick public access on a local machine:

```sh
cloudflared tunnel --url http://127.0.0.1:9129
```

Authentication modes for `--auth`:
- `token` (default): generates a one-time secret token printed to the terminal on startup.
- `password`: authenticates against a file specified by `--password-file` or the `HERMES_PLUS_PASSWORD` environment variable.
- `none`: permitted only when binding to loopback addresses (`127.0.0.1`, `localhost`, `::1`). Non-loopback binding with `auth=none` is rejected at startup.

When running behind Cloudflare Access or a reverse proxy terminating authentication, specify `--trust-proxy-headers` (or `HERMES_PLUS_TRUST_PROXY_HEADERS=1`). Stock Hermes remains bound strictly to loopback (`127.0.0.1`), ensuring external traffic passes exclusively through the sidecar.

### Compatibility gate

At startup and during `install`, the system checks the detected Hermes binary against `packages/hermes_plus/src/hermes_plus/compat/matrix.json`:

| Status | Condition | Runtime behavior |
|---|---|---|
| `supported` | Semver in `known_good` (`0.22.0`, `0.22.1`) or commit in `known_good_commits` (`379cf12`, `297552a`) | Full functionality enabled, fully verified by contract tests. |
| `untested` | Semver $\ge$ `0.22.0` or unpinned commit | Starts with an informational console banner. Features dynamically gate on runtime capability probes. |
| `unsupported` | Semver $<$ `0.22.0` | Server and install refuse to run. Bypassed only when `--force` or `HERMES_PLUS_FORCE=1` is provided. |

Compatibility matrix parameters:
- `adapter_api`: `1` (internal adapter interface version)
- `min`: `"0.22.0"`
- `known_good`: `["0.22.0", "0.22.1"]`
- `known_good_commits`: `["379cf12", "297552a"]`
- `known_good_refs`: `{"v0.22.1": "297552a"}`
- `tested_main`: `"379cf122"`
- `max_minor`: `"0.22"`

---

## 3. Architecture

### System topology

```
browser / client ──> hermes-plus sidecar :9129  (isolated process, own venv, own auth)
                        ├─ serves web UI (packages/ui build, injected runtime config)
                        ├─ HTTP routes: uploads, share, mobile, desktop-plugins, files, webapp, host-terminal
                        ├─ /api/ws JSON-RPC multiplexer:
                        │     plus namespaces (fork.*, bots_team.*, bots_mailbox.*, session.ask, …) -> handled in sidecar
                        │     upstream namespaces (session.*, prompt.*, profiles.*, …) -> forwarded to stock Hermes
                        ├─ plus.db (SQLite: schema migrations, approvals, bot teams, mailbox, mobile pairings, share grants)
                        └─ ADAPTER (packages/hermes_plus/src/hermes_plus/adapter/)
                              │ loopback WS (/api/ws) + CLI subprocess + filesystem access
                              ▼
                     stock `hermes dashboard` (child process on 127.0.0.1, token-authenticated)
                              │ loads directory plugin
                              ▼
                     $HERMES_HOME/plugins/hermes-plus/  (bridge plugin)
                        ├─ stdlib-only Python, uses public ctx API only
                        ├─ tools (visualization, git_ship, open_pr, update_task, bot_team, bot_mailbox)
                        ├─ hooks (pre_tool_call retry loop detector, post_tool_call, approval events)
                        ├─ prompt sections (team prompt section with bounded budget)
                        └─ spool (events written to $HERMES_HOME/hermes-plus/spool/ for sidecar ingest)
```

### Component breakdown

#### Adapter (`hermes_plus.adapter`)
The adapter is the only module in the entire codebase permitted to touch Hermes internals, invoke Hermes CLI binaries, or manage Hermes processes. This isolation is enforced by the lint test `tests/unit/test_import_boundary.py`. The adapter provides `detect()` to discover the Hermes binary path, interpreter shebang, version string, and git commit. It exposes `DashboardProcess` to supervise stock `hermes dashboard` child processes on loopback (`127.0.0.1`) with session token generation and port readiness checks. It implements `GatewayClient` for asynchronous JSON-RPC communication over `/api/ws`, `check_compat()` for matrix evaluation, `probe_capabilities()` for dynamic feature detection, `run_hermes()` for CLI invocations, and filesystem helpers for profile memory files (`MEMORY.md`, `USER.md`).

#### Sidecar (`hermes_plus.server`, `store`, `rpc`, `http`)
The sidecar is a Starlette ASGI application running under Uvicorn in its own isolated Python environment. It manages its own SQLite database located at `<HERMES_HOME>/hermes-plus/plus.db` using forward-only schema migrations (`0001_init.sql` through `0007_bridge_records.sql`), never writing to stock Hermes's `state.db`. The sidecar implements authentication (token, password, reverse proxy headers), serves custom HTTP routes (file uploads with 16 MiB limits, credential-free share viewer links, mobile pairing state, ranged and ticketed file downloads, and a PTY host terminal via `ptyprocess`), and hosts the `/api/ws` JSON-RPC multiplexer. The multiplexer routes desktop-specific namespaces to internal RPC handlers and forwards all standard Hermes methods and event streams to the stock child process.

#### Bridge plugin (`hermes_plus.bridge`)
The bridge is a directory plugin installed at `<HERMES_HOME>/plugins/hermes-plus/`. Written strictly with Python standard library modules, it avoids any dependency on `hermes_plus` packages or private Hermes APIs, interacting exclusively with the public `PluginContext` contract. It provides model tools (`create_visualization`, `git_ship`, `open_pr`, `update_task`, and agent-facing wrappers `bot_team` and `bot_mailbox`). It installs a `pre_tool_call` hook implementing a retry loop detector policy that rejects identical calls repeating more than 6 times in a turn. It renders dynamic prompt sections (such as team membership) with a strict execution budget. Events and tool records are spooled to `<HERMES_HOME>/hermes-plus/spool/`, which the sidecar runtime periodically ingests into `plus.db`.

#### User interface (`packages/ui`)
The frontend is a React application synced from `feat/hermes-next` (`apps/desktop`). It is built to static assets (`dist-webapp`) and packaged directly into the `hermes-plus` Python wheel under `ui_dist/`, eliminating any Node.js runtime requirement for end users. The sidecar injects runtime configuration into `index.html` at request time via `window.__HERMES_PLUS__ = { apiBase, wsBase, basePath }` and standard Hermes runtime globals (`__HERMES_BASE_PATH__`, `__HERMES_SESSION_TOKEN__`, `__HERMES_AUTH_REQUIRED__`, `__HERMES_UI_SURFACE__`). The UI communicates with the host exclusively through the `HostBridge` interface (`window.hermesDesktop`), allowing identical application code to function in a standard browser tab or desktop container.

---

## 4. Feature parity on stock Hermes

All 33 features from the fork inventory (`docs/PARITY.md`) are supported on stock Hermes through four mechanisms:
- **P**: Proxied to stock Hermes upstream
- **S**: Implemented in sidecar (own backend and `plus.db`)
- **B**: Implemented in bridge directory plugin (public `ctx` API)
- **U**: Implemented in UI frontend only

| # | Feature | Mechanism | Shipped implementation module(s) | Status |
|---|---|---|---|---|
| 1 | Chat, streaming, sessions list/resume/branch/interrupt | P | `packages/ui/src/api/sessions.ts`, server multiplexer | done |
| 2 | Tool cards (tool.start/complete/generating, diffs, todos) | P + U | `packages/ui/src/app/session/`, multiplexer event tap | done |
| 3 | Tool retry display metadata | B + S | `bridge/hooks.py`, `bridge/spool.py`, `store/bridge_records.py` | done |
| 4 | Approvals journal, `fork.approvals.list/decide` | S + B | `rpc/handlers/fork_approvals.py`, `bridge/hooks.py`, `store/approvals.py` | done |
| 5 | Sessions sidebar: ask dialog `session.ask` | S | `rpc/handlers/session_side.py`, `adapter/` GatewayClient (`llm.oneshot`) | done |
| 6 | `session.predict_next` | S | `rpc/handlers/session_side.py`, `adapter/` GatewayClient (`llm.oneshot`) | done |
| 7 | Delegation reports `delegation.reports` | B + S | `bridge/hooks.py`, `bridge/spool.py`, `store/bridge_records.py` | done |
| 8 | Group chat / bot room, `bots_team.*` (21 methods) | S | `rpc/handlers/bots_team.py`, `store/teams.py`, `store/team_rules.py` | done |
| 9 | Bot mailbox `bots_mailbox.*` | S | `rpc/handlers/bots_mailbox.py`, `store/mailbox.py` | done |
| 10 | Bot identity / memory `fork.bot.identity.*`, `fork.memory.*` | S | `rpc/handlers/fork_bot_identity.py`, `fork_memory.py`, `adapter/memory.py` | done |
| 11 | Bot relay extensions (`bot_relay.outbox.pending`, roster.sync, deliver/reply) | S + P | `adapter/capabilities.py`, `rpc/registry.py`, multiplexer event broadcasting | done |
| 12 | Bot mode DM/probe, hosted rooms | S + B | `adapter/live_delivery.py`, `bridge/tools.py` (`update_task`, `bot_mailbox`) | done |
| 13 | Team mode (team goals -> spawn task, budgets, audit, packs) | S | `adapter/kanban.py`, `rpc/handlers/bots_team.py`, `store/teams.py` | done |
| 14 | Routines `fork.routines.*` | S | `adapter/cron.py`, `rpc/handlers/fork_routines.py`, `store/routines.py` | done |
| 15 | Onboarding `fork.onboarding.*` (setup profile/session) | S | `adapter/profiles.py`, `rpc/handlers/fork_onboarding.py`, `store/setup_profile.py` | done |
| 16 | Policies, first-task and initiate-setup prompts | B | `bridge/policies.py`, `bridge/prompts.py`, `extras/skills/` | done |
| 17 | Browser snapshot/thumbnail `fork.browser.*` | B + S | `bridge/hooks.py`, `bridge/spool.py`, `store/bridge_records.py`, `http/files.py` | done |
| 18 | Computer-use timeline `fork.computer.timeline` | B + S | `bridge/hooks.py`, `server/spool.py`, `rpc/handlers/fork_computer.py` | done |
| 19 | MCP app tool cards `mcp_apps.list/call/read_ui` | S | `adapter/mcp.py`, `rpc/handlers/mcp_apps.py` (official MCP SDK) | done |
| 20 | Plugin rail `/api/desktop-plugins/*` | S | `http/desktop_plugins.py` | done |
| 21 | Web host terminal `WS /api/host-terminal` | S | `http/host_terminal.py` (`ptyprocess` PTY management) | done |
| 22 | File upload `POST /api/chat/file-upload` + prompt attachments | S | `http/uploads.py`, `server/multiplexer.py` (`staged_upload` translation) | done |
| 23 | Files pane with ranged/ticketed downloads | S + P | `http/files.py`, `server/auth.py` (ticket redemption and Range support) | done |
| 24 | Share links `/api/share/*`, `/share/view` | S | `http/share.py`, `store/share.py` (public viewer with filtered projection) | done |
| 25 | Mobile companion `/api/mobile/*`, `/mobile` | S | `http/mobile.py`, `store/mobile.py` (pairing state in `plus.db`) | done |
| 26 | Webapp windows `/webapp/*`, window tickets | S | `http/webapp.py` (window tickets and loopback UID checks) | done |
| 27 | Profiles switcher, profile lifecycle | P | `tui_gateway/methods_profiles.py` via multiplexer and direct dispatch | done |
| 28 | Git panel (web_git extras) | P + S | `http/git.py` (`/api/git/worktree/ensure`, `/api/git/worktree/merge`) | done |
| 29 | Extra plugins: backdrops, memory (claude-mem, holographic), langfuse | P + B | `extras/plugins/` (copied to `$HERMES_HOME/plugins/`) | done |
| 30 | Extra skills (bot-team-builder, first-task, initiate-setup, canvas, ambition) | U + B | `extras/skills/` (copied to `$HERMES_HOME/skills/` with hash ledger) | done |
| 31 | Extra tools (visualization, git_ship, open_pr, update_task, bot_team, bot_mailbox) | B + S | `bridge/tools.py` (stdlib-only implementation) | done |
| 32 | pane3d, agentcraft, kanban, radio, starmap, mission-control, hello-runtime, i18n, themes | U | `packages/ui/src/` components | done |
| 33 | Auth for public serving (token/password, Cloudflare Access) | S | `server/auth.py`, `settings.py` | done |

### Coverage audit verification

The automated coverage script `scripts/check-ui-coverage.py` was executed against stock Hermes (`~/Code/ship-test/upstream`) and Hermes Plus:
- Stock Hermes serves 151 RPC methods (including `profiles.*` via `@_profile_handler` and `projects.*` via `@_projects_handler`).
- Hermes Plus serves 50 RPC methods and 31 HTTP routes.
- UI calls 17 RPC methods and 103 HTTP routes.
- Zero unserved RPC methods remain.
- Zero unserved HTTP routes remain. All active endpoints are served; the 18 allowlisted items represent Electron-only IPC mechanisms (`wake.feed`, `wake.pause`) or future specifications.

---

## 5. What happens on upstream updates

### Capability detection and degradation
At startup, `hermes-plus serve` initiates an internal `GatewayClient` connection to the child dashboard and invokes `probe_capabilities()`. It scans available methods against `KNOWN_METHODS` and records available event types. If upstream removes or alters an optional method:
- The feature is disabled gracefully without server crashes.
- Any direct RPC invocation returns a structured error:
  `{"code": -32004, "message": "feature unavailable on this Hermes version", "data": {"capability": "<method>"}}`.
- The UI responds by hiding or disabling the corresponding action or panel.

### Compatibility matrix
Compatibility definitions in `matrix.json` govern startup rules:
- Known-good releases: `0.22.0`, `0.22.1`.
- Known-good commit SHAs: `379cf12`, `297552a`.
- Tested main branch SHA: `379cf122`.
- When upstream updates to a new patch release or commit within `max_minor: "0.22"`, the server runs in `untested` mode, logging an informational startup notice.
- If upstream releases a breaking change below `min: "0.22.0"`, the server halts with exit code 3, displaying remediation steps. Passing `--force` overrides this check.

### Automated continuous integration
Two GitHub Actions workflows maintain upstream compatibility:
1. `ci.yml`: Runs on every push and pull request. Validates Python unit and integration suites under Python 3.11 and 3.12, executes UI typechecks and tests, builds the webapp distribution, verifies wheel packaging, and executes contract tests against stock Hermes at pinned tag `v0.22.1` in `$RUNNER_TEMP/hroot`. The job enforces `HERMES_PLUS_TEST_EXPECT_COMPAT: supported`.
2. `upstream-nightly.yml`: Executes on a daily schedule and on manual trigger. It resolves `main` and the two most recent release tags from NousResearch/hermes-agent via `git ls-remote --tags`. It provisions clean stock environments, runs contract suites, and executes the end-to-end smoke test (`install.sh`, server startup on port 9230, and healthcheck query to `/healthz`).
   - If upstream changes break compatibility, it opens an issue titled `Upstream compat broken: <ref>`.
   - If a new tag passes all tests cleanly, it automatically opens a pull request bumping `known_good` and `known_good_commits` in `matrix.json`.

### Pinned tag verification
Release `v0.22.1` was proven in task S12 using a clean stock installation at commit `297552a`. The entire contract suite passed with zero skips (excluding model-dependent provider tests). Upstream `main` was verified at commit `379cf122`.

### Upgrades with `hermes-plus update`
Upstream updates and add-on updates remain completely decoupled:
- Users run `hermes update` to upgrade Hermes Agent; this modifies Hermes's runtime and virtual environment without altering the sidecar.
- Users run `hermes-plus update` to upgrade the add-on:
  1. Upgrades the `hermes-plus` package inside its tool environment (using `uv tool upgrade`, `pipx upgrade`, or pip).
  2. Atomically refreshes the bridge plugin at `<HERMES_HOME>/plugins/hermes-plus/`.
  3. Updates bundled extras and skills, retaining user modifications via the hash ledger `<plus_home>/installed-extras.json`.
  4. Applies forward migrations to `<HERMES_HOME>/hermes-plus/plus.db`.
  5. Runs `hermes-plus doctor` to evaluate compatibility against the active Hermes installation.

---

## 6. Remaining core patches / upstream PRs

Thirteen pull requests have been prepared in `docs/upstream-prs.md` for upstream submission to NousResearch/hermes-agent. None of these PRs are blockers for feature parity; all 33 features function today via the sidecar and bridge.

### Prepared upstream PRs

1. `feat(plugins): let plugins register gateway JSON-RPC methods`
   Adds `register_gateway_method` to `PluginContext`. Would allow the bridge plugin to register custom RPCs directly on upstream gateway, reducing proxy forwarding.
2. `feat(plugins): serve dashboard routers from pip-installed plugins`
   Adds `hermes_agent.dashboard_routers` entry-point group in `web_server_dashboard.py` to allow wheel-installed plugins to serve dashboard sub-routes.
3. `feat(plugins): register public (credential-free) dashboard paths`
   Adds `register_public_path` to `PluginContext` to permit unauthenticated endpoints (like share views and pairing) on plugin routes.
4. `feat(plugins): fire an on_delegation_complete hook`
   Adds `on_delegation_complete` to `VALID_HOOKS`, allowing plugins to capture delegation results without parsing stdout text.
5. `fix(checkpoints): delimiter-safe listing and optional per-turn trailer context`
   Ensures delimiter-safe listing of saved conversation checkpoints.
6. `fix(docker): stable container reuse fingerprint when skills contain symlinks`
   Prevents container cache invalidation when skills contain symlinked files.
7. `fix(mcp): hide app-only tools from the model and negotiate MCP Apps extensions`
   Prevents application-only tools from entering the model prompt and handles client capabilities.
8. `fix(qqbot): stop blocking the event loop during media sends and narrow error handling`
   Moves media sends to worker threads in messaging integrations.
9. `fix(credential-files): report the original skills source for sanitized copies`
   Preserves provenance tracking for credential files across skill invocations.
10. `feat(cua): configurable cursor motion style for the cua-driver backend`
    Configures cursor motion interpolation for computer-use agents.
11. `feat(plugins): let plugins attach display metadata to tool events`
    Allows `pre_tool_call` and `post_tool_call` hooks to attach UI display badges to tool cards without polluting the LLM's conversation context.
12. `fix(session-lifecycle): guard the deferred active-session lease registries`
    Adds lock protection to `_deferred_active_session_leases` across thread timers.
13. `fix(installer): resolve --branch as a git ref on an existing checkout`
    Permits branch/tag resolution on existing checkouts in `scripts/install.sh`.

### Dropped fork patches

Several invasive patches from `feat/hermes-next` were deliberately discarded:
- `server.py` method registration: Dropped. The sidecar `/api/ws` multiplexer handles custom namespaces cleanly without modifying core registries.
- `dashboard_auth` public paths: Dropped. The sidecar acts as the front door and handles public path exemptions.
- `hermes_state` SessionDB mixin and `tool_retries` table: Dropped. State is stored in `plus.db` and managed via bridge hooks.
- `tools/bot_team.py` core edits: Dropped. Rules and persistence live in the sidecar; the bridge provides agent wrapper tools.
- `profiles.py` lifecycle patching: Dropped. Stock Hermes serves `profiles.list` and lifecycle methods via `@_profile_handler`; sidecar serializes mutations via adapter CLI.
- `prompt_attachments` upload tickets: Dropped. The sidecar translates `staged_upload` payloads into standard paths for stock `file.attach`.

---

## 7. Native shell evaluation

An Architectural Decision Record (`docs/adr-native-shell.md`) evaluates future desktop shell options:
- **Tauri v2 (Rust core + system webview)**: Recommended for Phase B.
- **Web app only**: Shipped in Phase A (current release).
- **Electron**: Fallback option only (rejected for Phase B due to 150 MB+ binary size, high memory consumption, and duplicate Chromium instances).
- **Full-native Rust (Dioxus, Slint, egui, gpui)**: Rejected. Rewriting ~200k lines of React (assistant-ui, three.js `pane3d`, agentcraft) would take months, would eliminate browser/web serving, and would create ongoing maintenance drift against upstream UI improvements.

### Key points of the Tauri v2 decision

1. Reuses 100% of the existing `packages/ui` React build with zero changes to frontend application logic.
2. The sidecar (`hermes-plus serve`) remains the backend in all configurations, running as a supervised loopback child process.
3. Host communication is abstracted behind a new `tauri` implementation of the existing `HostBridge` interface (`window.hermesDesktop`).
4. Small binary size (5 to 15 MB) and low RAM footprint.
5. Primary technical risk is Linux WebKitGTK WebGL compatibility for three.js `pane3d`, which will be evaluated in a Phase B1 spike.

### Phasing
- **Phase A (Current)**: Web add-on served by `hermes-plus serve` to any modern browser.
- **Phase B (Desktop)**: Tauri v2 desktop application:
  - B1: Tauri window loading sidecar URL with `tauri` HostBridge implementation and WebKitGTK validation.
  - B2: Bundled loopback sidecar supervision (process launch, dynamic port allocation, health monitoring, clean shutdown).
  - B3: System tray, deep linking (`hermes-plus://`), single-instance lock.
  - B4: Auto-updater and signed OS binaries.
- **Phase C (Mobile)**: Tauri v2 mobile application or PWA connecting to a remote sidecar over a secure tunnel.

---

## 8. Clean-install proof

PENDING: final proof results

*Note: This section will be populated by the engineering manager with the final verification run from task S10/S16.*

### Verification items to be inserted
- Clean installation transcript on a virgin machine with stock Hermes Agent.
- Full pytest suite summary lines across unit, integration, and contract test suites.
- Visual screenshot verification artifacts from `docs/screenshots/`:
  - `chat-tool-cards.png` (chat turn displaying tool execution cards, diffs, and todo items)
  - `sessions-sidebar.png` (sessions sidebar including ask dialog)
  - `group-chat.png` (bot room with mailbox and roster)
  - `team-mode.png` (team goal hierarchy, task breakdown, budget, and audit log)
  - `plugin-rail.png` (desktop plugin rail)
  - `mobile-companion.png` (mobile companion view)

---

## 9. Publish steps for owner

Follow this checklist to publish Hermes Desktop Plus:

1. **Review report**: Review this document (`SHIP-DONE.md`) and verify the test execution and screenshot proofs.
2. **Create repository**: Create the GitHub repository (e.g., `NousResearch/hermes-desktop-plus` or personal organization), set initially to private.
3. **Configure environments**:
   - In GitHub repository settings, create the environment named `release`.
   - Configure PyPI trusted publishing and GHCR write permissions for this environment as needed by `.github/workflows/release.yml`.
4. **Push commits**:
   - Verify local git status: no branches or commits have been pushed to any remote during development.
   - Push `master` branch to the new remote:
     ```sh
     git push origin master
     ```
5. **Update URLs**:
   - In `README.md` and `install.sh`, replace `<INSTALL_URL>` with the published release URL (e.g., `https://raw.githubusercontent.com/<owner>/hermes-desktop-plus/v0.1.0/install.sh`).
   - If using a custom container registry owner, verify `ghcr.io/<owner>/hermes-desktop-plus` references.
   - Commit and push URL updates to `master`.
6. **Tag release**:
   - Tag the initial release version:
     ```sh
     git tag -a v0.1.0 -m "Hermes Desktop Plus v0.1.0"
     git push origin v0.1.0
     ```
7. **Verify release automation**:
   - Confirm `.github/workflows/ci.yml` runs and succeeds on `master`.
   - Confirm `.github/workflows/release.yml` triggers on `v0.1.0`, builds the webapp assets, packages the Python wheel, pushes container images to GHCR, and publishes to PyPI.
8. **Make repository public**: Change repository visibility to public once release assets are verified.

---

## 10. Known limitations

The following limitations and runtime constraints were established during implementation and testing:

1. **Model-dependent contract tests**:
   Tests `test_session_ask_answers_from_a_real_transcript` and `test_predict_next_returns_a_short_plain_message` require a real model provider configuration. They are automatically skipped in environments where `HERMES_PLUS_TEST_MODEL_CONFIG` does not point to a valid `config.yaml` containing provider credentials (S1b, S8a, S12).
2. **Direct stdio RPC execution (`rpc/__main__.py`)**:
   Running the JSON-RPC interface directly over stdio (`python -m hermes_plus.rpc --plus-home DIR`) initializes `RpcContext` without a live upstream gateway connection (`ctx.internal = None`). Methods requiring upstream forwarding (`session.ask`, `session.predict_next`, and `fork.onboarding.ensure_setup_session`) return `-32004 (FEATURE_UNAVAILABLE)` over stdio. They function fully when accessed through the `/api/ws` WebSocket multiplexer in `hermes-plus serve` (S2, S8a).
3. **Session history requirement for side-queries**:
   `session.ask` and `session.predict_next` derive query context by calling stock `session.history` and `llm.oneshot`. Stock Hermes only maintains `session.history` for active, live sessions; queries against non-live session IDs return stock error code 4001 (S8a).
4. **Stock runtime-only installation layout**:
   Upstream's `setup-hermes.sh --runtime-only` installer terminates before publishing user-facing launchers to `$HOME/.local/bin/hermes` and does not include prebuilt `web_dist/index.html`. The adapter handles this by providing a temporary stub dist for `/api/health` so the dashboard child starts cleanly without triggering upstream asset rebuilds (S1b, S12).
5. **Fresh `HERMES_HOME` cold-start latency**:
   When initialized with a new or cold `HERMES_HOME`, stock Hermes prepares its isolated Python environment lazily, requiring 60 to 110 seconds before command responses are returned. The adapter accounts for this with extended timeouts (300 seconds for CLI operations, 120 seconds for dashboard startup) (S12).
6. **Host terminal reattachment lifetime**:
   PTY sessions created via `WS /api/host-terminal` (`ptyprocess`) are held in sidecar memory for up to 15 minutes to allow browser client reattachment. They do not persist across sidecar process restarts (S6).
7. **Skipped desktop-only layout tools**:
   The tools `apply_layout` and `verify_preview` require native Electron platform callbacks and cannot function inside a browser or headless bridge plugin; they are omitted from the bridge plugin toolset (S7).
8. **GitHub Actions workflow validation**:
   The local test environment does not have `actionlint` installed. Workflow files (`ci.yml`, `upstream-nightly.yml`, `release.yml`) were validated for structural syntax using `yaml.safe_load` and schema rule checks (S9).
