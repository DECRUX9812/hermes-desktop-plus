/**
 * Home Feed — the fork's Hark-style home surface as a bundled SDK plugin: flat
 * action cards over what needs you (command-room escalations, open group-rooms
 * handoffs, overdue/due cron jobs) plus the "Recent tasks" one-job-per-screen
 * session cards. Pure SDK-consumer work: every read and write goes through the
 * `plugin.home_feed.*` JSON-RPC methods the plugin's Python side registers
 * itself at register(ctx) time — no core UI or backend file is touched.
 *
 * Opt-in: a nav row under Browse and a `/home-feed` route, nothing more. It
 * never auto-opens, and Capabilities ▸ Plugins can still switch it off.
 */

import {
  type HermesPlugin,
  type RouteContribution,
  ROUTES_AREA,
  type SidebarNavContribution,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'

import { HomeFeed } from './feed'

const plugin: HermesPlugin = {
  id: 'home-feed',
  name: 'Home Feed',
  description: 'Action cards over what needs you, and the Recent tasks row.',
  defaultEnabled: false,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/home-feed' } satisfies RouteContribution,
        render: () => <HomeFeed />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 57,
        data: { codicon: 'home', label: 'Home Feed', path: '/home-feed' } satisfies SidebarNavContribution
      }
    ])
  }
}

export default plugin
