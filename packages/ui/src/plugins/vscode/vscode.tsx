/**
 * VS Code view — the fork's Code pane as a pure SDK consumer. Every decision
 * comes through the `plugin.vscode.*` RPC the Python plugin registers: the
 * workspace folder for the focused session, the embed URL, and the deep link.
 *
 * The fork opened the user's own VS Code through an Electron preload that
 * spawned and served it on loopback; the browser add-on has no preload and the
 * plugin SDK exposes no process or terminal surface, so this view embeds a
 * configured code-server/openvscode-server URL in a sandboxed iframe and falls
 * back to a `vscode://file` deep link when none is configured. The shapes below
 * mirror the plugin's JSON results.
 */

import { host, useValue } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'

export interface VscodeConfig {
  server_url: string
  workspace_root: string
  open_side: string
  default_width: number
  deep_link_enabled: boolean
  cli_pane_enabled: boolean
}

export interface VscodeResolve {
  ok: boolean
  reason?: string
  folder: null | string
  server_url: string
  embed_url: null | string
  deep_link_url: null | string
  deep_link_only: boolean
  side: string
  width: number
  cli_enabled: boolean
}

function request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  return host.request<T>(method, params ?? {})
}

/** All pane state for a session: one load, refreshed on demand. */
export function useVscodePane(sessionId: null | string) {
  const [config, setConfig] = useState<null | VscodeConfig>(null)
  const [resolved, setResolved] = useState<null | VscodeResolve>(null)
  const [error, setError] = useState<null | string>(null)

  const refresh = useCallback(async () => {
    try {
      const [configReply, resolveReply] = await Promise.all([
        request<VscodeConfig>('plugin.vscode.config'),
        request<VscodeResolve>('plugin.vscode.resolve', { session_id: sessionId })
      ])
      setConfig(configReply)
      setResolved(resolveReply)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [sessionId])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { config, resolved, error, refresh }
}

function CodeTab({ view, width }: { view: ReturnType<typeof useVscodePane>; width: number }) {
  const resolved = view.resolved

  if (!view.error && !resolved) {
    return <p data-testid="vscode-loading">Loading workspace…</p>
  }
  if (!resolved) {
    return null
  }
  if (!resolved.ok) {
    // The plugin never invents a folder: an unresolvable workspace is an
    // honest empty state, not a wrong-directory embed.
    return <p data-testid="vscode-missing">No workspace folder to open for this session.</p>
  }

  return (
    <div
      className="flex min-h-0 flex-1 flex-col gap-2"
      data-testid="vscode-embed"
      style={{ width: `${width}px`, maxWidth: '100%' }}
    >
      {resolved.embed_url ? (
        // Sandboxed: the code-server origin gets scripts + same-origin (its own
        // UI needs them) but nothing more; no allow-popups, no top navigation.
        <iframe
          data-testid="vscode-iframe"
          sandbox="allow-scripts allow-same-origin allow-forms"
          src={resolved.embed_url}
          title="VS Code"
        />
      ) : resolved.deep_link_only ? (
        <p data-testid="vscode-deep-link-fallback">
          No code-server configured — open the workspace folder in your desktop's VS Code instead.
        </p>
      ) : (
        <p data-testid="vscode-not-configured">VS Code pane is not configured.</p>
      )}
      {resolved.deep_link_url && (
        <a data-testid="vscode-deep-link" href={resolved.deep_link_url}>
          Open in VS Code
        </a>
      )}
      <span data-testid="vscode-folder">{resolved.folder}</span>
    </div>
  )
}

function CliTab() {
  // The plugin SDK exposes no terminal host API, and a plugin that spawned its
  // own PTY would be exactly the process-spawning this port forbids — so the
  // CLI toggle points at the host terminal instead of faking one.
  return (
    <div data-testid="vscode-cli-pane">
      <p data-testid="vscode-cli-unavailable">
        The Hermes CLI runs in the host terminal. The plugin SDK does not expose a terminal
        surface, so this pane cannot embed it.
      </p>
    </div>
  )
}

export function VscodePane() {
  // The pane opens the FOCUSED chat's workspace — the fork's per-session
  // folder, resolved server-side by the plugin's RPC.
  const sessionId = useValue(host.state.focusedSessionId)
  const [tab, setTab] = useState<'cli' | 'code'>('code')
  const view = useVscodePane(sessionId)

  const cliOn = view.config?.cli_pane_enabled !== false
  const width = view.resolved?.width ?? view.config?.default_width ?? 560

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-3" data-vscode-root="" data-vscode-side={view.resolved?.side}>
      <div data-testid="vscode-tabs" role="tablist">
        <button
          aria-selected={tab === 'code'}
          data-testid="vscode-tab-code"
          onClick={() => setTab('code')}
          role="tab"
          type="button"
        >
          VS Code
        </button>
        {cliOn && (
          <button
            aria-selected={tab === 'cli'}
            data-testid="vscode-tab-cli"
            onClick={() => setTab('cli')}
            role="tab"
            type="button"
          >
            Hermes CLI
          </button>
        )}
        <button onClick={() => view.refresh()} type="button">
          Refresh
        </button>
      </div>
      {view.error && <div role="alert">{view.error}</div>}
      {tab === 'code' ? <CodeTab view={view} width={width} /> : <CliTab />}
    </div>
  )
}
