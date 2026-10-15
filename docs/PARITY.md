# Hermes Desktop Plus Feature Parity Audit

This table tracks feature parity between stock Hermes, the Hermes Plus sidecar, and the bridge plugin. It maps each feature from `feat/hermes-next` (the fork) to its implementation mechanism on stock Hermes.

**Mechanism key:**
- **P** = Proxied to stock upstream
- **S** = Sidecar (our backend, our DB)
- **B** = Bridge directory plugin (public ctx API)
- **U** = UI only

| # | Feature | Mechanism | Module(s) | Test(s) | Status |
|---|---------|-----------|-----------|---------|--------|
| 1 | Chat, streaming, sessions list/resume/branch/interrupt | P | `packages/ui/src/api/sessions.ts` | `packages/ui/src/api/sessions.test.ts` | done |
| 2 | Tool cards (tool.start/complete/generating, diffs, todos) | P + U | `packages/ui/src/app/session/` | UI integration tests | done |
| 3 | Tool retry display metadata | B + S | `packages/hermes_plus/src/hermes_plus/bridge/`, `store/` | contract tests | done |
| 4 | Approvals journal, `fork.approvals.list/decide` | S + B | `packages/hermes_plus/src/hermes_plus/rpc/handlers/fork_approvals.py` | contract tests | done |
| 5 | Sessions sidebar: ask dialog `session.ask` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/session_side.py` | contract tests | done |
| 6 | `session.predict_next` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/session_side.py` | contract tests | done |
| 7 | Delegation reports `delegation.reports` | B + S | `packages/hermes_plus/src/hermes_plus/bridge/` | contract tests | in progress |
| 8 | Group chat / bot room, `bots_team.*` (21 methods) | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/bots_team.py` | contract tests | done |
| 9 | Bot mailbox `bots_mailbox.*` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/bots_mailbox.py` | contract tests | done |
| 10 | Bot identity / memory `fork.bot.identity.*`, `fork.memory.*` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/fork_bot_identity.py`, `fork_memory.py` | contract tests | done |
| 11 | Bot relay extensions (`bot_relay.outbox.pending`, roster.sync, deliver/reply changes) | S + P | `packages/hermes_plus/src/hermes_plus/rpc/handlers/` | contract tests | in flight on ship/s8 |
| 12 | Bot mode DM/probe, hosted rooms | S + B | `packages/hermes_plus/src/hermes_plus/rpc/handlers/`, `bridge/` | contract tests | in flight on ship/s8 |
| 13 | Team mode (team goals → spawn task, budgets, audit, packs) | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/` | contract tests | in flight on ship/s8 |
| 14 | Routines `fork.routines.*` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/fork_routines.py` | contract tests | done |
| 15 | Onboarding `fork.onboarding.*` (setup profile/session) | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/fork_onboarding.py` | contract tests | done |
| 16 | Policies, first-task and initiate-setup prompts | B | `packages/hermes_plus/src/hermes_plus/bridge/` | contract tests | in flight on ship/s7 |
| 17 | Browser snapshot/thumbnail `fork.browser.*` | B + S | `packages/hermes_plus/src/hermes_plus/bridge/`, `store/` | contract tests | in flight on ship/s7 |
| 18 | Computer-use timeline `fork.computer.timeline` | B + S | `packages/hermes_plus/src/hermes_plus/bridge/`, `store/` | contract tests | in flight on ship/s7 |
| 19 | MCP app tool cards `mcp_apps.list/call/read_ui` | S | `packages/hermes_plus/src/hermes_plus/rpc/handlers/mcp_apps.py` | contract tests | done |
| 20 | Plugin rail `/api/desktop-plugins/*` | S | `packages/hermes_plus/src/hermes_plus/http/desktop_plugins.py` | contract tests | done |
| 21 | Web host terminal `WS /api/host-terminal` | S | `packages/hermes_plus/src/hermes_plus/http/host_terminal.py` | contract tests | done |
| 22 | File upload `POST /api/chat/file-upload` + prompt attachments | S | `packages/hermes_plus/src/hermes_plus/http/uploads.py` | contract tests | done |
| 23 | Files pane with ranged/ticketed downloads | S + P | `packages/hermes_plus/src/hermes_plus/http/files.py` | contract tests | done |
| 24 | Share links `/api/share/*`, `/share/view` | S | `packages/hermes_plus/src/hermes_plus/http/share.py` | contract tests | done |
| 25 | Mobile companion `/api/mobile/*`, `/mobile` | S | `packages/hermes_plus/src/hermes_plus/http/mobile.py` | contract tests | done |
| 26 | Webapp windows `/webapp/*`, window tickets | S | `packages/hermes_plus/src/hermes_plus/http/webapp.py` | contract tests | done |
| 27 | Profiles switcher, profile lifecycle | P + S | `packages/ui/src/api/profiles.ts`, `packages/hermes_plus/src/hermes_plus/rpc/handlers/profiles.py` | contract tests | done |
| 28 | Git panel (web_git extras) | P + S | `packages/hermes_plus/src/hermes_plus/server/` | contract tests | in flight on ship/s6 |
| 29 | Extra plugins: backdrops, memory (claude-mem, holographic), langfuse | P + B | `packages/hermes_plus/src/hermes_plus/bridge/`, extras | contract tests | in flight on ship/s7 |
| 30 | Extra skills (bot-team-builder/retro, first-task, initiate-setup, excalidraw canvas) | U + B | `packages/hermes_plus/src/hermes_plus/bridge/`, extras | tests | in flight on ship/s7 |
| 31 | Extra tools (apply_layout, visualization, verify_preview, git_ship, browser_step_thumbnail, mcp_apps) | B + S | `packages/hermes_plus/src/hermes_plus/bridge/`, handlers | tests | in flight on ship/s8 |
| 32 | pane3d, agentcraft, kanban, radio, starmap, mission-control, hello-runtime, i18n, themes | U | `packages/ui/src/` | UI tests | done |
| 33 | Auth for public serving (token/password, Cloudflare Access) | S | `packages/hermes_plus/src/hermes_plus/server/auth.py` | contract tests | done |

