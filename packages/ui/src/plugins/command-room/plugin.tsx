/**
 * Command Room — the fork's CEO surface as a bundled SDK plugin: the board
 * (needs you · waiting · working · answered), the escalations list with
 * ack/snooze, consensus vote cards, the 24h brief + catch-up panel, and the
 * moved-threads indicator. Pure SDK-consumer work: every read and write goes
 * through the `plugin.command_room.*` JSON-RPC methods the plugin's Python side
 * registers itself at register(ctx) time (and through the group-rooms plugin's
 * `plugin.group_rooms.*` for the room list) — no core UI or backend file is
 * touched.
 *
 * On by default like the workboard: a nav row under Browse and a
 * `/command-room` route, nothing more. It never auto-opens, and Capabilities ▸
 * Plugins can still switch it off.
 */

import {
  type HermesPlugin,
  type RouteContribution,
  ROUTES_AREA,
  type SidebarNavContribution,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'

import { CommandRoom } from './room'
import { COMMAND_ROOM_LOCALES } from './i18n'

const plugin: HermesPlugin = {
  id: 'command-room',
  name: 'Command Room',
  description: 'Escalations, the CEO board, consensus votes, the 24h brief and seat budgets.',
  defaultEnabled: true,
  register(ctx) {
    ctx.i18n.register(COMMAND_ROOM_LOCALES)
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/command-room' } satisfies RouteContribution,
        render: () => <CommandRoom />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 56,
        data: { codicon: 'gauge', label: 'Command Room', path: '/command-room' } satisfies SidebarNavContribution
      }
    ])
  }
}

export default plugin
