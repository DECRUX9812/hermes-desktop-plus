import { mascotLabel } from '@/lib/botroom-mascots'
import type { BotRoomBot } from '@/store/botroom'

export interface BotRoomPillViewOptions {
  /** The roster row to render: a window builds it from `?bot=&name=&color=`,
   *  the page layer hands over the live row. */
  bot: BotRoomBot
  /**
   * The send path. The default posts a `task` control frame, which main (or the
   * main renderer's store) routes to the bot; the page layer hands the text to
   * the store's dispatcher directly, because a BroadcastChannel never delivers
   * a frame back to the context that posted it.
   */
  onSend?: (botId: string, text: string) => void
}

export interface BotRoomPillView {
  destroy: () => void
}

function esc(s: string): string {
  const d = document.createElement('div')
  d.textContent = s

  return d.innerHTML
}

/**
 * The Bot Room task pill: the small composer that opens next to a mascot. It
 * only exists because the user explicitly opened it, so it takes the keyboard
 * (in Electron the window is focusable on click; in the page layer the input
 * focuses itself). Sends ride the shared control channel — the main renderer's
 * store routes `task` to the bot exactly like the overlay's composer does.
 *
 * The caller injects the surface CSS: a window document wants
 * `ensureMascotWindowStyles()`, the page layer `ensureMascotLayerStyles()`.
 */
export function createBotRoomPillView(host: HTMLElement, { bot, onSend }: BotRoomPillViewOptions): BotRoomPillView {
  let label = mascotLabel(bot)
  const pill = document.createElement('div')
  pill.className = 'hr-pill'
  pill.innerHTML = `
    <div class="hr-pill-head">
      <span class="hr-pill-avatar">${esc(label.slice(0, 1).toUpperCase())}</span>
      <span class="hr-pill-name">${esc(label)}</span>
      <span class="hr-pill-status" data-kind="idle"></span>
    </div>
    <form class="hr-pill-form">
      <input class="hr-pill-input" placeholder="Ask ${esc(label)}…" autocomplete="off" spellcheck="false" />
      <button class="hr-pill-send" type="submit" aria-label="Send">
        <svg viewBox="0 0 16 16" width="14" height="14"><path d="M2 8h10M8.5 3.5 13 8l-4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
    </form>`
  host.appendChild(pill)

  if (bot.color) {
    pill.style.setProperty('--hr-pill-accent', bot.color)
  }

  const input = pill.querySelector<HTMLInputElement>('.hr-pill-input')!
  const form = pill.querySelector<HTMLFormElement>('.hr-pill-form')!
  const statusEl = pill.querySelector<HTMLElement>('.hr-pill-status')!
  const nameEl = pill.querySelector<HTMLElement>('.hr-pill-name')!
  const avatarEl = pill.querySelector<HTMLElement>('.hr-pill-avatar')!
  const bridge = window.hermesDesktop

  // The window is focusable on click; the input focuses itself when shown so
  // typing starts immediately. On send/blur the OS hands the keyboard back to
  // whatever app was frontmost.
  const focusTimer = window.setTimeout(() => input.focus(), 80)

  form.addEventListener('submit', (e) => {
    e.preventDefault()

    const text = input.value.trim()

    if (!text) {
      return
    }

    if (onSend) {
      onSend(bot.id, text)
    } else {
      bridge?.botroom?.control?.({ type: 'task', botId: bot.id, text })
    }

    input.value = ''
    statusEl.dataset.kind = 'working'
    input.blur()
  })

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      input.blur()
    }
  }

  input.addEventListener('keydown', onKeyDown)

  const offState = bridge?.botroomMascot?.onState?.((row: BotRoomBot) => {
    if (!row || row.id !== bot.id) {
      return
    }

    const next = mascotLabel(row)

    if (next && next !== label) {
      label = next
      nameEl.textContent = next
      avatarEl.textContent = next.slice(0, 1).toUpperCase()
      input.placeholder = `Ask ${next}…`
    }

    statusEl.dataset.kind = row.status === 'working' ? 'working' : row.status === 'sleeping' ? 'sleeping' : 'idle'
    statusEl.title = row.statusLine ?? ''
  })

  return {
    destroy: () => {
      window.clearTimeout(focusTimer)
      offState?.()
      input.removeEventListener('keydown', onKeyDown)
      pill.remove()
    }
  }
}