## Unserved RPC Methods (from UI coverage check)

These RPC methods are called by the UI but not yet served by stock or plus. All are Electron-only or pending future implementation:

- `profiles.configure` - Profile deep operations, desktop-specific UI
- `profiles.set_asset` - Profile asset management, desktop-specific
- `reload.mcp` - MCP configuration reload, UI-triggered
- `session.save` - Session persistence/snapshots, UI-only
- `session.status` - Session status queries, alternative patterns
- `skills.manage` - Skill management, delegated or future feature

## Unserved HTTP Routes (from UI coverage check)

These HTTP paths are called by the UI but not yet served by stock or plus. All are Electron-only or pending future implementation:

- `/api/auth/ws-ticket` - Electron authentication/websocket tickets
- `/api/config/defaults` - Configuration defaults, embedded or UI-derived
- `/api/config/schema` - Configuration schema, embedded or UI-derived
- `/api/config?include_defaults=false` - Configuration query variant
- `/api/config?preserve_language=true` - Configuration query variant
- `/api/logs` - System logs, Electron-only operational surface
- `/api/profiles/sessions/pull-requests` - Pull request integration, future feature
- `/api/sessions/owner-backfill` - Session owner backfill, internal operation
- `/api/skills/hub/install` - Skills hub installation, backend-driven
- `/api/skills/hub/official` - Skills hub official catalog, backend-driven
- `/api/skills/hub/sources` - Skills hub sources, backend-driven
- `/api/skills/hub/uninstall` - Skills hub uninstallation, backend-driven
- `/api/skills/hub/update` - Skills hub update, backend-driven

## Notes

- S6 (http routes), S7 (bridge plugin), S8b (bot teams), and S13 (coverage audit/profiles.list) are merged
- Table reflects completion state as of S14
- Remaining unserved entries (6 RPC, 13 HTTP) are all Electron-only or future features
- Coverage script now correctly detects Starlette Route() definitions and wrapper decorators
- All UI-called RPC and HTTP endpoints are either served or justified as non-web/future work
