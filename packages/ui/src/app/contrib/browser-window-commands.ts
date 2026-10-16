/**
 * Command-palette doors for the browser host's window-backed surfaces.
 *
 * Two feat/hermes-next surfaces are separate OS windows in Electron: the 3D
 * Pane (`?win=pane3d`) and the per-bot mascots plus their task pill. Both are
 * opened by the MAIN PROCESS — `apps/desktop/electron/pane3d-ipc.ts` registers
 * `hermes:pane3d:open`, and main spawns one mascot window per roster row
 * (`botroom-mascots.ts`) — so the shared renderer has no user gesture to mirror,
 * and there is no Electron-side row to keep in step.
 *
 * The browser host can render both (a same-origin popup tab for the pane, an
 * in-page layer for the mascots). These rows are its doors, registered only
 * when this renderer IS the browser host, so Electron's palette is unchanged.
 */

import { PALETTE_AREA, type PaletteContribution, paletteToggle } from '@/app/command-palette/contrib'
import { registry } from '@/contrib/registry'
import { Cpu, Layers3 } from '@/lib/icons'
import { isBrowserHostedDesktop } from '@/lib/platform'
import { $botRoomMascotLayerVisible, setBotRoomMascotLayerVisible } from '@/store/botroom-mascot-layer'
import { $pane3dOpen, openPane3d } from '@/store/pane3d'

export function registerBrowserWindowCommands(): void {
  if (!isBrowserHostedDesktop()) {
    return
  }

  registry.registerMany([
    {
      id: 'view.pane3d',
      area: PALETTE_AREA,
      data: {
        id: 'view.pane3d',
        label: 'Open 3D Pane',
        icon: Layers3,
        keywords: ['3d', 'pane', 'avatars', 'desktop', 'window', 'overlay'],
        // The pane tab reports its own close over the pane channel, so this
        // reads the live state rather than what the row last did.
        detail: () => ($pane3dOpen.get() ? 'open' : 'closed'),
        detailVariant: 'state',
        run: () => void openPane3d()
      } satisfies PaletteContribution
    },
    paletteToggle({
      id: 'botroom.mascots',
      label: 'Bot mascots over the app',
      icon: Cpu,
      keywords: ['bot', 'bots', 'mascot', 'mascots', 'botroom', 'bot room', 'desktop', 'overlay', 'float'],
      get: () => $botRoomMascotLayerVisible.get(),
      set: setBotRoomMascotLayerVisible
    })
  ])
}
