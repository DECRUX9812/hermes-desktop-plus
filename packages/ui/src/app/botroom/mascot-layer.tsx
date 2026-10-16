import { useEffect, useRef } from 'react'

import {
  MASCOT_SLOT,
  mascotSlotPoint,
  PILL_SLOT,
  pillSlotPoint,
  type SlotPoint,
  type SlotSize
} from '@/lib/botroom-mascots'
import {
  botRoomRoster,
  dispatchBotRoomControl,
  startBotRoomFeed,
  subscribeBotRoomState,
  type BotRoomBot
} from '@/store/botroom'

import { createBotRoomMascotView, type BotRoomMascotView } from './mascot-view'
import { createBotRoomPillView, type BotRoomPillView } from './pill-view'
import { ensureMascotLayerStyles } from './engine/styles'

interface MascotSlot {
  bot: BotRoomBot
  /** Set once the user drags the mascot: the fan no longer repositions it. */
  dragged: boolean
  element: HTMLElement
  point: SlotPoint
  view: BotRoomMascotView
}

interface OpenPill {
  bot: BotRoomBot
  element: HTMLElement
  view: BotRoomPillView
}

interface Drag {
  botId: string
  origin: SlotPoint
  start: SlotPoint
}

const layerViewport = (): SlotSize => ({ height: window.innerHeight, width: window.innerWidth })

/**
 * The browser host's bot mascot layer — the same per-bot surfaces Electron puts
 * in one always-on-top window each, floated over the app instead (a browser tab
 * cannot own an OS window, and one popup per bot would fight popup blockers and
 * bury the app in tabs).
 *
 * Rows arrive through `botroomMascot`, exactly as a mascot window receives them
 * from main; membership comes from the store's own roster pushes, so the layer
 * reconciles with the same feed the Bot Room overlay is built from. The pill is
 * one at a time, like the Electron pill window: opening another bot's pill
 * retargets it.
 *
 * Deliberately not gated on the Bot Room overlay being closed (Electron hides
 * its mascot windows while the fullscreen stage is up): the overlay is a
 * separate tab here, so the two never render onto the same surface.
 */
