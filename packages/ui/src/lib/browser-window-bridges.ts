/**
 * Browser-host implementations of the two window-driven Desktop bridges.
 *
 * Both are puppets in Electron: the main renderer owns the state and pushes it
 * to a separate window, which sends user actions back. The browser host keeps
 * that contract but swaps the IPC transport for a same-origin
 * `BroadcastChannel` (see `browser-window-channel.ts`) and the native window for
 * a popup tab. Members that only make sense for a native window (click-through,
 * per-pixel hit regions, OS page-context capture) stay no-op or refused, exactly
 * like the `BROWSER_BRIDGE_STUBS` they replace.
 */

import type { AvatarId, DemoScript, PaneAnchor, PaneControl, PaneState } from '@/app/pane3d/protocol'
import { AVATAR_IDS } from '@/app/pane3d/protocol'
import { prefersReducedMotion } from '@/app/pane3d/scene/reduced-motion'
import type { BotRoomBot, BotRoomControl, BotRoomRoom, BotRoomStatePayload } from '@/store/botroom'

import { browserUnsupported } from './browser-bridge-stubs'
import { createBrowserPopupWindow, createWindowChannel, type WindowMessageGuard } from './browser-window-channel'

export const BOT_ROOM_CHANNEL = 'hermes.botroom.v1'
export const PANE3D_CHANNEL = 'hermes.pane3d.v1'

const BOT_STATUSES = ['idle', 'working', 'sleeping', 'offline'] as const

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const isFlag = (value: unknown): value is boolean => typeof value === 'boolean'
const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isText = (value: unknown): value is string => typeof value === 'string'
const isTexts = (value: unknown): value is string[] => Array.isArray(value) && value.every(isText)
const isAvatar = (value: unknown): value is AvatarId => AVATAR_IDS.includes(value as AvatarId)
const isBotStatus = (value: unknown): boolean => BOT_STATUSES.includes(value as (typeof BOT_STATUSES)[number])

/**
 * A message schema: every field the tag may carry, each with its own check, and
 * which of them may be absent. A payload with a field outside the schema is not
 * a match — a stray same-origin writer cannot smuggle extra data through a
 * message the store or the stage will act on.
 */
function fields(spec: Record<string, (value: unknown) => boolean>, optional: readonly string[] = []) {
  const allowed = Object.keys(spec)

  return (value: unknown): value is Record<string, unknown> => {
    if (!isRecord(value)) {
      return false
    }

    // `type` is the tag itself — the channel already matched it to this spec.
    return (
      Object.keys(value).every(key => key === 'type' || allowed.includes(key)) &&
      allowed.every(key => (optional.includes(key) && value[key] === undefined) || spec[key](value[key]))
    )
  }
}

const isBot = fields(
  { color: isText, displayName: isText, id: isText, name: isText, status: isBotStatus, statusLine: isText },
  ['color', 'displayName', 'statusLine']
)

const isRoom = fields({ anchor: fields({ x: isNumber, y: isNumber }), id: isText, memberBotIds: isTexts, name: isText }, [
  'anchor'
])

const isBots = (value: unknown): boolean => Array.isArray(value) && value.every(isBot)
const isRooms = (value: unknown): boolean => Array.isArray(value) && value.every(isRoom)
const isRoomMsg = fields({ at: isNumber, author: isText, ephemeral: isFlag, roomId: isText, text: isText }, [
  'at',
  'ephemeral'
])

const BOT_ROOM_STATE_GUARDS = {
  'bot.action': fields({ action: isText, botId: isText }),
  'bot.status': fields({ bot: isBot }),
  'bots': fields({ bots: isBots }),
  'init': fields({ bots: isBots, enabled: isFlag, rooms: isRooms }, ['bots', 'enabled', 'rooms']),
  'room.msg': fields({ msg: isRoomMsg }),
  'rooms': fields({ rooms: isRooms })
} satisfies Record<BotRoomStatePayload['type'], WindowMessageGuard>

