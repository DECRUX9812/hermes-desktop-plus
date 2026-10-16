/**
 * The Bot Room task pill surface (`?win=botroom-pill`, and the browser host's
 * in-page layer). One surface, so this exercises the shared view over the real
 * `BroadcastChannel`: the roster row arrives on the botroomMascot channel and the
 * typed task leaves as a `task` control frame the store answers.
 */

import { BroadcastChannel as NodeBroadcastChannel } from 'node:worker_threads'

import type { BotRoomBot, BotRoomControl } from '@/store/botroom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createBrowserBotRoomBridge, createBrowserBotRoomMascotBridge } from '@/lib/browser-window-bridges'

import { mountBotRoomPillWindow } from './pill-root'
import { createBotRoomPillView } from './pill-view'

/**
 * `BroadcastChannel` delivery is a task, never a microtask — and it is not
 * ORDERED against a `setTimeout(0)`, so spin the loop a few turns rather than
 * racing one timer against the frame.
 */
const tick = async (): Promise<void> => {
  for (let turn = 0; turn < 8; turn += 1) {
    await new Promise(resolve => setTimeout(resolve, 0))
  }
}

const ROW: BotRoomBot = { displayName: 'Demo Bot', id: 'profile:demo-bot', name: 'demo-bot', status: 'working' }
const OPENED: BotRoomBot = { id: 'profile:demo-bot', name: 'demo-bot', status: 'idle' }

let host: HTMLElement

beforeEach(() => {
  vi.stubGlobal('BroadcastChannel', NodeBroadcastChannel)
  // The pill window's own document kind.
  window.history.replaceState(null, '', '?win=botroom-pill')
  window.hermesDesktop = {
    botroom: createBrowserBotRoomBridge(),
    botroomMascot: createBrowserBotRoomMascotBridge()
  } as unknown as Window['hermesDesktop']
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  host.remove()
})

describe('bot room pill', () => {
  it('paints the bot it was opened for, then follows the row pushed to it', async () => {
    const view = createBotRoomPillView(host, { bot: OPENED })

    expect(host.querySelector('.hr-pill-name')?.textContent).toBe('demo-bot')
    expect(host.querySelector('.hr-pill-status')?.getAttribute('data-kind')).toBe('idle')

    const renderer = createBrowserBotRoomBridge()

    renderer.pushState({ bots: [ROW], type: 'bots' })
    await tick()

    expect(host.querySelector('.hr-pill-name')?.textContent).toBe('Demo Bot')
    expect(host.querySelector('.hr-pill-status')?.getAttribute('data-kind')).toBe('working')
    expect(host.querySelector<HTMLInputElement>('.hr-pill-input')?.placeholder).toBe('Ask Demo Bot…')

    view.destroy()
    expect(host.querySelector('.hr-pill')).toBeNull()
  })

  it('sends the typed task as a control frame and clears the input', async () => {
    const view = createBotRoomPillView(host, { bot: OPENED })
    const renderer = createBrowserBotRoomBridge()
    const controls: BotRoomControl[] = []

    renderer.onControl(control => controls.push(control))

    const input = host.querySelector<HTMLInputElement>('.hr-pill-input')!

    input.value = '  ping the bot  '
    host
      .querySelector<HTMLFormElement>('.hr-pill-form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await tick()

    expect(controls).toEqual([{ botId: ROW.id, text: 'ping the bot', type: 'task' }])
    expect(input.value).toBe('')
    view.destroy()
  })

  it('sends nothing for an empty draft', async () => {
    const view = createBotRoomPillView(host, { bot: OPENED })
    const renderer = createBrowserBotRoomBridge()
    const controls: BotRoomControl[] = []

    renderer.onControl(control => controls.push(control))

    const input = host.querySelector<HTMLInputElement>('.hr-pill-input')!

    input.value = '   '
    host
      .querySelector<HTMLFormElement>('.hr-pill-form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await tick()

    expect(controls).toEqual([])
    view.destroy()
  })
})

describe('bot room pill window', () => {
  it('mounts the row from its URL params, ships the window bundle, and tears down', async () => {
    // The real `?win=` document: Electron's own pill window, and the browser
    // host's popup. Params are the only state it is given at mount.
    window.history.replaceState(
      null,
      '',
      '?win=botroom-pill&bot=profile:demo-bot&name=Demo%20Bot&color=%23ff8800'
    )

    mountBotRoomPillWindow(host)

    expect(host.querySelector('.hr-pill-name')?.textContent).toBe('Demo Bot')
    expect(host.querySelector<HTMLInputElement>('.hr-pill-input')?.placeholder).toBe('Ask Demo Bot…')
    expect(host.querySelector<HTMLElement>('.hr-pill')?.style.getPropertyValue('--hr-pill-accent')).toBe('#ff8800')

    // A window document gets its own bundle: variables on `:root`, the body
    // reset, and the surfaces — no page-layer rules.
    const css = [...document.querySelectorAll('head style')].map(node => node.textContent).join('\n')

    expect(css).toContain(':root {')
    expect(css).toContain('body { font:')
    expect(css).toContain('.hr-pill {')
    expect(css).not.toContain('.hr-layer {')

    window.dispatchEvent(new Event('beforeunload'))
    expect(host.querySelector('.hr-pill')).toBeNull()
  })
})
