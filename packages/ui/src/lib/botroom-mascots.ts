/**
 * Geometry and labels for the per-bot mascot surfaces.
 *
 * Both the Electron windows and the browser host's in-page layer render the
 * same 150x165 mascot box and 320px pill, so the numbers live here instead of
 * being spelled twice — and the page layer's slot fan is pure math over the
 * viewport, which is what makes it testable without a DOM.
 */

import type { BotRoomBot } from '@/store/botroom'

export interface SlotSize {
  height: number
  width: number
}

export interface SlotPoint {
  x: number
  y: number
}

/** The mascot window's own size (`botroom-mascot-window.ts`). */
export const MASCOT_SLOT: SlotSize = { height: 165, width: 150 }

/** The pill window's own size (`botroom-pill-window.ts`), used for the flip. */
export const PILL_SLOT: SlotSize = { height: 132, width: 320 }

/** Distance from the layer's edges, and between two mascot slots. */
const MARGIN = 16
const GAP = 8
/** The pill window's own clearances (`botroom-pill-window.ts`). */
const PILL_MARGIN = 8
const PILL_GAP = 10

/** The label a mascot shows — the roster's own rule, so a window built from URL
 *  params and a page-layer slot on a live row read the same. */
export function mascotLabel(bot: BotRoomBot): string {
  return bot.displayName?.trim() || bot.name
}

/**
 * Where the nth mascot sits in the page layer: a fan up the right edge (the
 * app's own chrome, not the transcript's reading column), filling a column
 * bottom-up and starting a new column to the left when it runs out of height.
 */
export function mascotSlotPoint(index: number, viewport: SlotSize): SlotPoint {
  const step = MASCOT_SLOT.height + GAP
  const perColumn = Math.max(1, Math.floor((viewport.height - 2 * MARGIN + GAP) / step))
  const column = Math.floor(index / perColumn)
  const row = index % perColumn

  return {
    x: Math.max(MARGIN, viewport.width - MARGIN - MASCOT_SLOT.width - column * (MASCOT_SLOT.width + GAP)),
    y: Math.max(MARGIN, viewport.height - MARGIN - MASCOT_SLOT.height - row * step)
  }
}

/**
 * The pill opens centered under its mascot, fully clear of its 165px box so the
 * two surfaces never overlap, and flips above it when the viewport bottom is too
 * close — the rule `botroom-pill-window.ts` applies before it picks a position.
 */
export function pillSlotPoint(mascot: SlotPoint, viewport: SlotSize): SlotPoint {
  const below = mascot.y + MASCOT_SLOT.height + PILL_GAP
  const above = mascot.y - PILL_SLOT.height - PILL_GAP
  const y = below + PILL_SLOT.height <= viewport.height ? below : Math.max(PILL_MARGIN, above)

  return {
    x: Math.max(
      PILL_MARGIN,
      Math.min(mascot.x - PILL_SLOT.width / 2 + MASCOT_SLOT.width / 2, viewport.width - PILL_SLOT.width - PILL_MARGIN)
    ),
    y
  }
}
