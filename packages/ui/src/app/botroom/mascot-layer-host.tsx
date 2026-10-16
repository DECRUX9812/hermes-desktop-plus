import { useStore } from '@nanostores/react'
import { lazy, Suspense } from 'react'

import { isBrowserHostedDesktop } from '@/lib/platform'
import { $botRoomMascotLayerVisible } from '@/store/botroom-mascot-layer'

// Lazy for the same reason the 3D Pane and the Bot Room overlay are: the mascot
// canvas is three.js, which must not reach the main window's bundle
// (VAL-PANE-007).
const BotRoomMascotLayer = lazy(async () => ({ default: (await import('./mascot-layer')).BotRoomMascotLayer }))

/**
 * The browser host's in-page bot mascot layer, where the user asked for it.
 *
 * Electron never renders this: its mascots are real always-on-top windows main
 * spawns per bot, so the layer would duplicate every one of them. In a browser
 * tab the layer is the only way those surfaces can exist, and it is the user's
 * latch (`$botRoomMascotLayerVisible`) that turns it on.
 */
export function BotRoomMascotLayerHost() {
  const visible = useStore($botRoomMascotLayerVisible)

  if (!isBrowserHostedDesktop() || !visible) {
    return null
  }

  return (
    <Suspense fallback={null}>
      <BotRoomMascotLayer />
    </Suspense>
  )
}
