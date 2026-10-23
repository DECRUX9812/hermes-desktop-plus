/**
 * The Command Room page against a mocked `@hermes/plugin-sdk` host: render the
 * CEO board, cast a consensus vote (the RPC must carry the right method and
 * params), and ack an escalation — every write hits the plugin's own
 * `plugin.command_room.*` RPC.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CommandRoom } from './room'

const { requestMock, notifyErrorMock } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  notifyErrorMock: vi.fn()
}))

vi.mock('@hermes/plugin-sdk', () => ({
  host: { request: requestMock, notifyError: notifyErrorMock }
}))

const BOARD = {
  board: {
    room: 'ops',
    tiles: { needs_you: 1, open_handoffs: 2, escalations_open: 1, delivered_today: 3 },
    columns: {
      needs: [{ thread_id: 3, title: 'deploy blocked', pinned: false, escalated: true, open_handoffs: 0, last_at: 5, messages: 4 }],
      waiting: [{ thread_id: 4, title: 'hero shot', pinned: true, escalated: false, open_handoffs: 2, last_at: 4, messages: 2 }],
      working: [],
      answered: []
    }
  }
}

const ESCALATIONS = {
  escalations: [
    { id: 1, room: 'ops', thread_id: 3, kind: 'stalled', title: 'deploy blocked', detail: '', status: 'open', snoozed_until: null },
    { id: 2, room: 'ops', thread_id: null, kind: 'loop', title: 'ping-pong on pricing', detail: '', status: 'acked', snoozed_until: null }
  ]
}

const VOTES = {
  votes: [
    {
      id: 7, room: 'ops', question: 'ship friday?', options: ['ship', 'wait'], quorum: 2,
      status: 'open', decided: '', counts: { ship: 1, wait: 0 }, voters: 1, quorum_met: false, consensus: 'majority'
    }
  ]
}

beforeEach(() => {
  vi.clearAllMocks()
  requestMock.mockImplementation(async (method: string) => {
    if (method === 'plugin.command_room.config') {
      return {
        brief_window_hours: 24, default_seat_budget: 100,
        escalation_threshold_minutes: 20, consensus_quorum: 2, os_alerts_enabled: true
      }
    }
    if (method === 'plugin.command_room.escalations.list') {
      return ESCALATIONS
    }
    if (method === 'plugin.group_rooms.rooms.list') {
      return { rooms: [{ name: 'ops' }, { name: 'port' }] }
    }
    if (method === 'plugin.command_room.board.get') {
      return BOARD
    }
    if (method === 'plugin.command_room.votes.list') {
      return VOTES
    }
    if (method === 'plugin.command_room.catchup') {
      return {
        total: 3,
        lines: [
          { thread_id: 3, title: 'deploy blocked', fresh: 2, escalated: true, pinned: false, unseen: true },
          { thread_id: 4, title: 'hero shot', fresh: 1, escalated: false, pinned: true, unseen: false }
        ]
      }
    }
    if (method === 'plugin.command_room.moved.threads') {
      return { room: 'ops', threads: [{ thread_id: 3, title: 'deploy blocked', last_at: 5, messages: 4 }], marked: true }
    }
    return {}
  })
})

describe('CommandRoom', () => {
  it('renders the CEO board with tiles, columns and the moved-threads marker', async () => {
    render(<CommandRoom />)

    expect(await screen.findByTestId('command-room-board')).toBeTruthy()
    expect(screen.getByTestId('tile-needs-you').textContent).toContain('Needs you: 1')
    expect(screen.getByLabelText('Needs you (1)')).toBeTruthy()
    expect(screen.getByLabelText('Waiting on the team (1)')).toBeTruthy()
    // The hero-shot card is pinned AND in the waiting column; deploy-blocked moved.
    const cards = screen.getAllByTestId('command-board-card')
    expect(cards.map(card => card.textContent)).toEqual(['deploy blocked · moved', '📌 hero shot'])
    expect(requestMock).toHaveBeenCalledWith('plugin.command_room.board.get', { room: 'ops' })
    expect(requestMock).toHaveBeenCalledWith('plugin.command_room.moved.threads', { room: 'ops' })
  })

  it('casts a consensus vote with the right method and params, then refreshes', async () => {
    render(<CommandRoom />)

    const option = await screen.findByText('wait (0)')
    fireEvent.click(option)

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('plugin.command_room.vote.cast', {
        room: 'ops',
        vote_id: 7,
        voter: 'user',
        option: 'wait'
      })
    })
    // The page re-reads the board and the vote list after the cast.
    expect(requestMock).toHaveBeenCalledWith('plugin.command_room.votes.list', { room: 'ops' })
    expect(requestMock).toHaveBeenCalledWith('plugin.command_room.board.get', { room: 'ops' })
  })

  it('acks an escalation through the plugin RPC', async () => {
    render(<CommandRoom />)

    const row = await screen.findByTestId('escalation-row')
    expect(row.textContent).toContain('deploy blocked')
    // The acked escalation stays off the list.
    expect(screen.queryByText(/ping-pong on pricing/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Ack' }))

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('plugin.command_room.escalations.ack', {
        room: 'ops',
        escalation_id: 1,
        note: ''
      })
    })
  })

  it('snoozes an escalation for an hour', async () => {
    render(<CommandRoom />)

    fireEvent.click(await screen.findByRole('button', { name: 'Snooze 1h' }))

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('plugin.command_room.escalations.snooze', {
        room: 'ops',
        escalation_id: 1,
        minutes: 60
      })
    })
  })

  it('renders the catch-up lines with their flags', async () => {
    render(<CommandRoom />)

    const panel = await screen.findByTestId('command-room-catchup')
    expect(panel.textContent).toContain('Catch-up (3 new)')
    expect(panel.textContent).toContain('deploy blocked · escalated · 2 new')
    expect(panel.textContent).toContain('hero shot · pinned · 1 new')
  })

  it('picks a room through the group-rooms-backed picker', async () => {
    render(<CommandRoom />)

    fireEvent.change(await screen.findByLabelText('Room'), { target: { value: 'port' } })

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('plugin.command_room.board.get', { room: 'port' })
    })
    expect(requestMock).toHaveBeenCalledWith('plugin.group_rooms.rooms.list', {})
  })
})
