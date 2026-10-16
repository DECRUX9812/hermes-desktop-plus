/**
 * The browser host's Bot Room / 3D Pane bridges.
 *
 * Two bridge instances stand in for the two tabs the host opens: the main
 * renderer and the popup surface. They speak over the real `BroadcastChannel`
 * — jsdom has no implementation, so this file installs Node's (the same
 * WHATWG one the browser provides) instead of pretending a channel away.
 */

import { BroadcastChannel as NodeBroadcastChannel } from 'node:worker_threads'

import type { PaneControl, PaneState } from '@/app/pane3d/protocol'
import { dispatchBotRoomControl } from '@/store/botroom'
import type { BotRoomBot, BotRoomControl, BotRoomStatePayload } from '@/store/botroom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  BOT_ROOM_CHANNEL,
  PANE3D_CHANNEL,
  createBrowserBotRoomBridge,
  createBrowserBotRoomMascotBridge,
  createBrowserPane3dBridge
} from './browser-window-bridges'

/**
 * `BroadcastChannel` delivery is a task, never a microtask — and it is not
 * ORDERED against a `setTimeout(0)`, so on a loaded event loop (this file runs
 * inside the whole `src/lib` batch) the timer can fire before the frame lands.
 * Spin the loop a few turns so the assertions below read a settled channel.
 */
const tick = async (): Promise<void> => {
  for (let turn = 0; turn < 8; turn += 1) {
    await new Promise(resolve => setTimeout(resolve, 0))
  }
}

const DEMO_BOT = { id: 'profile:demo-bot', name: 'demo-bot', status: 'idle' as const }
const DEMO_ROW = { ...DEMO_BOT, displayName: 'Demo Bot', status: 'working' as const }

/** A `?win=` document: the mascot/pill tabs read the roster off the channel. */
const asWindowDocument = (win: string): void => {
  window.history.replaceState(null, '', `?win=${win}`)
}

beforeEach(() => {
  vi.stubGlobal('BroadcastChannel', NodeBroadcastChannel)
})

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

describe('bot room channel', () => {
  it('carries pushed roster state to the overlay and overlay control back to the renderer', async () => {
    const renderer = createBrowserBotRoomBridge()
    const overlay = createBrowserBotRoomBridge()
    const states: BotRoomStatePayload[] = []
    const controls: BotRoomControl[] = []

    overlay.onState(state => states.push(state))
    renderer.onControl(control => controls.push(control))

    renderer.pushState({ bots: [DEMO_BOT], type: 'bots' })
    overlay.control({ type: 'ready' })
    await tick()

    expect(states).toEqual([{ bots: [DEMO_BOT], type: 'bots' }])
    expect(controls).toEqual([{ type: 'ready' }])
  })

  it('answers a late ready with a full init snapshot', async () => {
    const renderer = createBrowserBotRoomBridge()
    const overlay = createBrowserBotRoomBridge()
    const states: BotRoomStatePayload[] = []
    const rooms = [{ id: 'room:demo', memberBotIds: ['profile:demo-bot'], name: 'Demo room' }]

    // The renderer side mirrors `store/botroom.ts`'s `ready` branch: answer the
    // overlay's mount handshake with everything the controller holds.
    renderer.onControl(control => {
      if (control.type === 'ready') {
        renderer.pushState({ bots: [DEMO_BOT], enabled: true, rooms, type: 'init' })
      }
    })
    overlay.onState(state => states.push(state))

    overlay.control({ type: 'ready' })
    await tick()

    expect(states).toEqual([{ bots: [DEMO_BOT], enabled: true, rooms, type: 'init' }])
  })

  it('drops every message that does not match the channel schema', async () => {
    const renderer = createBrowserBotRoomBridge()
    const overlay = createBrowserBotRoomBridge()
    const states: BotRoomStatePayload[] = []
    const controls: BotRoomControl[] = []

    overlay.onState(state => states.push(state))
    renderer.onControl(control => controls.push(control))

    // A real same-origin writer, posting the shapes a broken or hostile tab could.
    const writer = new NodeBroadcastChannel(BOT_ROOM_CHANNEL)

    writer.postMessage({ hermesWindowChannel: 1, kind: 'state', payload: { type: 'evil' } })
    writer.postMessage({ bots: [DEMO_BOT], type: 'bots' })
    writer.postMessage({ hermesWindowChannel: 9, kind: 'state', payload: { bots: [DEMO_BOT], type: 'bots' } })
    writer.postMessage({ hermesWindowChannel: 1, kind: 'state', payload: { bots: [{ id: 7 }], type: 'bots' } })
    writer.postMessage({ hermesWindowChannel: 1, kind: 'control', payload: { type: 'task' } })
    writer.postMessage({ hermesWindowChannel: 1, kind: 'control', payload: { text: 'hi', type: 'task', x: 1 } })
    await tick()

    expect(states).toEqual([])
    expect(controls).toEqual([])
    writer.close()
  })

  it('reports a blocked popup instead of opening the overlay', async () => {
    const renderer = createBrowserBotRoomBridge()

    // jsdom's `window.open` is unimplemented and answers with no handle, which
    // is exactly what a browser popup blocker does in production.
    await expect(renderer.open()).resolves.toEqual({ ok: false, reason: 'popup-blocked' })
    await expect(renderer.close()).resolves.toEqual({ ok: true })
  })
})

