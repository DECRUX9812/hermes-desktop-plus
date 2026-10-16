import type { BotRoomBot } from '@/store/botroom'

import { ensureMascotWindowStyles } from './engine/styles'
import { createBotRoomMascotView } from './mascot-view'

/**
 * Bot Room mascot window — `?win=botroom-mascot&bot=<id>&name=..&color=..`.
 *
 * One bot, one small always-on-top window, living on the desktop itself. The
 * window body is a native drag region (drag the mascot anywhere; clicks are
 * handled by the action dot + context menu so the drag region doesn't eat
 * them), and status arrives on the `hermes:botroom-mascot:state` channel.
 * Control verbs ride the shared `hermes:botroom:control` channel so the main
 * renderer's store handles them exactly like overlay controls.
 *
 * The surface itself is `mascot-view.ts`, shared with the browser host's
 * in-page layer; this file is only the window's URL-param entry point.
 */
export function mountBotRoomMascotWindow(host: HTMLElement): void {
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

  const view = createBotRoomMascotView(host, { bot })

  window.addEventListener('beforeunload', view.destroy)
}