/** The tags the main renderer accepts back from the overlay, mascot and pill. */
const BOT_ROOM_CONTROL_GUARDS = {
  'close': fields({}),
  'mascot.action': fields({ action: isText, botId: isText }),
  'mascot.move': fields({ botId: isText, x: isNumber, y: isNumber }),
  'open-app': fields({}),
  'open-pill': fields({ botId: isText }),
  'ready': fields({}),
  'room.create': fields({ botIds: isTexts, name: isText }, ['botIds']),
  'room.move': fields({ botId: isText, roomId: isText }, ['botId']),
  'room.remove': fields({ roomId: isText }),
  'task': fields({ botId: isText, roomId: isText, text: isText }, ['botId', 'roomId'])
} satisfies Record<BotRoomControl['type'], WindowMessageGuard>

export function createBrowserBotRoomBridge(): Window['hermesDesktop']['botroom'] {
  const channel = createWindowChannel<BotRoomStatePayload, BotRoomControl>({
    controlGuards: BOT_ROOM_CONTROL_GUARDS,
    name: BOT_ROOM_CHANNEL,
    stateGuards: BOT_ROOM_STATE_GUARDS
  })
  const overlay = createBrowserPopupWindow({
    name: 'hermes-botroom',
    size: { height: 900, width: 1440 },
    win: 'botroom'
  })

  return {
    close: overlay.close,
    // A browser tab always owns the pointer and the keyboard: there is no
    // click-through layer to toggle and no always-on-top window to focus.
    control: channel.postControl,
    onControl: channel.onControl,
    onState: channel.onState,
    open: overlay.open,
    pushState: channel.postState,
    setFocusable: () => undefined,
    setIgnoreMouse: () => undefined
  }
}

const isRect = fields({ height: isNumber, width: isNumber, x: isNumber, y: isNumber })

const isAnchor = fields({ kind: isText, label: isText, rect: isRect })

const isNotifyAction = fields({ id: isText, label: isText })

const isNotifyRequest = fields(
  { action: isNotifyAction, avatar: isAvatar, body: isText, source: isText, title: isText },
  ['action', 'source']
)

const isRects = (value: unknown): boolean => Array.isArray(value) && value.every(isRect)

const PANE3D_STATE_GUARDS = {
  'anchor': fields({ anchor: isAnchor }),
  'demo': fields({ script: isText }),
  'dismiss': fields({ avatar: isAvatar }),
  'init': fields({ anchor: isAnchor, platform: isText, reducedMotion: isFlag }),
  'notify': fields({ id: isText, request: isNotifyRequest }),
  'summon': fields({ avatar: isAvatar })
} satisfies Record<PaneState['type'], WindowMessageGuard>

const PANE3D_CONTROL_GUARDS = {
  'close': fields({}),
  'focus': fields({ focusable: isFlag }),
  'hit-regions': fields({ regions: isRects }),
  'ignore-mouse': fields({ ignore: isFlag }),
  'notify.action': fields({ actionId: isText, id: isText }),
  'notify.dismissed': fields({ id: isText }),
  'open-app': fields({}),
  'ready': fields({})
} satisfies Record<PaneControl['type'], WindowMessageGuard>

/** The pane's `platform` field in Node's vocabulary; the rig reads it for its
 *  per-platform motion, and this tab is the pane's host. */
function browserPlatform(): NodeJS.Platform {
  const hint = `${navigator.platform} ${navigator.userAgent}`.toLowerCase()

  if (hint.includes('mac')) {
    return 'darwin'
  }

  if (hint.includes('win')) {
    return 'win32'
  }

  return 'linux'
}

/** A browser tab has no OS window to perch on, so the tab itself is the anchor.
 *  Its viewport is only reachable through the popup handle, and a handle that
 *  has navigated off-origin refuses that read. */
