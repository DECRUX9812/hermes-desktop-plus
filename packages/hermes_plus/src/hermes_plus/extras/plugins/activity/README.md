# activity (plugin)

The fork's Muse-style **Activity pane** as an add-on plugin: recent activity
rows over what every bot is doing and has done, plus the **Now / Next / Done
bot rail**. Ported from `hermes-ui-repair` `apps/desktop/src/app/activity/`
(activity-tab + the route `#/activity`) with the mission rail's Now/Next/Done
board sections (`plugins/hermes-bots/work-board.tsx`) as the rail.

The pane owns no state. Every source is read-only:

- **sessions** — the profile's `state.db`, opened through a `mode=ro` sqlite
  URI; never written, never locked longer than a read;
- **cron** — the profile's `cron/jobs.json`, the file the scheduler already owns;
- **command-room / group-rooms** — their public APIs (`escalation_list`,
  `list_rooms` + `room_state`) when those plugins are registered in this
  process. Without them the corresponding rows degrade to empty; the pane
  never errors over a missing sibling.

## RPC surface

Methods are registered by the plugin itself at `register(ctx)` time, under the
`plugin.activity.` namespace:

| method | params | result |
|---|---|---|
| `plugin.activity.rows` | `limit?: int` | `{ rows: ActivityRow[], total: int }` — newest first, capped at `max_rows` |
| `plugin.activity.rail` | — | `{ enabled: bool, now: ActivityRow[], next: ActivityRow[], done: ActivityRow[] }` |
| `plugin.activity.config` | — | the effective knobs (window_hours, max_rows, now_next_done_enabled, include_cron, include_command_room, include_group_rooms) |

`ActivityRow`:

```json
{
  "id": "session:<id> | cron:<id> | escalation:<room>:<id> | handoff:<room>:<id>",
  "kind": "session | cron | approval | handoff",
  "label": "…", "detail": "…",
  "status": "running | success",
  "session_id": "<id> | null",
  "last_active": 1760000000,
  "created_at": 1760000000
}
```

## Timestamp contract (read this before touching the UI)

**`last_active` is an integer UNIX SECONDS value.** Sources stamp in mixed
units, so the plugin normalizes at the boundary: a value past `1e12` is
milliseconds and is converted down to seconds; `0`, negative, `None` and
unparseable stamps are rejected and the row is omitted — never epoch 0.

The UI converts seconds to milliseconds **exactly once** (`x * 1000`) at the
relative-time formatting boundary. The fork's fix commit `3f8c0538` exists
because `activity-tab.tsx` fed a seconds value to `relativeTime` (which takes
ms), rendering every session as "~20,728 days ago". Two conversions in a chain
re-creates that bug; one `* 1000` at the formatter is the whole rule.

An exception by design: **Next rail entries** carry the *scheduled* cron time
in `last_active`, which may lie in the future (it is the next run).

## Settings

All knobs live in `plugin.yaml`'s `config_schema` and are read only through
`ctx.get_config` (config.yaml: `plugins.entries.activity.settings.<name>`):
`window_hours` (24), `max_rows` (20), `now_next_done_enabled` (true),
`now_window_minutes` (10), `include_cron` (true), `include_command_room`
(true), `include_group_rooms` (true), `debug` (false).

## Buckets

- **Now** — running sessions (active inside `now_window_minutes`), open
  command-room escalations, open group-rooms handoffs, overdue cron slots
  (past the scheduler's 15-minute dispatch grace).
- **Next** — upcoming cron runs inside `window_hours`, soonest first.
- **Done** — idle and ended sessions as receipts (the fork's Done list).

## UI

`packages/ui/src/plugins/activity/` ships the bundled SDK plugin: an
`/activity` route with a nav row, relative-time activity rows (row click opens
the session through the host's `openSession`), and the Now/Next/Done rail
gated by `now_next_done_enabled`.

## Tests

`packages/hermes_plus/tests/test_plugin_activity.py` (plugin side, end to end
over the registered RPC) and `packages/ui/src/plugins/activity/activity.test.tsx`
(UI side: seconds are formatted as hours ago, never thousands of days; the
rail buckets; row click → `openSession`).
