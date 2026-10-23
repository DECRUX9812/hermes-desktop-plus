/**
 * The VS Code pane against a mocked `@hermes/plugin-sdk` host: the iframe
 * carries the RIGHT src (the plugin's embed_url, encoded server-side) with
 * sandbox attributes; with no server configured the pane degrades to the
 * vscode:// deep-link fallback — never a wrong embed; the CLI toggle gates on
 * the plugin's config and states the SDK's no-terminal limit instead of
 * spawning anything; and the RPC goes out with the focused session's id.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { VscodePane } from './vscode'

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }))

vi.mock('@hermes/plugin-sdk', () => ({
  host: {
    request: requestMock,
    state: {
      focusedSessionId: { get: () => 'sess-1', listen: () => () => {} }
    }
  },
  useValue: (store: { get: () => unknown }) => store.get()
}))

const CONFIG = {
  server_url: 'http://127.0.0.1:8080',
  workspace_root: '/work',
  open_side: 'right',
  default_width: 560,
  deep_link_enabled: true,
  cli_pane_enabled: true
}

const RESOLVE = {
  ok: true,
  folder: '/work/sess-1',
  server_url: 'http://127.0.0.1:8080',
  embed_url: 'http://127.0.0.1:8080/?folder=%2Fwork%2Fsess-1',
  deep_link_url: 'vscode://file/work/sess-1',
  deep_link_only: false,
  side: 'right',
  width: 560,
  cli_enabled: true
}

beforeEach(() => {
  vi.clearAllMocks()
  requestMock.mockImplementation(async (method: string) => {
    if (method === 'plugin.vscode.config') {
      return CONFIG
    }
    if (method === 'plugin.vscode.resolve') {
      return RESOLVE
    }
    return {}
  })
})

describe('VscodePane', () => {
  it('renders the sandboxed iframe with the configured embed src', async () => {
    render(<VscodePane />)

    const frame = await screen.findByTestId('vscode-iframe')
    expect(frame.getAttribute('src')).toBe('http://127.0.0.1:8080/?folder=%2Fwork%2Fsess-1')
    expect(frame.getAttribute('sandbox')).toContain('allow-scripts')
    expect(frame.getAttribute('sandbox')).not.toContain('allow-popups')
    // The fork's secondary affordance survives the embed: the deep link rides along.
    const link = screen.getByTestId('vscode-deep-link')
    expect(link.getAttribute('href')).toBe('vscode://file/work/sess-1')
  })

  it('falls back to the deep link only when no server is configured', async () => {
    requestMock.mockImplementation(async (method: string) => {
      if (method === 'plugin.vscode.config') {
        return { ...CONFIG, server_url: '' }
      }
      if (method === 'plugin.vscode.resolve') {
        return {
          ...RESOLVE,
          server_url: '',
          embed_url: null,
          deep_link_only: true
        }
      }
      return {}
    })

    render(<VscodePane />)

    expect(await screen.findByTestId('vscode-deep-link-fallback')).toBeTruthy()
    expect(screen.getByTestId('vscode-deep-link').getAttribute('href')).toBe(
      'vscode://file/work/sess-1'
    )
    expect(screen.queryByTestId('vscode-iframe')).toBeNull()
  })

  it('calls the plugin RPC with the focused session id', async () => {
    render(<VscodePane />)

    await screen.findByTestId('vscode-embed')
    expect(requestMock).toHaveBeenCalledWith('plugin.vscode.config', {})
    expect(requestMock).toHaveBeenCalledWith('plugin.vscode.resolve', { session_id: 'sess-1' })
  })

  it('toggles to the Hermes CLI view and back', async () => {
    render(<VscodePane />)

    await screen.findByTestId('vscode-embed')
    fireEvent.click(screen.getByTestId('vscode-tab-cli'))

    // The SDK exposes no terminal host API: the CLI view says so, it does not
    // fake one.
    expect(screen.getByTestId('vscode-cli-unavailable')).toBeTruthy()
    expect(screen.queryByTestId('vscode-iframe')).toBeNull()

    fireEvent.click(screen.getByTestId('vscode-tab-code'))
    expect(screen.getByTestId('vscode-iframe')).toBeTruthy()
  })

  it('hides the CLI toggle when cli_pane_enabled is off', async () => {
    requestMock.mockImplementation(async (method: string) => {
      if (method === 'plugin.vscode.config') {
        return { ...CONFIG, cli_pane_enabled: false }
      }
      if (method === 'plugin.vscode.resolve') {
        return { ...RESOLVE, cli_enabled: false }
      }
      return {}
    })

    render(<VscodePane />)

    await screen.findByTestId('vscode-embed')
    expect(screen.queryByTestId('vscode-tab-cli')).toBeNull()
  })

  it('surfaces a pane failure as an alert instead of an empty pane', async () => {
    requestMock.mockRejectedValue(new Error('vscode plugin unreachable'))
    render(<VscodePane />)

    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})
