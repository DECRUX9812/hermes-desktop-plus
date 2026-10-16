import { describe, expect, it } from 'vitest'

import { MASCOT_SLOT, mascotLabel, mascotSlotPoint, PILL_SLOT, pillSlotPoint } from './botroom-mascots'

const bot = (over: Partial<Parameters<typeof mascotLabel>[0]> = {}) => ({
  id: 'profile:demo',
  name: 'demo',
  status: 'idle' as const,
  ...over
})

describe('mascot label', () => {
  it('falls back to the profile name when the display name is missing or blank', () => {
    // `profiles.list` sends "" for a profile with no display name, so the
    // fallback has to be on emptiness — the roster's own rule.
    expect(mascotLabel(bot())).toBe('demo')
    expect(mascotLabel(bot({ displayName: '' }))).toBe('demo')
    expect(mascotLabel(bot({ displayName: '   ' }))).toBe('demo')
    expect(mascotLabel(bot({ displayName: 'Demo Bot' }))).toBe('Demo Bot')
  })
})

describe('page-layer slots', () => {
  const viewport = { height: 900, width: 1440 }

  it('puts the first mascot in the bottom-right corner', () => {
    expect(mascotSlotPoint(0, viewport)).toEqual({
      x: viewport.width - 16 - MASCOT_SLOT.width,
      y: viewport.height - 16 - MASCOT_SLOT.height
    })
  })

  it('stacks up the column and starts a new one when the height runs out', () => {
    // 900 - 2*16 + 8 = 876 fits five 165px slots with an 8px gap (865).
    const fourth = mascotSlotPoint(4, viewport)
    const first = mascotSlotPoint(0, viewport)

    expect(fourth.y).toBe(first.y - 4 * (MASCOT_SLOT.height + 8))
    expect(fourth.x).toBe(first.x)

    const sixth = mascotSlotPoint(5, viewport)

    expect(sixth.y).toBe(first.y)
    expect(sixth.x).toBe(first.x - (MASCOT_SLOT.width + 8))
  })

  it('keeps a slot on-screen when the viewport is shorter than one column step', () => {
    const short = { height: 200, width: 800 }

    expect(mascotSlotPoint(0, short)).toEqual({ x: 800 - 16 - MASCOT_SLOT.width, y: 200 - 16 - MASCOT_SLOT.height })
    expect(mascotSlotPoint(1, short)).toEqual({
      x: 800 - 16 - MASCOT_SLOT.width - (MASCOT_SLOT.width + 8),
      y: 200 - 16 - MASCOT_SLOT.height
    })
  })
})

describe('pill slot', () => {
  it('opens under its mascot, centered on it', () => {
    const mascot = { x: 400, y: 100 }
    const pill = pillSlotPoint(mascot, { height: 900, width: 1440 })

    expect(pill.y).toBe(mascot.y + MASCOT_SLOT.height + 10)
    expect(pill.x).toBe(mascot.x - PILL_SLOT.width / 2 + MASCOT_SLOT.width / 2)
  })

  it('flips above the mascot when the bottom is too close, and clamps to the edges', () => {
    const corner = mascotSlotPoint(0, { height: 900, width: 1440 })
    const pill = pillSlotPoint(corner, { height: 900, width: 1440 })

    expect(pill.y).toBe(corner.y - PILL_SLOT.height - 10)
    expect(pill.x).toBe(1440 - PILL_SLOT.width - 8)

    expect(pillSlotPoint({ x: 0, y: 700 }, { height: 900, width: 400 }).x).toBe(8)
  })
})
