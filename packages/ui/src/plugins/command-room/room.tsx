/**
 * Command Room view — the CEO board, the escalations list (ack / snooze),
 * consensus vote cards, the catch-up panel and the 24h brief, and the
 * moved-threads indicator. A pure SDK consumer: every read and write goes
 * through the `plugin.command_room.*` RPC the Python plugin registers (plus the
 * group-rooms room list for the room picker), so this file holds no data model
 * of its own — the shapes below mirror the plugin's JSON results.
 */

import { host } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'

export interface CommandRoomConfig {
  brief_window_hours: number
  default_seat_budget: number
  escalation_threshold_minutes: number
  consensus_quorum: number
  os_alerts_enabled: boolean
}

export interface BoardCard {
  thread_id: null | number
  title: string
  pinned: boolean
  escalated: boolean
  open_handoffs: number
  last_at: number
  messages: number
}

export interface Board {
  room: string
  tiles: { needs_you: number; open_handoffs: number; escalations_open: number; delivered_today: number }
  columns: Record<'answered' | 'needs' | 'waiting' | 'working', BoardCard[]>
}

export interface Escalation {
  id: number
  room: string
  thread_id: null | number
  kind: string
  title: string
  detail: string
  status: 'acked' | 'open'
  snoozed_until: null | number
}

export interface VoteTally {
  id: number
  room: string
  question: string
  options: string[]
  quorum: number
  status: 'decided' | 'open'
  decided: string
  counts: Record<string, number>
  voters: number
  quorum_met: boolean
  consensus: 'majority' | 'split' | 'unanimous' | 'waiting'
}

export interface CatchUpLine {
  thread_id: null | number
  title: string
  fresh: number
  escalated: boolean
  pinned: boolean
  unseen: boolean
}

export interface Brief {
  hours: number
  rooms: Array<{ room: string; fresh_messages: number; escalations: number; escalations_open: number; budget_alerts: number }>
}

export interface MovedThread {
  thread_id: null | number
  title: string
  last_at: number
  messages: number
}

function request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  return host.request<T>(method, params ?? {})
}

