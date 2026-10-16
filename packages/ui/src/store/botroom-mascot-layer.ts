import { atom } from 'nanostores'

/**
 * Whether the browser host's in-page bot mascot layer is on.
 *
 * Electron never reads this: its mascots are real always-on-top windows main
 * spawns per bot. In a browser there is no such window, so the same surfaces
 * (and the task pill) float over the app instead, and this is the user's latch.
 * Session-only, like the windows: main spawns mascots per run too.
 */
export const $botRoomMascotLayerVisible = atom(false)

export function setBotRoomMascotLayerVisible(visible: boolean): void {
  $botRoomMascotLayerVisible.set(visible)
}

export function toggleBotRoomMascotLayer(): void {
  $botRoomMascotLayerVisible.set(!$botRoomMascotLayerVisible.get())
}