function paneAnchor(pane: Window | null): PaneAnchor {
  let height = 0
  let width = 0

  try {
    height = pane?.innerHeight ?? 0
    width = pane?.innerWidth ?? 0
  } catch {
    // Cross-origin handle: no viewport to read, so the pane keeps its defaults.
  }

  return { kind: 'desktop', label: 'Browser tab', rect: { height, width, x: 0, y: 0 } }
}

/**
 * The browser host's 3D Pane: a popup tab (`?win=pane3d`) driven over the pane
 * channel. The pane announces itself with `ready`; until then states are held,
 * exactly like the Electron relay's queue, and `init` answers that handshake.
 */
export function createBrowserPane3dBridge(): Window['hermesDesktop']['pane3d'] {
  const channel = createWindowChannel<PaneState, PaneControl>({
    controlGuards: PANE3D_CONTROL_GUARDS,
    name: PANE3D_CHANNEL,
    stateGuards: PANE3D_STATE_GUARDS
  })
  const pane = createBrowserPopupWindow({
    name: 'hermes-pane3d',
    size: { height: 900, width: 1440 },
    win: 'pane3d'
  })
  const controlListeners = new Set<(control: PaneControl) => void>()
  let pending: PaneState[] = []
  let ready = false

  const send = (state: PaneState): void => channel.postState(state)

  /** Mirrors the Electron relay: a state addressed to a closed pane opens it
   *  (when the verb allows) and waits for `ready`; summon/dismiss never open. */
  const deliver = (state: PaneState, openIfClosed: boolean): void => {
    const opened = pane.isOpen()

    if (!opened && !openIfClosed) {
      return
    }

    if (opened && ready) {
      send(state)

      return
    }

    if (!opened) {
      void pane.open()
    }

    pending.push(state)
  }

  const onControl = (listener: (control: PaneControl) => void): (() => void) => {
    controlListeners.add(listener)

    return () => controlListeners.delete(listener)
  }

  channel.onControl(control => {
    switch (control.type) {
      // The pane's own close (its UI, or the tab going away) — the host
      // renderer learns about it the way Electron's main window does.
      case 'close':
        void pane.close()
        ready = false
        pending = []
        controlListeners.forEach(listener => listener(control))

        return

      case 'notify.action':
      case 'notify.dismissed':
        controlListeners.forEach(listener => listener(control))

        return

      case 'ready': {
        ready = true
        send({
          anchor: paneAnchor(pane.handle()),
          platform: browserPlatform(),
          // The pane's own owner of the OS setting (scene/reduced-motion.ts).
          reducedMotion: prefersReducedMotion(),
          type: 'init'
        })

        for (const state of pending) {
          send(state)
        }

        pending = []

        return
      }

      // `focus`, `ignore-mouse`, `hit-regions` and `open-app` drive native
      // window behaviour (keyboard handoff, click-through, window raising)
      // that a browser tab provides for itself.
      default:
        return
    }
  })

  return {
    // This tab cannot see other apps or the OS window stack, so the pane's
    // OS-level page context stays a refusal.
    captureContext: async () => {
      throw browserUnsupported('3D Pane page context')
    },
    close: pane.close,
    control: channel.postControl,
    dismiss: async (avatar: AvatarId) => {
      deliver({ avatar, type: 'dismiss' }, false)

      return { ok: true }
    },
    isOpen: async () => pane.isOpen(),
    notify: async request => {
      const id = crypto.randomUUID()

      deliver({ id, request, type: 'notify' }, true)

      return { id, ok: true }
    },
    onControl,
    onState: channel.onState,
    open: pane.open,
    playDemo: async (script: DemoScript = 'launch') => {
      deliver({ script, type: 'demo' }, true)

      return { ok: true }
    },
    summon: async (avatar: AvatarId) => {
      deliver({ avatar, type: 'summon' }, false)

      return { ok: true }
    }
  }
}
