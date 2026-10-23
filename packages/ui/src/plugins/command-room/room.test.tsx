/**
 * The Command Room page against a mocked `@hermes/plugin-sdk` host: render the
 * CEO board, CALL a consensus vote (the card's option click is the owner's
 * "Call it" — `vote.call`, never a ballot on the owner's behalf), and ack an
 * escalation — every write hits the plugin's own `plugin.command_room.*` RPC.
 * The VoteCard's fork visuals (bars scaled to the top count, voter names,
 * per-word consensus chip tone, waiting-on footer, locale copy) are covered on
 * the exported card directly.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CommandRoom, VoteCard } from './room'
import { COMMAND_ROOM_LOCALES, type CommandRoomMessages } from './i18n'
import type { VoteTally } from './room'

const { requestMock, notifyErrorMock, translateMock } = vi.hoisted(() => ({
  requestMock: vi.fn<(method: string, params?: Record<string, unknown>) => Promise<unknown>>(),
  notifyErrorMock: vi.fn(),
  translateMock: vi.fn<(key: string, ...args: unknown[]) => string>()
}))

vi.mock('@hermes/plugin-sdk', () => ({
  host: { request: requestMock, notifyError: notifyErrorMock },
  usePluginI18n: () => translateMock
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
      asked: ['atlas', 'vex'], message_id: 12, thread_id: 3,
      status: 'open', decided: '', counts: { ship: 1, wait: 0 }, voters: 1, quorum_met: false,
      missing: ['vex'], consensus: 'majority',
      ballots: [{ voter: 'atlas', option: 'ship' }]
    }
  ]
}

/** A two-vote room: 2-1 lead (bars 100%/50%), one member still out. */
const CARD_VOTE: VoteTally = {
  id: 9, room: 'ops', question: 'ship the rewrite or keep the old one?', options: ['ship', 'keep'],
  quorum: 2, asked: ['atlas', 'vex', 'art'], message_id: 41, thread_id: 3,
  status: 'open', decided: '', counts: { ship: 2, keep: 1 }, voters: 3, quorum_met: true,
  missing: ['art'], consensus: 'majority',
  ballots: [
    { voter: 'atlas', option: 'ship' },
    { voter: 'vex', option: 'keep' },
    { voter: 'art', option: 'ship' }
  ]
}

