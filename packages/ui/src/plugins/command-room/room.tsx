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

import { useVoteCopy } from './i18n'

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
  /** Everyone asked: the room's seats, or the explicit asked list. */
  asked: string[]
  /** The group-rooms message that asked — the card anchors under it. */
  message_id: null | number
  thread_id: null | number
  status: 'decided' | 'open'
  decided: string
  counts: Record<string, number>
  voters: number
  quorum_met: boolean
  /** Asked names that have not picked yet — the card's "waiting on". */
  missing: string[]
  consensus: 'majority' | 'split' | 'unanimous' | 'waiting'
  ballots: Array<{ voter: string; option: string }>
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
    // Calling it is the owner's act, separate from any ballot: the decision is
    // recorded AND the `decided: <option>` line lands in the room log.
    callVote: (voteId: number, option: string) =>
      act(() => request('plugin.command_room.vote.call', { room, vote_id: voteId, option }))
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

/** Where the team stands, option by option — ported from the fork's VoteCard
 *  (group-room-cards.tsx): a bar per option scaled to the top count, who is on
 *  it, the consensus word with its own tone, and — while the decision is open —
 *  the per-option "Call it" that records the owner's word in the room. */
const VOTE_TONE: Record<VoteTally['consensus'], string> = {
  majority: 'bg-(--ui-accent)/12 text-(--ui-accent)',
  split: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  unanimous: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  waiting: 'bg-(--ui-bg-tertiary) text-(--ui-text-secondary)'
}

export function VoteCard({ vote, onCall }: { vote: VoteTally; onCall: (voteId: number, option: string) => void }) {
  const v = useVoteCopy()
  // The tallest bar sets the scale, so bars compare against each other.
  const top = Math.max(0, ...Object.values(vote.counts))
  const votersByOption = new Map<string, string[]>()

  for (const ballot of vote.ballots) {
    votersByOption.set(ballot.option, [...(votersByOption.get(ballot.option) ?? []), ballot.voter])
  }

  return (
    <div
      className="overflow-hidden rounded-xl border border-(--ui-stroke-secondary)"
      data-consensus={vote.consensus}
      data-message-id={vote.message_id ?? undefined}
      data-testid="command-vote-card"
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) bg-(--ui-bg-tertiary) px-3 py-1.5">
        <span className="text-[0.75rem] font-semibold text-foreground">{v.vote.title}</span>
        <span
          className={`ml-auto rounded-full px-2 py-px text-[0.625rem] font-semibold ${VOTE_TONE[vote.consensus]}`}
          data-testid="command-vote-consensus"
        >
          {v.vote.consensus[vote.consensus]}
        </span>
      </div>
      <div className="flex flex-col gap-1.5 px-3 py-2">
        <span className="text-[0.8125rem] font-medium leading-snug text-foreground">{vote.question}</span>
        {vote.options.map(option => {
          const voters = votersByOption.get(option) ?? []
          const isCalled = vote.status === 'decided' && vote.decided === option
          const width = top ? Math.round(((vote.counts[option] ?? 0) / top) * 100) : 0

          return (
            <div className="flex items-center gap-2" data-option={option} data-testid="command-vote-option" key={option}>
              <div className="relative min-w-0 flex-1 overflow-hidden rounded-md bg-(--ui-bg-tertiary)">
                {/* The bar is the count, so a glance reads the shape of the
                    room before any name is legible. */}
                <div
                  aria-hidden
                  className={`absolute inset-y-0 left-0 h-full transition-[width] ${
                    isCalled ? 'bg-emerald-500/35' : 'bg-(--ui-accent)/18'}`}
                  data-testid="command-vote-bar"
                  style={{ width: `${width}%` }}
                />
                <div className="relative flex flex-wrap items-center gap-x-2 px-2 py-1">
                  <span
                    className={`min-w-0 truncate text-[0.75rem] ${
                      isCalled ? 'font-semibold text-emerald-700 dark:text-emerald-300' : 'text-foreground'}`}
                  >
                    {option}
                  </span>
                  {voters.length ? (
                    <span className="truncate text-[0.625rem] text-(--ui-text-tertiary)">
                      {v.vote.picked(voters.join(', '))}
                    </span>
                  ) : null}
                </div>
              </div>
              {/* Calling it is the user's alone — the card records their word
                  in the room, it never records one on their behalf. */}
              {vote.status === 'open' ? (
                <button
                  className="rounded-md border border-(--ui-stroke-secondary) px-2 py-1 text-[0.6875rem] font-medium text-foreground hover:bg-(--ui-bg-tertiary)"
                  data-testid="command-vote-call"
                  onClick={() => onCall(vote.id, option)}
                  type="button"
                >
                  {v.vote.callIt}
                </button>
              ) : null}
            </div>
          )
        })}
      </div>
      <div className="flex flex-wrap gap-x-3 px-3 pb-2 text-[0.625rem] text-(--ui-text-quaternary)">
        {vote.status === 'decided' ? (
          <span
            className="font-semibold text-emerald-700 dark:text-emerald-300"
            data-testid="command-vote-decided"
          >
            {v.vote.decided(vote.decided)}
          </span>
        ) : (
          <>
            {vote.missing.length ? (
              <span data-testid="command-vote-missing">{v.vote.missing(vote.missing.join(', '))}</span>
            ) : null}
            <span>{v.vote.callHint}</span>
          </>
        )}
      </div>
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
        {view.votes.map(vote => <VoteCard key={vote.id} onCall={view.callVote} vote={vote} />)}
      </section>
      {view.catchup && <CatchUpPanel catchup={view.catchup} />}
      {view.config && !view.config.os_alerts_enabled && (
        <p>OS alerts are off — budget blocks stay silent until you look.</p>
      )}
    </div>
  )
}
