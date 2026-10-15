/**
 * Minimal ambient `electron` module so the renderer typechecks the handful of
 * electron/* type files it imports (see ../electron/, synced from the fork).
 * The browser bundle never loads Electron; these declarations exist only so
 * `tsc -p tsconfig.json` can resolve the module without the 100 MB package.
 *
 * `Session`, `WebContents` and `BrowserWindow` are typed to the exact surface
 * challenge-window.ts exercises (permission guards, navigation listeners,
 * window lifecycle), so its callbacks are contextually typed instead of
 * implicitly `any`. Everything else the synced electron files touch stays
 * `any` on purpose: this file mirrors only what the renderer typechecks, not
 * Electron's API. If a synced electron file starts using more of that API,
 * widen the matching interface here rather than reverting to `any`.
 */
declare module 'electron' {
  /** Electron's `Event`-shaped callbacks: cancellable, nothing else is read. */
  interface ElectronEvent {
    preventDefault(): void
  }

  export interface Session {
    on(event: 'will-download', listener: (event: ElectronEvent, downloadItem: unknown) => void): void
    setPermissionCheckHandler(
      checkHandler: (webContents: unknown, permission: string, requestingOrigin: string) => boolean
    ): void
    setPermissionRequestHandler(
      requestHandler: (webContents: unknown, permission: string, callback: (granted: boolean) => void) => void
    ): void
  }

  export interface WebContents {
    on(event: 'did-navigate', listener: (event: ElectronEvent, url: string, httpResponseCode: number) => void): void
    on(
      event: 'did-navigate-in-page',
      listener: (event: ElectronEvent, url: string, isMainFrame: boolean) => void
    ): void
    on(event: 'render-process-gone', listener: (event: ElectronEvent, details: unknown) => void): void
    on(event: 'will-navigate', listener: (event: ElectronEvent, url: string) => void): void
    on(event: 'will-redirect', listener: (event: ElectronEvent, url: string) => void): void
    setAudioMuted(muted: boolean): void
    setWindowOpenHandler(handler: (details: { url: string }) => { action: string }): void
  }

  export interface BrowserWindow {
    destroy(): void
    focus(): void
    isDestroyed(): boolean
    loadURL(url: string): Promise<void>
    on(event: 'closed', listener: (event: ElectronEvent) => void): void
    show(): void
    webContents: WebContents
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type BrowserWindowConstructorOptions = any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type MenuItemConstructorOptions = any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export const app: any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export const ipcMain: any
}
