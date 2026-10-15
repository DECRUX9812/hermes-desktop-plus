/**
 * Same-origin window channels for the browser host.
 *
 * Several Desktop surfaces are separate OS windows the main renderer drives
 * over Electron IPC: the Bot Room overlay, its per-bot mascot/pill windows and
 * the 3D Pane. The browser host has no main process and no native overlay, so a
 * second tab stands in for the window and the same typed push/control traffic
 * travels a same-origin `BroadcastChannel` instead. The sidecar's auth cookie
 * already covers that tab, and only a tagged payload that passes this channel's
 * schema is ever handed to a listener — nothing is eval'd or forwarded blind.
 */

const ENVELOPE_KEY = 'hermesWindowChannel'
const ENVELOPE_VERSION = 1

/** One payload tag's guard: the key is the `type` tag, the guard checks fields. */
export type WindowMessageGuard = (payload: Record<string, unknown>) => boolean

export interface WindowChannel<State extends { type: string }, Control extends { type: string }> {
  postState: (state: State) => void
  postControl: (control: Control) => void
  onState: (listener: (state: State) => void) => () => void
  onControl: (listener: (control: Control) => void) => () => void
}

export interface WindowChannelOptions<State extends { type: string }, Control extends { type: string }> {
  /** One name per window kind, so two kinds never read each other's traffic. */
  name: string
  /** Guards for every message tag the window accepts; anything else is dropped. */
  controlGuards: Record<Control['type'], WindowMessageGuard>
  stateGuards: Record<State['type'], WindowMessageGuard>
}

function guarded<Message extends { type: string }>(
  guards: Record<string, WindowMessageGuard>,
  value: unknown
): Message | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }

  const payload = value as Record<string, unknown>
  const tag = payload.type

  return typeof tag === 'string' && guards[tag]?.(payload) ? (payload as unknown as Message) : null
}

export function createWindowChannel<
  State extends { type: string },
  Control extends { type: string }
>(options: WindowChannelOptions<State, Control>): WindowChannel<State, Control> {
  const stateListeners = new Set<(state: State) => void>()
  const controlListeners = new Set<(control: Control) => void>()
  let bus: BroadcastChannel | null | undefined

  const receive = (event: MessageEvent): void => {
    const envelope = event.data as Record<string, unknown> | null

    if (!envelope || typeof envelope !== 'object' || envelope[ENVELOPE_KEY] !== ENVELOPE_VERSION) {
      return
    }

    if (envelope.kind === 'state') {
      const state = guarded<State>(options.stateGuards, envelope.payload)

      if (state) {
        stateListeners.forEach(listener => listener(state))
      }
    } else if (envelope.kind === 'control') {
      const control = guarded<Control>(options.controlGuards, envelope.payload)

      if (control) {
        controlListeners.forEach(listener => listener(control))
      }
    }
  }

  // Dialed on first use so a context that never pushes or listens (or a host
  // without BroadcastChannel, such as jsdom) pays nothing and cannot fail to boot.
  const channel = (): BroadcastChannel | null => {
    if (bus === undefined) {
      bus = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(options.name)

      if (bus) {
        bus.onmessage = receive
      }
    }

    return bus
  }

  const post = (kind: 'control' | 'state', payload: unknown): void => {
    channel()?.postMessage({ [ENVELOPE_KEY]: ENVELOPE_VERSION, kind, payload })
  }

  const subscribe = <Listener>(listeners: Set<Listener>) => (listener: Listener): (() => void) => {
    listeners.add(listener)
    // Subscribing is what makes this context reachable, so the bus comes up here.
    channel()

    return () => listeners.delete(listener)
  }

  return {
    onControl: subscribe(controlListeners),
    onState: subscribe(stateListeners),
    postControl: control => post('control', control),
    postState: state => post('state', state)
  }
}

/** What `open()` answers: `reason` names why a browser refused to open the window. */
export interface WindowOpenResult {
  ok: boolean
  reason?: string
}

export interface BrowserPopupWindow {
  close: () => Promise<WindowOpenResult>
  /** The live handle, for the few facts (viewport) only its own window knows. */
  handle: () => Window | null
  isOpen: () => boolean
  open: () => Promise<WindowOpenResult>
}

/**
 * The same-origin URL for one `?win=` surface: this page's base path, its
 * `profile=`/`demo=` params (so the window boots exactly like its opener), and
 * the surface's own params.
 */
export function windowSurfaceTarget(win: string, query?: Record<string, string>): string {
  const target = new URL(window.location.href)

  target.searchParams.set('win', win)
  // `watch` belongs to one session window's live view, never to an overlay.
  target.searchParams.delete('watch')

  for (const [key, value] of Object.entries(query ?? {})) {
    target.searchParams.set(key, value)
  }

  target.hash = ''

  return target.href
}

/**
 * The browser stand-in for one native overlay window: a same-origin popup the
 * renderer keeps a handle to, so a second `open()` re-focuses that tab instead
 * of stacking another. `open()` must run inside the caller's user gesture; a
 * blocked popup is reported, never silently swallowed.
 */
export function createBrowserPopupWindow(options: {
  /** Stable browsing-context name, which is also how a re-open finds the tab. */
  name: string
  /** Extra query params the surface needs (`bot`, `name`, `color`). */
  query?: Record<string, string>
  size?: { height: number; width: number }
  /** The `?win=` surface this window shows. */
  win: string
}): BrowserPopupWindow {
  const size = options.size ?? { height: 800, width: 1280 }
  let opened: Window | null = null

  return {
    close: async () => {
      const current = opened

      opened = null

      if (current && !current.closed) {
        current.close()
      }

      return { ok: true }
    },
    handle: () => opened,
    isOpen: () => Boolean(opened && !opened.closed),
    open: async () => {
      const current = opened

      if (current && !current.closed) {
        current.focus()

        return { ok: true }
      }

      const target = windowSurfaceTarget(options.win, options.query)
      const features = `popup=yes,width=${size.width},height=${size.height}`
      const child = window.open(target, options.name, features)

      if (!child) {
        return { ok: false, reason: 'popup-blocked' }
      }

      opened = child

      return { ok: true }
    }
  }
}