describe('3D Pane channel', () => {
  it('holds state until the pane announces ready, then sends init and the queue', async () => {
    const renderer = createBrowserPane3dBridge()
    const pane = createBrowserPane3dBridge()
    const states: PaneState[] = []

    pane.onState(state => states.push(state))

    await renderer.playDemo('launch')
    await renderer.summon('muse')
    await tick()
    expect(states).toEqual([])

    await pane.control({ type: 'ready' })
    await tick()

    expect(states.map(state => state.type)).toEqual(['init', 'demo'])
    expect(states[0]).toMatchObject({
      anchor: { kind: 'desktop', label: 'Browser tab' },
      platform: 'linux',
      reducedMotion: false,
      type: 'init'
    })
    expect(states[1]).toEqual({ script: 'launch', type: 'demo' })
  })

  it('refuses the OS-level page context and reports a blocked pane window', async () => {
    const pane = createBrowserPane3dBridge()

    await expect(pane.captureContext()).rejects.toThrow('3D Pane page context is not available in the browser-hosted Desktop')
    await expect(pane.open()).resolves.toEqual({ ok: false, reason: 'popup-blocked' })
    await expect(pane.isOpen()).resolves.toBe(false)
  })

  it('closes the pane window on its close control and drops native-only controls', async () => {
    const renderer = createBrowserPane3dBridge()
    const pane = createBrowserPane3dBridge()
    const controls: PaneControl[] = []

    renderer.onControl(control => controls.push(control))

    await pane.control({ type: 'ignore-mouse', ignore: true })
    await pane.control({ type: 'hit-regions', regions: [] })
    await pane.control({ type: 'ready' })
    await tick()
    expect(controls).toEqual([])

    await pane.control({ type: 'close' })
    await tick()

    expect(controls).toEqual([{ type: 'close' }])
  })

  it('uses a pane channel name that cannot be confused with the bot room', () => {
    expect(PANE3D_CHANNEL).not.toBe(BOT_ROOM_CHANNEL)
  })
})

describe('bot mascot channels', () => {
  it('hands a mascot/pill tab the roster and action frames the renderer posts', async () => {
    asWindowDocument('botroom-mascot')

    const mascot = createBrowserBotRoomMascotBridge()
    const renderer = createBrowserBotRoomBridge()
    const rows: BotRoomBot[] = []
    const actions: Array<{ action: string; botId: string }> = []

    mascot.onState(row => rows.push(row))
    mascot.onAction(action => actions.push(action))

    renderer.pushState({ bots: [DEMO_ROW], type: 'bots' })
    renderer.pushState({ bot: { ...DEMO_ROW, status: 'idle' }, type: 'bot.status' })
    renderer.pushState({ action: 'dance', botId: DEMO_ROW.id, type: 'bot.action' })
    // Not a mascot row: the overlay's own room traffic must not reach them.
    renderer.pushState({ rooms: [], type: 'rooms' })
    await tick()

    expect(rows).toEqual([DEMO_ROW, { ...DEMO_ROW, status: 'idle' }])
    expect(actions).toEqual([{ action: 'dance', botId: DEMO_ROW.id }])
    // A tab has no OS drag region: the page layer moves its own slot instead.
    expect(mascot.drag({ botId: DEMO_ROW.id, phase: 'move', x: 10, y: 20 })).toBeUndefined()
  })

  it('drops mascot frames that do not match the channel schema', async () => {
    asWindowDocument('botroom-pill')

    const mascot = createBrowserBotRoomMascotBridge()
    const rows: BotRoomBot[] = []
    const actions: Array<{ action: string; botId: string }> = []

    mascot.onState(row => rows.push(row))
    mascot.onAction(action => actions.push(action))

    // A real same-origin writer, posting the shapes a broken tab could.
    const writer = new NodeBroadcastChannel(BOT_ROOM_CHANNEL)

    writer.postMessage({ hermesWindowChannel: 1, kind: 'state', payload: { bots: [{ id: 7 }], type: 'bots' } })
    writer.postMessage({
      hermesWindowChannel: 1,
      kind: 'state',
      payload: { action: 'dance', botId: DEMO_BOT.id, type: 'bot.action', x: 1 }
    })
    writer.postMessage({ hermesWindowChannel: 2, kind: 'state', payload: { bots: [DEMO_ROW], type: 'bots' } })
    await tick()

    expect(rows).toEqual([])
    expect(actions).toEqual([])
    writer.close()
  })

  it('feeds the main renderer from the store it shares, and still pushes to the windows', () => {
    const pushed: BotRoomStatePayload[] = []

    // The main renderer's host contract, hand-built the way `store/pane3d.test.ts`
    // builds the pane's: the store's own `push` is the thing under test.
    window.hermesDesktop = {
      botroom: {
        close: async () => ({ ok: true }),
        control: () => undefined,
        onControl: () => () => undefined,
        onState: () => () => undefined,
        open: async () => ({ ok: true }),
        pushState: (payload: BotRoomStatePayload) => pushed.push(payload),
        setFocusable: () => undefined,
        setIgnoreMouse: () => undefined
      }
    } as unknown as Window['hermesDesktop']

    const mascot = createBrowserBotRoomMascotBridge()
    const actions: Array<{ action: string; botId: string }> = []

    mascot.onAction(action => actions.push(action))

    // The store's own verb path — the overlay's `mascot.perform` relay, which
    // pushes `bot.action` to every window. The in-page layer is this same
    // document, so it hears it without a channel round trip.
    dispatchBotRoomControl({ action: 'dance', botId: DEMO_BOT.id, type: 'mascot.action' })

    expect(actions).toEqual([{ action: 'dance', botId: DEMO_BOT.id }])
    expect(pushed).toEqual([{ action: 'dance', botId: DEMO_BOT.id, type: 'bot.action' }])
  })
})
