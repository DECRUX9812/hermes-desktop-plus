/**
 * Group Rooms — the fork's Room 2.0 architecture as a bundled SDK plugin: a
 * thread rail (rooms + threads + live seat status) with a room composer,
 * reachable from the sidebar as its own page. Pure SDK-consumer work: every
 * read and write goes through the `plugin.group_rooms.*` JSON-RPC methods the
 * plugin's Python side registers itself at register(ctx) time, and the whole
 * surface is gated on the plugin's own `thread_rail_enabled` config knob read
 * through the same RPC — no core UI or backend file is touched.
 *
 * Opt-in: a nav row under Browse and a `/group-rooms` route, nothing more. It
 * never auto-opens, and Capabilities ▸ Plugins can still switch it off.
 */

import {
  type HermesPlugin,
  type RouteContribution,
  ROUTES_AREA,
  type SidebarNavContribution,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'

import { GroupRoomsRail } from './rail'

const plugin: HermesPlugin = {
  id: 'group-rooms',
  name: 'Group Rooms',
  description: 'Rooms with bot seats, threads and a shared composer — the Room 2.0 thread rail.',
  defaultEnabled: false,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/group-rooms' } satisfies RouteContribution,
        render: () => <GroupRoomsRail />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { codicon: 'comment-discussion', label: 'Group Rooms', path: '/group-rooms' } satisfies SidebarNavContribution
      }
    ])
  }
}

export default plugin
