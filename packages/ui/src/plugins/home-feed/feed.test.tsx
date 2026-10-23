/**
 * The Home Feed page against a mocked `@hermes/plugin-sdk` host: render the
 * feed cards and the Recent tasks row, open a recent task (the host's
 * `openSession` must receive the session id), and dismiss a card (the RPC must
 * carry the right method and params).
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { HomeFeed } from './feed'

const { requestMock, openSessionMock, notifyErrorMock } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  openSessionMock: vi.fn(),
  notifyErrorMock: vi.fn()
}))

vi.mock('@hermes/plugin-sdk', () => ({
  host: { request: requestMock, openSession: openSessionMock, notifyError: notifyErrorMock }
}))

const CARDS = {
  cards: [
    {
      id: 'escalation:ops:1', kind: 'approval', title: 'deploy blocked', caption: 'atlas waited 25m',
      session_id: null, pinned: false, created_at: 5
    },
    {
      id: 'cron:late', kind: 'cronOverdue', title: 'overdue job', caption: 'hourly',
      session_id: null, pinned: false, created_at: 4
    },
    {
      id: 'handoff:ops:1', kind: 'handoff', title: 'deploy review', caption: 'atlas → vex',
      session_id: null, pinned: true, created_at: 3
    },
    {
      id: 'escalation:ops:9', kind: 'approval', title: 'stalled chat', caption: '',
      session_id: 'stored-9', pinned: false, created_at: 2
    }
  ]
}

const TASKS = {
  tasks: [
    { id: 'stored-9', title: 'just now', caption: 'wrote the brief', started_at: 1, ended_at: null, last_at: 2, message_count: 4 },
    { id: 'stored-8', title: 'yesterday', caption: '', started_at: 0, ended_at: 1, last_at: 1, message_count: 9 }
  ]
}

beforeEach(() => {
  vi.clearAllMocks()
  requestMock.mockImplementation(async (method: string) => {
    if (method === 'plugin.home_feed.config') {
      return {
        max_cards: 5, recent_tasks_limit: 3, feed_window_hours: 24,
        include_command_room: true, include_group_rooms: true
      }
    }
    if (method === 'plugin.home_feed.feed') {
      return CARDS
    }
    if (method === 'plugin.home_feed.recent_tasks') {
      return TASKS
    }
    return {}
  })
})

describe('HomeFeed', () => {
  it('renders the feed cards in the plugin order with their pinned marker', async () => {
    render(<HomeFeed />)

    expect(await screen.findByTestId('home-feed')).toBeTruthy()
    const cards = screen.getAllByTestId('home-feed-card')
    expect(cards.map(card => card.getAttribute('data-feed-id')))
      .toEqual(['escalation:ops:1', 'cron:late', 'handoff:ops:1', 'escalation:ops:9'])
    expect(within(cards[0]).getByTestId('feed-card-title').textContent).toBe('deploy blocked')
    // The pinned handoff card wears the marker; a session-backed card gets an Open door.
    expect(cards[2].textContent).toContain('📌')
    expect(cards[3].textContent).toContain('Open')
    expect(requestMock).toHaveBeenCalledWith('plugin.home_feed.feed', {})
  })

  it('renders the Recent tasks row and opens a session through the host on click', async () => {
    render(<HomeFeed />)

    const row = await screen.findByTestId('recent-tasks')
    expect(row.textContent).toContain('just now')
    expect(row.textContent).toContain('wrote the brief')

    const card = document.querySelector('[data-task-card="stored-9"]')
    expect(card).toBeTruthy()
    fireEvent.click(card as Element)

    await waitFor(() => {
      expect(openSessionMock).toHaveBeenCalledWith('stored-9')
    })
  })

  it('dismisses a card through the plugin RPC, then refreshes', async () => {
    render(<HomeFeed />)

    const row = (await screen.findAllByTestId('home-feed-card'))[0]
    fireEvent.click(within(row).getByTestId('feed-card-dismiss'))

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('plugin.home_feed.dismiss', { id: 'escalation:ops:1' })
    })
    // The page re-reads the feed (and the tasks row) after the dismiss.
    expect(requestMock).toHaveBeenCalledWith('plugin.home_feed.feed', {})
  })

  it('pins a card through the plugin RPC', async () => {
    render(<HomeFeed />)

    const pin = (await screen.findAllByRole('button', { name: 'Pin' }))[0]
    fireEvent.click(pin)

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('plugin.home_feed.pin.add', {
        id: 'escalation:ops:1', title: 'deploy blocked'
      })
    })
  })

  it('surfaces a feed failure as an alert instead of an empty page', async () => {
    requestMock.mockRejectedValue(new Error('home feed unreachable'))
    render(<HomeFeed />)

    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})
