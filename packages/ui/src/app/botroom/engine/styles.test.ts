/**
 * The two bundles the per-bot surfaces ship in.
 *
 * `styles.ts` used to inject ONE sheet into every document. The page layer
 * renders inside the app, where the window bundle's `:root` variables, `body`
 * reset and `user-select: none` would restyle the whole app — so the surfaces
 * are shared and the preambles are not. This pins the split, the scoping and
 * the once-per-document behaviour, because a regression here is invisible in
 * jsdom (no layout) and shows up only as a restyled app or a transparent
 * mascot window in a real browser.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

type Styles = typeof import('./styles')

/** A fresh module registry per test: the injection latch is module state. */
async function loadStyles(): Promise<Styles> {
  vi.resetModules()

  return await import('./styles')
}

const injected = (): string => [...document.querySelectorAll('head style')].map(node => node.textContent).join('\n')

beforeEach(() => {
  document.head.querySelectorAll('style').forEach(node => node.remove())
})

describe('mascot window bundle', () => {
  it('gives a standalone window its variables, its reset and the surfaces', async () => {
    const styles = await loadStyles()

    styles.ensureMascotWindowStyles()

    const css = injected()

    expect(css).toContain(':root {')
    expect(css).toContain('html, body { background: transparent; overflow: hidden; }')
    expect(css).toContain('.hr-mw {')
    expect(css).toContain('.hr-pill {')
    // The window owns its whole document, so the surfaces are unscoped.
    expect(css).not.toContain('.hr-layer {')
    expect(document.head.querySelectorAll('style')).toHaveLength(1)
  })

  it('injects once, however many surfaces ask for it', async () => {
    const styles = await loadStyles()

    styles.ensureMascotWindowStyles()
    styles.ensureMascotWindowStyles()
    styles.ensureMascotWindowStyles()

    expect(document.head.querySelectorAll('style')).toHaveLength(1)
  })
})

describe('page layer bundle', () => {
  it('scopes the surfaces to the layer and leaves the app document alone', async () => {
    const styles = await loadStyles()

    styles.ensureMascotLayerStyles()

    const css = injected()

    expect(css).toContain('.hr-layer {')
    expect(css).toContain('.hr-layer-slot')
    expect(css).toContain('.hr-layer-pill')
    expect(css).toContain('.hr-mw {')
    expect(css).toContain('.hr-pill {')

    // The whole point of the split: nothing the app itself would feel.
    expect(css).not.toContain(':root {')
    expect(css).not.toContain('html, body {')
    expect(css).not.toContain('body { font:')
    expect(css).not.toContain('* { box-sizing: border-box; margin: 0; padding: 0; }')
  })

  it('replaces the window bundle rather than stacking a second sheet', async () => {
    // Not a real document order — just the latch: whichever bundle asks first
    // wins, and a second, different ask swaps the sheet instead of adding one.
    const styles = await loadStyles()

    styles.ensureMascotWindowStyles()
    styles.ensureMascotLayerStyles()

    expect(document.head.querySelectorAll('style')).toHaveLength(1)
    expect(injected()).toContain('.hr-layer {')
  })
})
