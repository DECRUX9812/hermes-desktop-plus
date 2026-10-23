/**
 * Home Feed view — the action cards and the "Recent tasks" row. A pure SDK
 * consumer: every read and write goes through the `plugin.home_feed.*` RPC the
 * Python plugin registers (dismiss and pin included, so the state survives a
 * reload the way the fork's localStorage did), and a task card opens its
 * session through the host's `openSession` — the same door core surfaces use.
 * The shapes below mirror the plugin's JSON results.
 */

import { host } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'

export interface HomeFeedConfig {
  max_cards: number
  recent_tasks_limit: number
  feed_window_hours: number
  include_command_room: boolean
  include_group_rooms: boolean
}

export interface FeedCard {
  id: string
  kind: string
  title: string
  caption: string
  session_id: null | string
  cron_job_id?: string
  room?: string
  pinned: boolean
  created_at: number
}

export interface TaskCard {
  id: string
  title: string
  caption: string
  started_at: number | null
  ended_at: number | null
  last_at: number | null
  message_count: number
}

function request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  return host.request<T>(method, params ?? {})
}

/** All home-feed state for the page: one load, refreshed after every action. */
export function useHomeFeed() {
  const [config, setConfig] = useState<null | HomeFeedConfig>(null)
  const [cards, setCards] = useState<FeedCard[]>([])
  const [tasks, setTasks] = useState<TaskCard[]>([])
  const [error, setError] = useState<null | string>(null)

  const refresh = useCallback(async () => {
    try {
      const [configReply, feedReply, tasksReply] = await Promise.all([
        request<HomeFeedConfig>('plugin.home_feed.config'),
        request<{ cards: FeedCard[] }>('plugin.home_feed.feed'),
        request<{ tasks: TaskCard[] }>('plugin.home_feed.recent_tasks')
      ])
      setConfig(configReply)
      setCards(feedReply.cards)
      setTasks(tasksReply.tasks)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const act = useCallback(async (fn: () => Promise<unknown>) => {
    try {
      await fn()
    } catch (err) {
      host.notifyError?.(err instanceof Error ? err.message : String(err), 'Home Feed')
    } finally {
      await refresh()
    }
  }, [refresh])

  return {
    config,
    cards,
    tasks,
    error,
    refresh,
    dismiss: (id: string) => act(() => request('plugin.home_feed.dismiss', { id })),
    restore: (id: string) => act(() => request('plugin.home_feed.restore', { id })),
    pin: (card: FeedCard) => act(() => request('plugin.home_feed.pin.add', { id: card.id, title: card.title })),
    unpin: (id: string) => act(() => request('plugin.home_feed.pin.remove', { id })),
    /** A task card opens its session in place — the fork's openSession(id, navigate, 'in-place'). */
    openTask: (sessionId: string) => host.openSession(sessionId)
  }
}

const KIND_GLYPH: Record<string, string> = {
  approval: 'bell-dot',
  handoff: 'arrow-switch',
  cronOverdue: 'clock',
  cronDue: 'clock'
}

export function FeedCardRow({ card, onDismiss, onOpen, onPin, onUnpin }: {
  card: FeedCard
  onDismiss: (id: string) => void
  onOpen: (card: FeedCard) => void
  onPin: (card: FeedCard) => void
  onUnpin: (id: string) => void
}) {
  return (
    <div data-feed-id={card.id} data-feed-kind={card.kind} data-testid="home-feed-card">
      {card.pinned ? '📌 ' : ''}
      <span>{KIND_GLYPH[card.kind] ?? 'circle-large'}</span>
      <div>
        <div data-testid="feed-card-title">{card.title}</div>
        {card.caption ? <div data-testid="feed-card-caption">{card.caption}</div> : null}
      </div>
      {card.session_id ? (
        <button data-feed-primary="true" onClick={() => onOpen(card)} type="button">Open</button>
      ) : null}
      <button onClick={() => (card.pinned ? onUnpin(card.id) : onPin(card))} type="button">
        {card.pinned ? 'Unpin' : 'Pin'}
      </button>
      <button aria-label={`Dismiss ${card.title}`} data-testid="feed-card-dismiss" onClick={() => onDismiss(card.id)} type="button">
        Dismiss
      </button>
    </div>
  )
}

/** One task, one card (the fork's TaskCard shape): title, status caption, open. */
export function TaskCardView({ task, onOpen }: { task: TaskCard; onOpen: (sessionId: string) => void }) {
  return (
    <button data-slot="task-card" data-task-card={task.id} onClick={() => onOpen(task.id)} type="button">
      <span data-testid="task-card-title">{task.title || task.id}</span>
      {task.caption ? <span data-testid="task-card-caption">{task.caption}</span> : null}
    </button>
  )
}

export function HomeFeed() {
  const view = useHomeFeed()

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3" data-home-feed-root>
      {view.error && <div role="alert">{view.error}</div>}
      <div>
        <button onClick={() => view.refresh()} type="button">Refresh</button>
      </div>
      <section aria-label="Recent tasks" data-slot="recent-tasks" data-testid="recent-tasks">
        <h2>Recent tasks</h2>
        {view.tasks.length === 0 && <p>No recent tasks.</p>}
        <div data-testid="task-cards">
          {view.tasks.map(task => (
            <TaskCardView key={task.id} onOpen={view.openTask} task={task} />
          ))}
        </div>
      </section>
      <section aria-label="Needs you" data-testid="home-feed">
        <h2>Needs you ({view.cards.length})</h2>
        {view.cards.length === 0 && <p>Nothing needs you.</p>}
        {view.cards.map(card => (
          <FeedCardRow
            card={card}
            key={card.id}
            onDismiss={view.dismiss}
            onOpen={open => open.session_id && view.openTask(open.session_id)}
            onPin={view.pin}
            onUnpin={view.unpin}
          />
        ))}
      </section>
    </div>
  )
}