/** Translate against one of the plugin's own bundles, the way the SDK does. */
function tFor(locale: keyof typeof COMMAND_ROOM_LOCALES) {
  return (key: string, ...args: unknown[]) => {
    const bundle = (COMMAND_ROOM_LOCALES[locale] ?? COMMAND_ROOM_LOCALES.en) as CommandRoomMessages
    let node: unknown = bundle
    for (const part of key.split('.')) node = (node as Record<string, unknown>)[part]
    return typeof node === 'function' ? node(...(args as [])) : (node as string)
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  translateMock.mockImplementation(tFor('en'))
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

  it('calls the vote with vote.call (never a ballot on the owner\'s behalf), then refreshes', async () => {
    render(<CommandRoom />)

    const call = await screen.findAllByTestId('command-vote-call')
    fireEvent.click(call[0])

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('plugin.command_room.vote.call', {
        room: 'ops',
        vote_id: 7,
        option: 'ship'
      })
    })
    expect(requestMock).not.toHaveBeenCalledWith('plugin.command_room.vote.cast', expect.anything())
    // The page re-reads the board and the vote list after the call.
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

describe('VoteCard', () => {
  it('draws per-option bars scaled to the top count, with the voter names', () => {
    render(<VoteCard onCall={vi.fn()} vote={CARD_VOTE} />)

    const options = screen.getAllByTestId('command-vote-option')
    expect(options).toHaveLength(2)
    // ship has 2 of the top count 2; keep has 1 — half the tallest bar.
    const widths = options.map(option =>
      (option.querySelector('[data-testid="command-vote-bar"]') as HTMLElement).style.width)
    expect(widths).toEqual(['100%', '50%'])
    expect(options[0].textContent).toContain('atlas, art picked this')
    expect(options[1].textContent).toContain('vex picked this')
  })

  it('chips the consensus word with its own tone', () => {
    const { rerender } = render(<VoteCard onCall={vi.fn()} vote={CARD_VOTE} />)

    const chip = screen.getByTestId('command-vote-consensus')
    expect(chip.textContent).toBe('Majority')
    expect(chip.className).toContain('text-(--ui-accent)')

    const asConsensus = (consensus: VoteTally['consensus']): VoteTally => ({
      ...CARD_VOTE,
      consensus,
      missing: consensus === 'unanimous' || consensus === 'waiting' ? [] : CARD_VOTE.missing,
      counts: consensus === 'waiting' ? { ship: 0, keep: 0 } : CARD_VOTE.counts,
      ballots: consensus === 'waiting' ? [] : CARD_VOTE.ballots
    })
    rerender(<VoteCard onCall={vi.fn()} vote={asConsensus('split')} />)
    expect(screen.getByTestId('command-vote-consensus').className).toContain('amber')

    rerender(<VoteCard onCall={vi.fn()} vote={asConsensus('unanimous')} />)
    expect(screen.getByTestId('command-vote-consensus').className).toContain('emerald')

    rerender(<VoteCard onCall={vi.fn()} vote={asConsensus('waiting')} />)
    expect(screen.getByTestId('command-vote-consensus').className).toContain('text-(--ui-text-secondary)')
    expect(screen.getByTestId('command-vote-consensus').textContent).toBe('Waiting')
  })

  it('names who is still missing in the footer while the vote is open', () => {
    render(<VoteCard onCall={vi.fn()} vote={CARD_VOTE} />)

    expect(screen.getByTestId('command-vote-missing').textContent).toBe('waiting on art')
    expect(screen.queryByTestId('command-vote-decided')).toBeNull()
  })

  it('styles the called option emerald and bold, and shows the decided marker', () => {
    const called: VoteTally = {
      ...CARD_VOTE,
      status: 'decided',
      decided: 'ship',
      consensus: 'unanimous',
      missing: []
    }
    render(<VoteCard onCall={vi.fn()} vote={called} />)

    const options = screen.getAllByTestId('command-vote-option')
    expect(options[0].querySelector('span')?.className).toContain('font-semibold')
    expect(options[0].querySelector('span')?.className).toContain('emerald')
    expect(options[1].querySelector('span')?.className).not.toContain('emerald')
    // A decided card is read-only: no Call it buttons remain.
    expect(screen.queryByTestId('command-vote-call')).toBeNull()
    expect(screen.getByTestId('command-vote-decided').textContent).toBe('Decided: ship')
  })

  it('anchors the card under the message that asked', () => {
    render(<VoteCard onCall={vi.fn()} vote={CARD_VOTE} />)

    expect(screen.getByTestId('command-vote-card').getAttribute('data-message-id')).toBe('41')
  })

  it('speaks the active locale (ja), and zh-Hant carries its own copy', () => {
    translateMock.mockImplementation(tFor('ja'))
    const { unmount } = render(<VoteCard onCall={vi.fn()} vote={CARD_VOTE} />)

    expect(screen.getByTestId('command-vote-card').textContent).toContain('チーム投票')
    expect(screen.getByTestId('command-vote-missing').textContent).toBe('art が未投票')
    expect(screen.getAllByTestId('command-vote-call')[0].textContent).toBe('決定する')
    unmount()

    translateMock.mockImplementation(tFor('zh-hant'))
    render(<VoteCard onCall={vi.fn()} vote={CARD_VOTE} />)
    expect(screen.getByTestId('command-vote-card').textContent).toContain('團隊投票')
    expect(screen.getByTestId('command-vote-missing').textContent).toBe('art 還沒投')
    expect(screen.getAllByTestId('command-vote-call')[0].textContent).toBe('拍板')
  })
})