export function BotRoomMascotLayer() {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const slotsRef = useRef(new Map<string, MascotSlot>())
  const pillRef = useRef<null | OpenPill>(null)
  const dragRef = useRef<null | Drag>(null)

  useEffect(() => {
    const host = hostRef.current

    if (!host) {
      return
    }

    // The layer's own bundle — the surfaces plus their variables scoped to
    // `.hr-layer`, so the app's theme is untouched.
    ensureMascotLayerStyles()

    const slots = slotsRef.current

    const closePill = () => {
      const open = pillRef.current

      pillRef.current = null

      if (open) {
        open.view.destroy()
        open.element.remove()
      }
    }

    const positionPill = () => {
      const open = pillRef.current
      const slot = open ? slots.get(open.bot.id) : undefined

      if (!open || !slot) {
        return
      }

      const point = pillSlotPoint(slot.point, layerViewport())

      open.element.style.left = `${point.x}px`
      open.element.style.top = `${point.y}px`
    }

    const openPill = (botId: string) => {
      const slot = slots.get(botId)

      if (!slot) {
        return
      }

      // One pill at a time — the Electron pill window retargets instead of
      // stacking another.
      closePill()

      const element = document.createElement('div')
      element.className = 'hr-layer-pill'
      element.dataset.botId = botId
      // The layer positions the pill; the pill's own box is its size, exactly
      // like a slot's (`.hr-pill` fills whatever box it is given).
      element.style.width = `${PILL_SLOT.width}px`
      element.style.height = `${PILL_SLOT.height}px`
      host.appendChild(element)

      const point = pillSlotPoint(slot.point, layerViewport())

      element.style.left = `${point.x}px`
      element.style.top = `${point.y}px`
      pillRef.current = {
        bot: slot.bot,
        element,
        view: createBotRoomPillView(element, {
          bot: slot.bot,
          // The pill's send path: the store is this same document, so the verb
          // goes to its dispatcher directly instead of over the channel (which
          // never delivers a frame back to the context that posted it).
          onSend: (id, text) => dispatchBotRoomControl({ botId: id, text, type: 'task' })
        })
      }
    }

    const placeSlot = (slot: MascotSlot, index: number) => {
      slot.point = mascotSlotPoint(index, layerViewport())
      slot.element.style.left = `${slot.point.x}px`
      slot.element.style.top = `${slot.point.y}px`
    }

    const onDrag = (botId: string) => (phase: 'start' | 'move' | 'end', x: number, y: number) => {
      if (phase === 'start') {
        const slot = slots.get(botId)

        if (slot) {
          dragRef.current = { botId, origin: { x, y }, start: slot.point }
        }

        return
      }

      const drag = dragRef.current

      if (!drag || drag.botId !== botId) {
        return
      }

      if (phase === 'end') {
        dragRef.current = null

        return
      }

      const slot = slots.get(botId)

      if (!slot) {
        return
      }

      const viewport = layerViewport()

      slot.dragged = true
      slot.point = {
        x: Math.max(0, Math.min(drag.start.x + x - drag.origin.x, viewport.width - MASCOT_SLOT.width)),
        y: Math.max(0, Math.min(drag.start.y + y - drag.origin.y, viewport.height - MASCOT_SLOT.height))
      }
      slot.element.style.left = `${slot.point.x}px`
      slot.element.style.top = `${slot.point.y}px`
      positionPill()
    }

    const mountSlot = (bot: BotRoomBot, index: number) => {
      const element = document.createElement('div')
      element.className = 'hr-layer-slot'
      element.dataset.botId = bot.id
      element.style.width = `${MASCOT_SLOT.width}px`
      element.style.height = `${MASCOT_SLOT.height}px`
      host.appendChild(element)

      const slot: MascotSlot = {
        bot,
        dragged: false,
        element,
        point: mascotSlotPoint(index, layerViewport()),
        view: createBotRoomMascotView(element, { bot, onDrag: onDrag(bot.id), onOpenPill: openPill })
      }

      element.style.left = `${slot.point.x}px`
      element.style.top = `${slot.point.y}px`
      slots.set(bot.id, slot)
    }

    const reconcile = (bots: BotRoomBot[]) => {
      const seen = new Set(bots.map(bot => bot.id))

      bots.forEach((bot, index) => {
        const slot = slots.get(bot.id)

        if (slot) {
          // Keep the row the pill opens with current; the mascot surface itself
          // is fed by the botroomMascot channel.
          slot.bot = bot

          return
        }

        mountSlot(bot, index)
      })

      for (const [id, slot] of slots) {
        if (!seen.has(id)) {
          if (pillRef.current?.bot.id === id) {
            closePill()
          }

          slot.view.destroy()
          slot.element.remove()
          slots.delete(id)
        }
      }
    }

    const onResize = () => {
      let index = 0

      for (const slot of slots.values()) {
        if (!slot.dragged) {
          placeSlot(slot, index)
        }

        index += 1
      }

      positionPill()
    }

    // Dismiss the pill the way a non-activating panel does: a pointer down
    // outside it, or Escape. The pill's input keeps Escape for its own blur (it
    // does not stop propagation), so this closes the pill too.
    const onDocumentPointerDown = (e: PointerEvent) => {
      const open = pillRef.current

      if (open && !open.element.contains(e.target as Node)) {
        closePill()
      }
    }

    const onDocumentKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        closePill()
      }
    }

    // The roster feed is the store's, shared with the Bot Room overlay's door —
    // Electron does not tie it to the mascot windows' lifetime either, so
    // turning the layer off leaves it running.
    startBotRoomFeed()
    reconcile(botRoomRoster())

    const offState = subscribeBotRoomState(payload => {
      if (payload.type === 'bots' || payload.type === 'init') {
        reconcile(payload.bots ?? [])
      }
    })

    window.addEventListener('resize', onResize)
    document.addEventListener('pointerdown', onDocumentPointerDown)
    document.addEventListener('keydown', onDocumentKeyDown)

    return () => {
      offState()
      window.removeEventListener('resize', onResize)
      document.removeEventListener('pointerdown', onDocumentPointerDown)
      document.removeEventListener('keydown', onDocumentKeyDown)
      closePill()

      for (const slot of slots.values()) {
        slot.view.destroy()
        slot.element.remove()
      }

      slots.clear()
    }
  }, [])

  return <div className="hr-layer" data-botroom-mascot-layer="" ref={hostRef} />
}
