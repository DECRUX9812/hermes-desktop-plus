import { MASCOT_SLOT, mascotLabel } from '@/lib/botroom-mascots'
import type { BotRoomBot } from '@/store/botroom'

import { type MascotAction, OverlayScene } from './engine/mascot3d'

export interface BotRoomMascotViewOptions {
  /** The roster row to render: a window builds it from `?bot=&name=&color=`,
   *  the page layer hands over the live row. */
  bot: BotRoomBot
  /**
   * Pointer drag, in client coordinates. A native window lets the OS move the
   * window (the default — main receives screen-space positions); the page layer
   * moves its own slot.
   */
  onDrag?: (phase: 'start' | 'move' | 'end', x: number, y: number) => void
  /** The action dot's request. The default asks main for the pill window; the
   *  page layer opens its own pill over the app. */
  onOpenPill?: (botId: string) => void
}

export interface BotRoomMascotView {
  destroy: () => void
}

function esc(s: string): string {
  const d = document.createElement('div')
  d.textContent = s

  return d.innerHTML
}

/**
 * One bot mascot surface: the small always-on-top panel that lives on the
 * desktop in Electron (`?win=botroom-mascot`) and the same box inside the
 * browser host's page layer. Status arrives on the `botroomMascot` channel;
 * control verbs ride the shared bot-room control channel.
 *
 * The caller injects the surface CSS: a window document wants
 * `ensureMascotWindowStyles()`, the page layer `ensureMascotLayerStyles()`.
 */
