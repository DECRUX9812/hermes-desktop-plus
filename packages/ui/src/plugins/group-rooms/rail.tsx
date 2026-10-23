/**
 * Thread rail — the group-rooms plugin's UI: rooms, their threads with live
 * per-seat status, and the room composer. A pure SDK-consumer view: every
 * read and write goes through the gateway JSON-RPC methods the plugin itself
 * registers (`plugin.group_rooms.*`); no core UI file is touched. The rail is
 * gated on the plugin's `thread_rail_enabled` config_schema knob, read through
 * the same RPC surface (`plugin.group_rooms.config`).
 */

import { host } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'

/** The composer always posts as the user — seats are the bots. */
const SENDER = 'user'

export interface GroupRoomsConfig {
  thread_rail_enabled: boolean
}

export interface RoomRow {
  id: number
  name: string
  mode: string
  goal: string
}

export interface SeatRow {
  bot: string
  role: string
  status: string
  status_note: string
}

export interface ThreadRow {
  id: number
  title: string
  origin_message_id: number | null
}

export interface RoomState {
  room: RoomRow
  seats: SeatRow[]
  threads: ThreadRow[]
  messages: Array<{ id: number; sender: string; text: string; thread_id: number | null }>
  handoffs: Array<{ id: number; thread_id: number; from_bot: string; to_bot: string; open: boolean }>
}

function request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  return host.request<T>(method, params ?? {})
}

/** One room state load + selection state, shared by the rail and composer.
 *  Gated: the config knob (`thread_rail_enabled`) is read first and NO room
 *  data flows while it is off or unresolved. */
export function useGroupRooms() {
  const [rooms, setRooms] = useState<RoomRow[]>([])
  const [room, setRoom] = useState<null | string>(null)
  const [state, setState] = useState<RoomState | null>(null)
  const [threadId, setThreadId] = useState<null | number>(null)
  const [error, setError] = useState<null | string>(null)
  const [loaded, setLoaded] = useState(false)
  const [enabled, setEnabled] = useState<null | boolean>(null)

  const loadState = useCallback(async (name: string) => {
    try {
      const reply = await request<{ state: RoomState }>('plugin.group_rooms.rooms.state', { room: name })
      setState(reply.state)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  const refresh = useCallback(async () => {
    try {
      const config = await request<GroupRoomsConfig>('plugin.group_rooms.config')
      setEnabled(Boolean(config.thread_rail_enabled))
      if (!config.thread_rail_enabled) {
        setLoaded(true)
        return
      }
      const reply = await request<{ rooms: RoomRow[] }>('plugin.group_rooms.rooms.list')
      setRooms(reply.rooms)
      setRoom(current => current ?? reply.rooms[0]?.name ?? null)
      setLoaded(true)
      setError(null)
    } catch (err) {
      setEnabled(false)
      setLoaded(true)
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    if (room) {
      loadState(room)
    }
  }, [room, loadState])

  return {
    enabled,
    rooms,
    room,
    selectRoom: (name: string) => {
      setRoom(name)
      setThreadId(null)
      setState(null)
    },
    state,
    threadId,
    selectThread: (id: null | number) => setThreadId(id),
    error,
    loaded,
    refresh: async () => {
      await refresh()
      if (room) {
        await loadState(room)
      }
    }
  }
}

/** Seat live status chip: idle → pending → working → done. */
export function SeatStatus({ seat }: { seat: SeatRow }) {
  return (
    <span
      className="inline-flex items-center gap-1 text-[0.6875rem] text-(--ui-text-tertiary)"
      data-seat-status={seat.status}
      title={seat.status_note || seat.status}
    >
      <span aria-hidden>{seat.bot}</span>
      <span className="text-(--ui-text-quaternary)">{seat.status}</span>
    </span>
  )
}

export function ThreadRail({ state, threadId, onSelectThread }: {
  state: RoomState
  threadId: null | number
  onSelectThread: (id: null | number) => void
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
      <ul aria-label="Threads">
        {state.threads.map(thread => (
          <li key={thread.id}>
            <button
              aria-current={threadId === thread.id}
              data-thread-title={thread.title}
              onClick={() => onSelectThread(threadId === thread.id ? null : thread.id)}
              type="button"
            >
              {thread.title || `Thread ${thread.id}`}
            </button>
          </li>
        ))}
        {state.threads.length === 0 && <li className="text-(--ui-text-tertiary)">No threads yet</li>}
      </ul>
      <div aria-label="Seats" className="flex flex-wrap gap-2">
        {state.seats.map(seat => <SeatStatus key={seat.bot} seat={seat} />)}
      </div>
    </div>
  )
}

export function Composer({ room, threadId, onPosted }: {
  room: null | string
  threadId: null | number
  onPosted: () => Promise<void>
}) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)

  const send = async () => {
    const body = text.trim()
    if (!room || !body || sending) {
      return
    }
    setSending(true)
    try {
      await request('plugin.group_rooms.message.post', {
        room,
        sender: SENDER,
        text: body,
        thread_id: threadId
      })
      setText('')
      await onPosted()
    } catch (err) {
      host.notifyError?.(err instanceof Error ? err.message : String(err), 'The message could not be posted')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex items-end gap-2">
      <textarea
        aria-label="Message"
        onChange={event => setText(event.target.value)}
        placeholder={threadId === null ? 'Message the room' : 'Reply in thread'}
        value={text}
      />
      <button disabled={!room || sending || !text.trim()} onClick={send} type="button">Send</button>
    </div>
  )
}

/** The config gate, shared by the page and any other rail mount point. */
export function useThreadRailEnabled(): null | boolean {
  const [enabled, setEnabled] = useState<null | boolean>(null)

  useEffect(() => {
    let live = true
    request<GroupRoomsConfig>('plugin.group_rooms.config')
      .then(config => {
        if (live) {
          setEnabled(Boolean(config.thread_rail_enabled))
        }
      })
      .catch(() => {
        if (live) {
          setEnabled(false)
        }
      })
    return () => {
      live = false
    }
  }, [])

  return enabled
}

export function GroupRoomsRail() {
  const view = useGroupRooms()

  if (view.enabled === false) {
    return (
      <div className="p-4 text-(--ui-text-tertiary)">
        The thread rail is disabled — enable the group-rooms plugin's "Thread rail" setting.
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3" data-rail-root>
      {view.rooms.length > 1 && (
        <select
          aria-label="Room"
          onChange={event => view.selectRoom(event.target.value)}
          value={view.room ?? ''}
        >
          {view.rooms.map(roomRow => (
            <option key={roomRow.id} value={roomRow.name}>{roomRow.name}</option>
          ))}
        </select>
      )}
      {view.error && <div className="text-(--ui-text-secondary)" role="alert">{view.error}</div>}
      {!view.error && view.state && (
        <ThreadRail onSelectThread={view.selectThread} state={view.state} threadId={view.threadId} />
      )}
      <Composer onPosted={view.refresh} room={view.room} threadId={view.threadId} />
    </div>
  )
}
