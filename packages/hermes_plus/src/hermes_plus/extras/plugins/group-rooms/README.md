# group-rooms

The fork's Room 2.0 / group-chat architecture, ported as an add-on plugin: rooms with
multiple bot **seats**, one composer, Slack/Discord-style **threads**, **routing**
(who answers a posted message) and **handoffs** (move an open thread between seats),
with live per-seat status (`idle → pending → working → done`).

Ported from `hermes-bots` group-chat-view / bot-relay (fork Room 2.0). No core Hermes
files are touched; state lives in this plugin's own sqlite file
(`<data dir>/group-rooms/rooms.db`), not in plus.db, so the model stays self-contained
and portable. Seat identities are bot names, compatible with the add-on's teams store
rosters (which can later map seats ↔ `bot_teams` members without schema changes).

## Knobs (plugin.yaml config_schema, read only via `ctx.get_config`)

| Knob | Default | Meaning |
|---|---|---|
| `max_seats` | 6 | Upper bound on bot seats per room |
| `default_room_mode` | `collab` | Routing for new rooms: `collab` / `round_robin` / `lead` |
| `thread_rail_enabled` | true | Whether the thread rail (threads API) is exposed |
| `max_message_chars` | 8000 | Max characters per posted message |
| `db_path` | "" | Explicit sqlite path; empty = under the host data dir |
| `debug` | false | Verbose logging |

## API (module level; the store is opened lazily after `register(ctx)`)

`create_room`, `add_seat`, `remove_seat`, `set_seat_status`, `seat`, `post_message`,
`start_thread`, `route_message`, `handoff`, `resolve_handoff`, `room_state`,
`list_rooms`, `close`.

Proof test: `tests/test_plugin_group_rooms.py` (run from `packages/hermes_plus` with
`TMPDIR=~/Code/repair-work/tmp hermes-lock -- uv run pytest tests/test_plugin_group_rooms.py -q`).
