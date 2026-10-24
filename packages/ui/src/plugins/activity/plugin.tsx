/**
 * Activity — the fork's Muse-style Activity pane as a bundled SDK plugin:
 * recent activity rows (sessions, command-room escalations, open group-rooms
 * handoffs, overdue cron) plus the Now/Next/Done bot rail. Pure SDK-consumer
 * work: every read goes through the `plugin.activity.*` JSON-RPC methods the
 * plugin's Python side registers itself at register(ctx) time — no core UI or
 * backend file is touched.
 *
 * Opt-in: a nav row under Browse and an `/activity` route (the fork's
 * `#/activity`), nothing more. It never auto-opens, and Capabilities ▸ Plugins
 * can still switch it off.
 */

import {
  type HermesPlugin,
  type RouteContribution,
  ROUTES_AREA,
  type SidebarNavContribution,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'

import { Activity } from './activity'

const plugin: HermesPlugin = {
  id: 'activity',
  name: 'Activity',
  description: 'Recent activity rows and the Now/Next/Done bot rail.',
  defaultEnabled: false,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/activity' } satisfies RouteContribution,
        render: () => <Activity />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 58,
        data: { codicon: 'pulse', label: 'Activity', path: '/activity' } satisfies SidebarNavContribution
      }
    ])
  }
}

export default plugin
