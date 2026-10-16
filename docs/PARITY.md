# Hermes Desktop Plus Feature Parity Audit

This table tracks feature parity between stock Hermes, the Hermes Plus sidecar, and the bridge plugin. It maps each feature from `feat/hermes-next` (the fork) to its implementation mechanism on stock Hermes.

**Mechanism key:**
- **P** = Proxied to stock upstream
- **S** = Sidecar (our backend, our DB)
- **B** = Bridge directory plugin (public ctx API)
- **U** = UI only

**Proof step key** (the clean-install proof in `proof/`; every row below is asserted by at least one
of these, on a real install, with no mocks):

- `run-proof: <step>` — a step of `proof/run-proof.sh` (install, doctor, serve, teardown, …)
- `browser: <check>` — a check in `proof/playwright/proof.mjs` (the packaged UI in headless Chromium)
- `parity: <check>` — a check in `proof/parity-proof.py` (our RPC namespaces, our HTTP routes, the
  host-terminal WS, and the stock CLI cross-checks), run by `run-proof.sh` against the same sidecar

| # | Feature | Mechanism | Module(s) | Test(s) | Proof step | Status |
|---|---------|-----------|-----------|---------|-----------|--------|
| 1 | Chat, streaming, sessions list/resume/branch/interrupt | P | `packages/ui/src/api/sessions.ts` | `packages/ui/src/api/sessions.test.ts` | `browser: chat reply streams from the configured model`, `start a second session from the UI`, `both sessions are listed after a reload`, `the first session still holds its messages after a reload` | done |
| 2 | Tool cards (tool.start/complete/generating, diffs, todos) | P + U | `packages/ui/src/app/session/` | UI integration tests | `browser: tool card rendered` | done |
| 3 | Tool retry display metadata | B + S | `packages/hermes_plus/src/hermes_plus/bridge/`, `store/` | contract tests | `parity: the relay outbox drain and roster sync answer` (+ the pending-event assertion in `bots_mailbox send/list/update round-trips`) | done |
| 4 | Approvals journal, `fork.approvals.list/decide` | S + B | `packages/hermes_plus/src/hermes_plus/rpc/handlers/fork_approvals.py` | contract tests | `parity: fork.approvals.list answers`, `fork.approvals.decide answers` | done |
| 5 | Sessions sidebar: ask dialog `session.ask` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/session_side.py` | contract tests | `parity: session.ask answers about the session` | done |
| 6 | `session.predict_next` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/session_side.py` | contract tests | `parity: session.predict_next answers with {text}` (the runner turns the auxiliary task on in the temp home) | done |
| 7 | Delegation reports `delegation.reports` | B + S | `packages/hermes_plus/src/hermes_plus/bridge/` | contract tests | `parity: delegation.reports answers` | in progress |
| 8 | Group chat / bot room, `bots_team.*` (21 methods) | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/bots_team.py` | contract tests | `browser: create a bot team over the app transport`, `the team appears on the Team page`, `the team survives a reload`; `parity: the team-mode goal/budget/audit/pack verbs answer` | done |
| 9 | Bot mailbox `bots_mailbox.*` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/bots_mailbox.py` | contract tests | `parity: bots_mailbox send/list/update round-trips (and emits the relay event)` | done |
| 10 | Bot identity / memory `fork.bot.identity.*`, `fork.memory.*` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/fork_bot_identity.py`, `fork_memory.py` | contract tests | `browser: bot identity round-trips and survives a reload`; `parity: fork.memory.list answers`, `fork.memory add/update/list/remove round-trips` | done |
| 11 | Bot relay extensions (`bot_relay.outbox.pending`, roster.sync, deliver/reply changes) | S + P | `packages/hermes_plus/src/hermes_plus/rpc/handlers/` | contract tests | `parity: bots_mailbox … (and emits the relay event)`, `the relay outbox drain and roster sync answer` | in flight on ship/s8 |
| 12 | Bot mode DM/probe, hosted rooms | S + B | `packages/hermes_plus/src/hermes_plus/rpc/handlers/`, `bridge/` | contract tests | `browser: the Bot Room overlay opens from the roster toolbar and paints the team bot`, `the Bot Room demo path still paints its canned roster`, `bot pane opens`, `the roster toolbar floats the bot mascots and their task pill` (the browser host's in-page layer: a real roster slot with a live canvas, and the 320x132 task pill its action dot opens) | in flight on ship/s8 |
| 13 | Team mode (team goals → spawn task, budgets, audit, packs) | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/` | contract tests | `parity: the team-mode goal/budget/audit/pack verbs answer` (goal → spawned kanban task, budget check/record, audit trail, pack export) | in flight on ship/s8 |
| 14 | Routines `fork.routines.*` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/fork_routines.py` | contract tests | `parity: fork.routines.create/list/remove`, `stock Hermes cron holds the routine as a job`, `stock Hermes cron dropped the routine`, `fork.routines update/pause/resume/run_now answer` | done |
| 15 | Onboarding `fork.onboarding.*` (setup profile/session) | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/fork_onboarding.py` | contract tests | `parity: fork.onboarding.state answers`, `fork.onboarding.ensure_setup_profile creates a real profile`, `the onboarding flow answers (mark_seen / failed start / reset / setup session)` | done |
| 16 | Policies, first-task and initiate-setup prompts | B | `packages/hermes_plus/src/hermes_plus/bridge/` | contract tests | `browser: the bundled extras skills are listed on the Skills tab`; `parity: extras skills are listed by /api/skills`. The repeat-tool-loop tripwire itself is **not** exercised (it needs a model stuck repeating one tool call); the skills half is. | in flight on ship/s7 |
| 17 | Browser snapshot/thumbnail `fork.browser.*` | B + S | `packages/hermes_plus/src/hermes_plus/bridge/`, `store/` | contract tests | `parity: fork.browser.thumbnail/snapshot answer` | in flight on ship/s7 |
| 18 | Computer-use timeline `fork.computer.timeline` | B + S | `packages/hermes_plus/src/hermes_plus/bridge/`, `store/` | contract tests | `parity: fork.computer.timeline answers` | in flight on ship/s7 |
| 19 | MCP app tool cards `mcp_apps.list/call/read_ui` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/mcp_apps.py` | contract tests | `parity: mcp_apps.list answers`, `mcp_apps.call and mcp_apps.read_ui answer` | done |
| 20 | Plugin rail `/api/desktop-plugins/*` | S | `packages/hermes_plus/src/hermes_plus/http/desktop_plugins.py` | contract tests | `run-proof: desktop plugin installed through the disk door`; `parity: the desktop-plugins routes answer`; `browser: the plugin installed through the disk door (hello-runtime) is live` | done |
| 21 | Web host terminal `WS /api/host-terminal` | S | `packages/hermes_plus/src/hermes_plus/http/host_terminal.py` | contract tests | `parity: the host terminal runs a command and streams its output` | done |
| 22 | File upload `POST /api/chat/file-upload` + prompt attachments | S | `packages/hermes_plus/src/hermes_plus/http/uploads.py` | contract tests | `browser: attaching a file uploads it and paints an attachment in the composer`, `a prompt carrying the attachment gets a reply that quotes it`; `parity: POST /api/chat/file-upload accepts a file` | done |
| 23 | Files pane with ranged/ticketed downloads | S + P | `packages/hermes_plus/src/hermes_plus/http/files.py` | contract tests | `parity: a ticketed download honours a Range header (206 + bytes)`, `a file ticket admits only its own path` | done |
| 24 | Share links `/api/share/*`, `/share/view` | S | `packages/hermes_plus/src/hermes_plus/http/share.py` | contract tests | `browser: a share link can be created for a session`, `an anonymous reader sees the filtered projection at /share/view`; `parity: share create/list/revoke round-trips` | done |
| 25 | Mobile companion `/api/mobile/*`, `/mobile` | S | `packages/hermes_plus/src/hermes_plus/http/mobile.py` | contract tests | `browser: the mobile companion page loads without a cookie`, `the mobile pairing endpoint answers on the session`; `parity: POST /api/mobile/pairing mints a pairing code`, `GET /api/mobile/overview answers with the session list`, `the pairing code exchanges for a session token`, `the mobile reply and respond routes answer` (credential required, empty body refused, over-long reply refused, and a reply really lands in the named session's transcript) | done |
| 26 | Webapp windows `/webapp/*`, window tickets | S | `packages/hermes_plus/src/hermes_plus/http/webapp.py` | contract tests | `parity: a webapp window ticket exchanges for a window session`, `the webapp launcher and child-window pages render` | done |
| 27 | Profiles switcher, profile lifecycle | P | `packages/ui/src/api/profiles.ts` | contract tests | `browser: the profiles switcher lists and switches the served profiles` (selecting a profile must re-scope live requests to it); `parity: profiles.list answers with the profile rows` | done |
| 28 | Git panel (web_git extras) | P + S | `packages/hermes_plus/src/hermes_plus/server/` | contract tests | `parity: POST /api/git/worktree/ensure restores a session worktree`, `POST /api/git/worktree/merge answers` | in flight on ship/s6 |
| 29 | Extra plugins: backdrops, memory (claude-mem, holographic), langfuse | P + B | `packages/hermes_plus/src/hermes_plus/bridge/`, extras | contract tests | `run-proof: hermes-plus install --extras`, `extras plugins installed and enabled` (stock's own `hermes plugins list`) | in flight on ship/s7 |
| 30 | Extra skills (bot-team-builder/retro, first-task, initiate-setup, excalidraw canvas) | U + B | `packages/hermes_plus/src/hermes_plus/bridge/`, extras | tests | `browser: the bundled extras skills are listed on the Skills tab`, `typing /ambition offers the skill and inserts a skill chip`; `parity: extras skills are listed by /api/skills` | in flight on ship/s7 |
| 31 | Extra tools (apply_layout, visualization, verify_preview, git_ship, browser_step_thumbnail, mcp_apps) | B + S | `packages/hermes_plus/src/hermes_plus/bridge/`, handlers | tests | `parity: the bridge's extra tools are on stock's registry` — asserts the 6 ported tools (`git_ship`, `open_pr`, `create_visualization`, `bot_team`, `bot_mailbox`, `update_task`) and that the documented non-ports (`apply_layout`, `verify_preview`, `mcp_apps`) stay unregistered; `mcp_apps` is served as the row-19 RPC and `browser_step_thumbnail` as the row-17 `fork.browser.thumbnail` | in flight on ship/s8 |
| 32 | pane3d, agentcraft, kanban, radio, starmap, mission-control, hello-runtime, i18n, themes | U | `packages/ui/src/` | UI tests | `browser: the bundled UI plugins are installed on the Plugins tab`, `the bundled plugin pages and overlays render` (kanban board, AgentCraft studio, starmap overlay, mission-control overview), `the command palette opens the 3D Pane in a browser tab` (the `Open 3D Pane` row opens `?win=pane3d`, and the launch demo paints a real avatar), `the appearance settings expose the language switcher and the theme controls`, `the plugin installed through the disk door (hello-runtime) is live` | done |
| 33 | Auth for public serving (token/password, Cloudflare Access) | S | `packages/hermes_plus/src/hermes_plus/server/auth.py` | contract tests | `browser: auth gate`, `token URL opens the app with a session cookie` | done |

## Unserved RPC Methods (from UI coverage check)

All RPC methods called by the UI are served by stock or plus. No unserved RPC methods remain.

## Unserved HTTP Routes (from UI coverage check)

All HTTP paths called by the UI are served by stock or plus. No unserved HTTP routes remain.

## Notes

- S6 (http routes), S7 (bridge plugin), S8b (bot teams), and S13 (coverage audit) are merged
- S14 round 2: fixed stock-side extraction to detect `@_profile_handler`, `@_projects_handler` wrappers; removed duplicate plus profiles.list handler
- Table reflects completion state as of S14 round 2: all UI-called RPC and HTTP endpoints are served
- Coverage script now correctly detects Starlette Route() definitions and all wrapper decorators (@_*_handler patterns)
- Zero unserved RPC methods and zero unserved HTTP routes from UI coverage check
- **S39**: every row above now names the proof step that asserts it. Before S39 the clean-install
  proof covered about a dozen surfaces; it now runs 32 browser checks and 41 backend checks, plus the
  install/doctor/serve/teardown steps. One deliberate limit is stated above rather than papered over:
  row 16's repeat-loop tripwire (needs a model stuck in a loop). Row 32's pane3d was the other
  limit — an Electron-only native window — and S34 closed it: the browser host carries its own door
  for the pane (a popup `?win=pane3d` tab) and for the per-bot mascots (an in-page layer), and the
  S34 steps assert both. They sit at the marked insertion point in `proof/playwright/proof.mjs`.
  Landing S34 on this branch also fixed one regression the merge exposed: the Bot Room store had
  become a shared chunk (the in-page mascot layer imports it), so its module body could run before
  the browser host bridge existed and its self-wire silently never subscribed — the overlay opened
  to an empty stage. `wireBotRoomControl()` is now called from `main.tsx`'s body, where the bridge is
  guaranteed to be installed; `the Bot Room overlay opens from the roster toolbar and paints the team
  bot` is the check that caught it.
