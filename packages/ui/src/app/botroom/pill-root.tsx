import type { BotRoomBot } from '@/store/botroom'

import { ensureMascotWindowStyles } from './engine/styles'
import { createBotRoomPillView } from './pill-view'

/**
 * Bot Room task pill — `?win=botroom-pill&bot=<id>&name=..&color=..`.
 *
 * The small composer that opens next to a mascot window. It only exists because
 * the user explicitly opened it, so it is a focusable panel — first click takes
 * the keyboard (acceptFirstMouse), send/blur hands it back. Sends ride the
 * shared control channel — the main renderer's store routes `task` to the bot
 * exactly like the overlay's composer does.
 *
 * The surface itself is `pill-view.ts`, shared with the browser host's in-page
 * layer; this file is only the window's URL-param entry point.
 */
export function mountBotRoomPillWindow(host: HTMLElement): void {
  const params = new URLSearchParams(location.search)
  const botId = params.get('bot') ?? 'bot'
  const botName = decodeURIComponent(params.get('name') ?? botId)
  const botColor = decodeURIComponent(params.get('color') ?? '')

  const bot: BotRoomBot = {
    color: botColor || undefined,
    displayName: botName,
    id: botId,
    name: botId,
    status: 'idle'
  }

  ensureMascotWindowStyles()

  const view = createBotRoomPillView(host, { bot })

  window.addEventListener('beforeunload', view.destroy)
}
