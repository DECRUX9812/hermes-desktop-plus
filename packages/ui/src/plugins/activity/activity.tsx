/**
 * Activity view — the activity rows and the Now/Next/Done bot rail. A pure SDK
 * consumer: every read goes through the `plugin.activity.*` RPC the Python
 * plugin registers, and a session row opens through the host's `openSession` —
 * the same door core surfaces use.
 *
 * TIMESTAMP CONTRACT: the RPC's `last_active` is integer UNIX SECONDS. The UI
 * converts seconds to milliseconds EXACTLY ONCE (x * 1000) at the relative-time
 * boundary — `relativeTime` takes ms, and feeding it seconds (or converting
 * twice) is the fork's "20,728 days ago" bug (fix commit 3f8c0538).
 * The shapes below mirror the plugin's JSON results.
 */

import { host, relativeTime } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'

export interface ActivityConfig {
  window_hours: number
  max_rows: number
  now_next_done_enabled: boolean
  now_window_minutes: number
  include_cron: boolean
  include_command_room: boolean
  include_group_rooms: boolean
}

export interface ActivityRow {
  id: string
  kind: string
  label: string
  detail: string
  status: string
  session_id: null | string
  last_active: number
  created_at: null | number
}

export interface ActivityRail {
  enabled: boolean
  now: ActivityRow[]
  next: ActivityRow[]
  done: ActivityRow[]
}

function request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  return host.request<T>(method, params ?? {})
}

/** All activity state for the page: one load, refreshed on demand. */
export function useActivity() {
  const [config, setConfig] = useState<null | ActivityConfig>(null)
  const [rows, setRows] = useState<ActivityRow[]>([])
  const [rail, setRail] = useState<null | ActivityRail>(null)
  const [error, setError] = useState<null | string>(null)

  const refresh = useCallback(async () => {
    try {
      const [configReply, rowsReply, railReply] = await Promise.all([
        request<ActivityConfig>('plugin.activity.config'),
        request<{ rows: ActivityRow[] }>('plugin.activity.rows'),
        request<ActivityRail>('plugin.activity.rail')
      ])
      setConfig(configReply)
      setRows(rowsReply.rows)
      setRail(railReply)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return {
    config,
    rows,
    rail,
    error,
    refresh,
    /** A session row opens its session in place — the host's openSession door. */
    openRow: (row: ActivityRow) => {
      if (row.session_id) {
        void host.openSession(row.session_id)
      }
    }
  }
}

export function RailSection({ id, rows: bucket, onOpen, title }: {
  id: string
  rows: ActivityRow[]
  onOpen: (row: ActivityRow) => void
  title: string
}) {
  return (
    <section aria-label={title} data-rail-section={id} data-testid={`rail-${id}`}>
      <h2>{title}</h2>
      {bucket.length === 0 && <p>Nothing {id}.</p>}
      {bucket.map(row => (
        <ActivityRowView key={row.id} onOpen={onOpen} row={row} />
      ))}
    </section>
  )
}

export function ActivityRowView({ onOpen, row }: { onOpen: (row: ActivityRow) => void; row: ActivityRow }) {
  // last_active arrives as integer UNIX SECONDS; relativeTime takes ms. This
  // multiplication is the ONLY seconds→ms conversion in the chain.
  const when = relativeTime(row.last_active * 1000)

  return (
    <button
      data-activity-row={row.id}
      data-testid="activity-row"
      onClick={() => onOpen(row)}
      type="button"
    >
      <span data-activity-status={row.status} data-testid="activity-row-status">{row.status}</span>
      <span>
        <span data-testid="activity-row-label">{row.label}</span>
        {row.detail ? <span data-testid="activity-row-detail">{row.detail}</span> : null}
      </span>
      <span data-testid="activity-row-time">{when}</span>
    </button>
  )
}

export function Activity() {
  const view = useActivity()
  const railOn = Boolean(view.config?.now_next_done_enabled) && view.rail?.enabled === true

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3" data-activity-root="">
      {view.error && <div role="alert">{view.error}</div>}
      <div>
        <button onClick={() => view.refresh()} type="button">Refresh</button>
      </div>
      {railOn && (
        <section aria-label="Bots" data-testid="activity-rail">
          <RailSection id="now" onOpen={view.openRow} rows={view.rail?.now ?? []} title="Now" />
          <RailSection id="next" onOpen={view.openRow} rows={view.rail?.next ?? []} title="Next" />
          <RailSection id="done" onOpen={view.openRow} rows={view.rail?.done ?? []} title="Done" />
        </section>
      )}
      <section aria-label="Recent activity" data-testid="activity-rows">
        <h2>Activity</h2>
        {view.rows.length === 0 && !view.error && <p>Nothing happening yet.</p>}
        {view.rows.map(row => (
          <ActivityRowView key={row.id} onOpen={view.openRow} row={row} />
        ))}
      </section>
    </div>
  )
}
