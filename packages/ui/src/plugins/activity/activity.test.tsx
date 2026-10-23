/**
 * The Activity pane against a mocked `@hermes/plugin-sdk` host — with the REAL
 * `relativeTime` (the SDK re-exports it from lib/time), because the timestamp
 * contract is the point: a row whose `last_active` is now-3600 (integer UNIX
 * SECONDS, as the RPC contract promises) must render "1 hr ago"-style text,
 * NEVER thousands of days — the fork's "20,728 days ago" bug was a seconds
 * value reaching a millisecond formatter. Also the Now/Next/Done rail buckets
 * and a row click reaching `host.openSession`.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Activity } from './activity'

const { requestMock, openSessionMock, notifyErrorMock } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  openSessionMock: vi.fn(),
  notifyErrorMock: vi.fn()
}))

vi.mock('@hermes/plugin-sdk', async () => {
  const { relativeTime } = await import('@/lib/time')
  return {
    host: { request: requestMock, openSession: openSessionMock, notifyError: notifyErrorMock },
    relativeTime
  }
})

const CONFIG = {
  window_hours: 24, max_rows: 20, now_next_done_enabled: true, now_window_minutes: 10,
  include_cron: true, include_command_room: true, include_group_rooms: true
}

const nowSeconds = () => Math.floor(Date.now() / 1000)

function rpcRows(overrides: Array<Record<string, unknown>> = []) {
  return {
    rows: [
      {
        id: 'session:live', kind: 'session', label: 'running chat', detail: 'Conversation',
        status: 'running', session_id: 'live', last_active: nowSeconds() - 60, created_at: nowSeconds() - 600
      },
      ...overrides
    ],
    total: 1 + overrides.length
  }
}

const RAIL = {
  enabled: true,
  now: [
    {
      id: 'session:live', kind: 'session', label: 'running chat', detail: 'Conversation',
      status: 'running', session_id: 'live', last_active: nowSeconds() - 60, created_at: null
    }
  ],
  next: [
    {
      id: 'cron:soon', kind: 'cron', label: 'due soon', detail: 'every 10m',
      status: 'success', session_id: null, last_active: nowSeconds() + 600, created_at: null
    }
  ],
  done: [
    {
      id: 'session:past', kind: 'session', label: 'finished chat', detail: 'Conversation (ended)',
      status: 'success', session_id: 'past', last_active: nowSeconds() - 3600, created_at: null
    }
  ]
}

beforeEach(() => {
  vi.clearAllMocks()
  requestMock.mockImplementation(async (method: string) => {
    if (method === 'plugin.activity.config') {
      return CONFIG
    }
    if (method === 'plugin.activity.rows') {
      return rpcRows()
    }
    if (method === 'plugin.activity.rail') {
      return RAIL
    }
    return {}
  })
})

describe('Activity', () => {
  it('formats a seconds last_active (now-3600) as about an hour ago, never thousands of days', async () => {
    // last_active is integer UNIX SECONDS per the RPC contract.
    requestMock.mockImplementation(async (method: string) => {
      if (method === 'plugin.activity.config') {
        return CONFIG
      }
      if (method === 'plugin.activity.rows') {
        return {
          rows: [
            {
              id: 'session:past', kind: 'session', label: 'finished chat', detail: '',
              status: 'success', session_id: null, last_active: nowSeconds() - 3600, created_at: null
            }
          ],
          total: 1
        }
      }
      if (method === 'plugin.activity.rail') {
        return { enabled: false, now: [], next: [], done: [] }
      }
      return {}
    })

    render(<Activity />)

    const times = await screen.findAllByTestId('activity-row-time')
    expect(times[0].textContent).toMatch(/1 hr/i)
    expect(times[0].textContent).toMatch(/ago/i)
    // The exact regression the fork's fix commit 3f8c0538 pins: a seconds value
    // formatted as ms reads "~20,728 days ago". Never again.
    expect(times[0].textContent).not.toMatch(/20,?728/)
    expect(screen.queryByText(/days ago/)).toBeNull()
  })

  it('renders the Now/Next/Done rail buckets', async () => {
    render(<Activity />)

    const rail = await screen.findByTestId('activity-rail')
    expect(rail).toBeTruthy()
    expect(screen.getByTestId('rail-now').textContent).toContain('running chat')
    expect(screen.getByTestId('rail-next').textContent).toContain('due soon')
    expect(screen.getByTestId('rail-done').textContent).toContain('finished chat')
  })

  it('hides the rail when now_next_done_enabled is off', async () => {
    requestMock.mockImplementation(async (method: string) => {
      if (method === 'plugin.activity.config') {
        return { ...CONFIG, now_next_done_enabled: false }
      }
      if (method === 'plugin.activity.rows') {
        return rpcRows()
      }
      if (method === 'plugin.activity.rail') {
        return { enabled: false, now: [], next: [], done: [] }
      }
      return {}
    })

    render(<Activity />)

    expect(await screen.findByTestId('activity-rows')).toBeTruthy()
    expect(screen.queryByTestId('activity-rail')).toBeNull()
  })

  it('opens a session row through the host openSession on click', async () => {
    render(<Activity />)

    await screen.findByTestId('activity-rows')
    const row = document.querySelector('[data-activity-row="session:live"]')
    expect(row).toBeTruthy()
    fireEvent.click(row as Element)

    await waitFor(() => {
      expect(openSessionMock).toHaveBeenCalledWith('live')
    })
  })

  it('surfaces a pane failure as an alert instead of an empty page', async () => {
    requestMock.mockRejectedValue(new Error('activity unreachable'))
    render(<Activity />)

    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})
