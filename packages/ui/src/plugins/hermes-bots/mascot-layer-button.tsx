/**
 * The roster toolbar's door for the browser host's in-page bot mascot layer.
 *
 * Electron has no such layer: main spawns one always-on-top window per bot the
 * moment the renderer pushes a roster, and the Bot Room button beside this one
 * is how the user gets there. A browser tab cannot own an OS window, so the same
 * mascots (and their task pill) float over the app instead, and this is the
 * button that turns them on — the same toolbar surface, one host down.
 *
 * Renders nothing in Electron, where the layer does not exist.
 */

import { Button, cn, Codicon, host, isBrowserHostedDesktop, Tip, useValue } from '@hermes/plugin-sdk'

export function BotMascotLayerButton() {
  const on = useValue(host.state.botRoomMascotsVisible)

  if (!isBrowserHostedDesktop()) {
    return null
  }

  return (
    <Tip label={on ? 'Bot mascots over the app — on' : 'Bot mascots over the app'}>
      <Button
        aria-label="Bot mascots over the app"
        aria-pressed={on}
        className={cn(
          'rounded-md hover:text-foreground',
          on ? 'text-(--ui-text-secondary)' : 'text-(--ui-text-tertiary)'
        )}
        onClick={() => host.toggleBotRoomMascots()}
        size="icon-xs"
        variant="ghost"
      >
        <Codicon name={on ? 'eye' : 'eye-closed'} />
      </Button>
    </Tip>
  )
}