export function createBotRoomMascotView(
  host: HTMLElement,
  { bot, onDrag, onOpenPill }: BotRoomMascotViewOptions
): BotRoomMascotView {
  const label = mascotLabel(bot)
  const stage = document.createElement('div')
  stage.className = 'hr-mw'
  stage.innerHTML = `
    <canvas class="hr-mw-canvas" width="${MASCOT_SLOT.width}" height="${MASCOT_SLOT.height}"></canvas>
    <div class="hr-mw-status" data-kind="idle"></div>
    <button class="hr-mw-dot" aria-label="Ask ${esc(label)}"></button>
    <div class="hr-mw-name">${esc(label)}</div>
    <div class="hr-mw-menu">
      <button data-act="task">Give task</button>
      <button data-act="dance">Dance</button>
      <button data-act="spin">Spin</button>
      <button data-act="celebrate">Celebrate</button>
      <button data-act="sleep">Sleep / wake</button>
    </div>`
  host.appendChild(stage)

  if (bot.color) {
    stage.style.setProperty('--hr-mw-accent', bot.color)
  }

  const canvas = stage.querySelector<HTMLCanvasElement>('.hr-mw-canvas')!
  const statusEl = stage.querySelector<HTMLElement>('.hr-mw-status')!
  const dot = stage.querySelector<HTMLButtonElement>('.hr-mw-dot')!
  const nameEl = stage.querySelector<HTMLElement>('.hr-mw-name')!
  const menu = stage.querySelector<HTMLElement>('.hr-mw-menu')!
  // A page-layer slot has no window to measure, so the scene's world is the box
  // itself — the framing a mascot window gets from its own 150x165 viewport.
  const scene = new OverlayScene(canvas, () => MASCOT_SLOT)

  scene.add(bot.id, label, MASCOT_SLOT.width / 2, MASCOT_SLOT.height / 2 + 12)

  const bridge = window.hermesDesktop

  const requestPill = () => {
    if (onOpenPill) {
      onOpenPill(bot.id)
    } else {
      bridge?.botroom?.control?.({ type: 'open-pill', botId: bot.id })
    }
  }

  // The action dot is the one non-drag pixel — opens the task pill anchored at
  // this mascot. Everything else on the window body drags.
  dot.addEventListener('click', (e) => {
    e.stopPropagation()
    requestPill()
  })

  stage.addEventListener('contextmenu', (e) => {
    e.preventDefault()
    menu.classList.toggle('open')
  })

  menu.addEventListener('click', (e) => {
    const act = (e.target as HTMLElement).getAttribute('data-act')

    if (!act) {
      return
    }

    menu.classList.remove('open')

    const mascot = scene.get(bot.id)

    if (act === 'task') {
      requestPill()
    } else if (act === 'sleep') {
      mascot?.setWorking(false)
      mascot?.play('sleep', 1.4)
    } else if (act === 'dance' || act === 'spin' || act === 'celebrate') {
      mascot?.play(act as MascotAction, 1.2)
    }
  })

  const onDocumentClick = (e: MouseEvent) => {
    if (!menu.contains(e.target as Node)) {
      menu.classList.remove('open')
    }
  }

  document.addEventListener('click', onDocumentClick)

  // Drag: the body captures the pointer. A window streams screen-space
  // positions to main, which repositions the native window (an app-region drag
  // doesn't engage on a non-activating panel); a page-layer slot moves itself
  // from the client-space stream. The dot/menu are skipped so clicks reach them.
  let dragging = false

  const drag = (phase: 'start' | 'move' | 'end', e: PointerEvent) => {
    if (onDrag) {
      onDrag(phase, e.clientX, e.clientY)
    } else {
      bridge?.botroomMascot?.drag?.({ botId: bot.id, phase, x: e.screenX, y: e.screenY })
    }
  }

  const onPointerDown = (e: PointerEvent) => {
    const target = e.target as HTMLElement

    if (target.closest('.hr-mw-dot, .hr-mw-menu')) {
      return
    }

    dragging = true
    drag('start', e)
    e.preventDefault()
  }

  const onPointerMove = (e: PointerEvent) => {
    if (dragging) {
      drag('move', e)
    }
  }

  const onPointerEnd = (e: PointerEvent) => {
    if (dragging) {
      dragging = false
      drag('end', e)
    }
  }

  stage.addEventListener('pointerdown', onPointerDown)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerEnd)
  window.addEventListener('pointercancel', onPointerEnd)

  const onEnter = () => nameEl.classList.add('show')

  const onLeave = () => {
    nameEl.classList.remove('show')
    menu.classList.remove('open')
  }

  stage.addEventListener('mouseenter', onEnter)
  stage.addEventListener('mouseleave', onLeave)

  // Roster state pushed over the botroomMascot channel (roster sync /
  // bot.status) — the same row a mascot window receives from main.
  const offState = bridge?.botroomMascot?.onState?.((row: BotRoomBot) => {
    if (!row || row.id !== bot.id) {
      return
    }

    const next = mascotLabel(row)

    if (next) {
      nameEl.textContent = next
      dot.title = `Ask ${next}`
    }

    const kind = row.status === 'working' ? 'working' : row.status === 'sleeping' ? 'sleeping' : 'idle'
    statusEl.dataset.kind = kind
    statusEl.title = row.statusLine ?? kind

    const mascot = scene.get(bot.id)

    if (mascot) {
      mascot.setWorking(row.status === 'working')
    }
  })

  // Commanded performances — 'bot.action' pushes from the store (tasks landing,
  // room relays, scripts). Same verb set the extension uses.
  const offAction = bridge?.botroomMascot?.onAction?.((payload: { botId: string; action: string }) => {
    if (!payload || payload.botId !== bot.id) {
      return
    }

    const mascot = scene.get(bot.id)
    const action = payload.action as MascotAction

    if (action === 'point') {
      // Point straight ahead — the canvas has no element targets the way the
      // extension's page DOM does, so point reads as "attention here".
      mascot?.pointAt?.(MASCOT_SLOT.width * 0.62, MASCOT_SLOT.height * 0.35)
    } else {
      mascot?.play(action, 1.4)
    }
  })

  return {
    destroy: () => {
      offState?.()
      offAction?.()
      stage.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerEnd)
      window.removeEventListener('pointercancel', onPointerEnd)
      stage.removeEventListener('mouseenter', onEnter)
      stage.removeEventListener('mouseleave', onLeave)
      document.removeEventListener('click', onDocumentClick)
      scene.dispose()
      stage.remove()
    }
  }
}
