/**
 * VS Code — the fork's Code pane as a bundled SDK plugin: a code-server /
 * openvscode-server embed beside the chat (sandboxed iframe), or a vscode://
 * deep-link fallback to the desktop's installed VS Code, opened on the focused
 * session's workspace folder. Pure SDK-consumer work: every decision goes
 * through the `plugin.vscode.*` JSON-RPC methods the plugin's Python side
 * registers itself at register(ctx) time — no core UI or backend file is
 * touched, no process is ever spawned.
 *
 * The pane contributes beside the workspace (the fork's open-side default,
 * collapsed until revealed), plus a `/vscode` route and nav row for hosts
 * without a free-floating pane spot.
 */

import {
  type HermesPlugin,
  type PaneContribution,
  PANES_AREA,
  type RouteContribution,
  ROUTES_AREA,
  type SidebarNavContribution,
  SIDEBAR_NAV_AREA
} from '@hermes/plugin-sdk'

import { VscodePane } from './vscode'

const plugin: HermesPlugin = {
  id: 'vscode',
  name: 'VS Code',
  description:
    'The Code pane: a code-server embed (or a vscode:// deep link) beside the chat, opened on the session workspace.',
  defaultEnabled: false,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        data: {
          placement: 'right',
          width: '560px',
          defaultCollapsed: true,
          dock: { pane: 'workspace', pos: 'right', enforce: true }
        } satisfies PaneContribution,
        render: () => <VscodePane />
      },
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/vscode' } satisfies RouteContribution,
        render: () => <VscodePane />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 59,
        data: { codicon: 'code', label: 'VS Code', path: '/vscode' } satisfies SidebarNavContribution
      }
    ])
  }
}

export default plugin