/** All command-room state for the page: one load + the room-scoped slices. */
export function useCommandRoom() {
  const [config, setConfig] = useState<null | CommandRoomConfig>(null)
  const [rooms, setRooms] = useState<string[]>([])
  const [room, setRoom] = useState<null | string>(null)
  const [board, setBoard] = useState<null | Board>(null)
  const [escalations, setEscalations] = useState<Escalation[]>([])
  const [votes, setVotes] = useState<VoteTally[]>([])
  const [catchup, setCatchup] = useState<null | { lines: CatchUpLine[]; total: number }>(null)
  const [moved, setMoved] = useState<MovedThread[]>([])
  const [error, setError] = useState<null | string>(null)

  const loadRoom = useCallback(async (name: string) => {
    try {
      const [boardReply, votesReply, catchupReply, movedReply] = await Promise.all([
        request<{ board: Board }>('plugin.command_room.board.get', { room: name }),
        request<{ votes: VoteTally[] }>('plugin.command_room.votes.list', { room: name }),
        request<{ lines: CatchUpLine[]; total: number }>('plugin.command_room.catchup', { room: name }),
        request<{ threads: MovedThread[] }>('plugin.command_room.moved.threads', { room: name })
      ])
      setBoard(boardReply.board)
      setVotes(votesReply.votes)
      setCatchup(catchupReply)
      setMoved(movedReply.threads)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [configReply, escalationsReply] = await Promise.all([
        request<CommandRoomConfig>('plugin.command_room.config'),
        request<{ escalations: Escalation[] }>('plugin.command_room.escalations.list', {})
      ])
      setConfig(configReply)
      setEscalations(escalationsReply.escalations)
      // The room picker prefers the group-rooms rooms; escalations on rooms the
      // integration can't see still deserve a picker entry.
      let names = escalationsReply.escalations.map(escalation => escalation.room)
      try {
        const roomsReply = await request<{ rooms: Array<{ name: string }> }>('plugin.group_rooms.rooms.list')
        names = [...names, ...roomsReply.rooms.map(row => row.name)]
      } catch {
        // group-rooms not registered: the picker runs on command-room rooms alone.
      }
      const unique = [...new Set(names)].sort()
      setRooms(unique)
      setRoom(current => current ?? unique[0] ?? null)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    if (room) {
      loadRoom(room)
    }
  }, [room, loadRoom])

  const act = useCallback(async (fn: () => Promise<unknown>) => {
    try {
      await fn()
    } catch (err) {
      host.notifyError?.(err instanceof Error ? err.message : String(err), 'Command Room')
    } finally {
      await refresh()
      if (room) {
        await loadRoom(room)
      }
    }
  }, [refresh, loadRoom, room])

  return {
    config,
    rooms,
    room,
    selectRoom: setRoom,
    board,
    escalations,
    votes,
    catchup,
    moved,
    error,
    refresh,
    ack: (escalationId: number, note = '') =>
      act(() => request('plugin.command_room.escalations.ack', { room, escalation_id: escalationId, note })),
    snooze: (escalationId: number, minutes = 60) =>
      act(() => request('plugin.command_room.escalations.snooze', { room, escalation_id: escalationId, minutes })),
    castVote: (voteId: number, option: string) =>
      act(() => request('plugin.command_room.vote.cast', { room, vote_id: voteId, voter: 'user', option }))
  }
}

const COLUMNS: ReadonlyArray<{ id: 'answered' | 'needs' | 'waiting' | 'working'; label: string }> = [
  { id: 'needs', label: 'Needs you' },
  { id: 'waiting', label: 'Waiting on the team' },
  { id: 'working', label: 'Working' },
  { id: 'answered', label: 'Answered' }
]

export function BoardView({ board, moved }: { board: Board; moved: MovedThread[] }) {
  const movedIds = new Set(moved.map(thread => thread.thread_id))

  return (
    <div aria-label="CEO board" data-testid="command-room-board">
      <div role="group" aria-label="At a glance">
        <span data-testid="tile-needs-you">Needs you: {board.tiles.needs_you}</span>
        <span>Open handoffs: {board.tiles.open_handoffs}</span>
        <span>Delivered today: {board.tiles.delivered_today}</span>
      </div>
      <div>
        {COLUMNS.map(column => {
          const cards = board.columns[column.id]
          return (
            <section
              aria-label={`${column.label} (${cards.length})`}
              data-board-column={column.id}
              key={column.id}
            >
              <h3>{column.label} ({cards.length})</h3>
              {cards.map(card => (
                <button
                  data-testid="command-board-card"
                  data-moved={movedIds.has(card.thread_id) || undefined}
                  key={`${column.id}:${card.thread_id}`}
                  type="button"
                >
                  {card.pinned ? '📌 ' : ''}
                  {card.title || `Thread ${card.thread_id}`}
                  {movedIds.has(card.thread_id) ? ' · moved' : ''}
                </button>
              ))}
              {cards.length === 0 && <p>Nothing here.</p>}
            </section>
          )
        })}
      </div>
    </div>
  )
}

export function EscalationsList({ escalations, onAck, onSnooze }: {
  escalations: Escalation[]
  onAck: (id: number) => void
  onSnooze: (id: number) => void
}) {
  const open = escalations.filter(escalation => escalation.status === 'open')

  return (
    <section aria-label="Escalations" data-testid="command-room-escalations">
      <h2>Escalations ({open.length})</h2>
      {open.length === 0 && <p>Nothing needs you.</p>}
      <ul>
        {open.map(escalation => (
          <li data-testid="escalation-row" key={escalation.id}>
            <span>{escalation.kind === 'loop' ? 'Loop' : 'Stalled'}: {escalation.title}</span>
            {escalation.snoozed_until !== null && <span> · snoozed</span>}
            <button onClick={() => onAck(escalation.id)} type="button">Ack</button>
            <button onClick={() => onSnooze(escalation.id)} type="button">Snooze 1h</button>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function VoteCard({ vote, onCast }: { vote: VoteTally; onCast: (voteId: number, option: string) => void }) {
  return (
    <div data-consensus={vote.consensus} data-testid="command-vote-card">
      <span>{vote.question}</span>
      <span> — {vote.status === 'decided' ? `decided: ${vote.decided}` : vote.consensus}</span>
      <span>
        {' '}(quorum {vote.quorum_met ? 'met' : 'waiting'}, {vote.voters} of {vote.quorum})
      </span>
      {vote.status === 'open' && (
        <div role="group" aria-label={`Vote: ${vote.question}`}>
          {vote.options.map(option => (
            <button
              data-testid="vote-option"
              key={option}
              onClick={() => onCast(vote.id, option)}
              type="button"
            >
              {option} ({vote.counts[option] ?? 0})
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function CatchUpPanel({ catchup }: { catchup: { lines: CatchUpLine[]; total: number } }) {
  return (
    <section aria-label="Catch-up" data-testid="command-room-catchup">
      <h2>Catch-up ({catchup.total} new)</h2>
      {catchup.lines.length === 0 && <p>Nothing moved.</p>}
      <ul>
        {catchup.lines.map(line => (
          <li key={`${line.thread_id}`}>
            {line.title || `Thread ${line.thread_id}`}
            {line.escalated ? ' · escalated' : ''}
            {line.pinned ? ' · pinned' : ''}
            {line.fresh > 0 ? ` · ${line.fresh} new` : ''}
          </li>
        ))}
      </ul>
    </section>
  )
}

export function CommandRoom() {
  const view = useCommandRoom()

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3" data-command-room-root>
      {view.error && <div role="alert">{view.error}</div>}
      <div>
        <select
          aria-label="Room"
          onChange={event => view.selectRoom(event.target.value)}
          value={view.room ?? ''}
        >
          {view.rooms.map(name => <option key={name} value={name}>{name}</option>)}
        </select>
        <button onClick={() => view.refresh()} type="button">Refresh</button>
      </div>
      {view.board && <BoardView board={view.board} moved={view.moved} />}
      <EscalationsList
        escalations={view.escalations}
        onAck={id => view.ack(id)}
        onSnooze={id => view.snooze(id)}
      />
      <section aria-label="Consensus votes" data-testid="command-room-votes">
        <h2>Votes</h2>
        {view.votes.length === 0 && <p>No open decisions.</p>}
        {view.votes.map(vote => <VoteCard key={vote.id} onCast={view.castVote} vote={vote} />)}
      </section>
      {view.catchup && <CatchUpPanel catchup={view.catchup} />}
      {view.config && !view.config.os_alerts_enabled && (
        <p>OS alerts are off — budget blocks stay silent until you look.</p>
      )}
    </div>
  )
}
