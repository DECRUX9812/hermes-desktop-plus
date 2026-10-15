/**
 * Minimal ambient `electron` module so the renderer typechecks the handful of
 * electron/* type files it imports (see ../electron/, synced from the fork).
 * The browser bundle never loads Electron; these declarations exist only so
 * `tsc -p tsconfig.json` can resolve the module without the 100 MB package.
 */
declare module 'electron' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type BrowserWindow = any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type BrowserWindowConstructorOptions = any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type MenuItemConstructorOptions = any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export type Session = any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export const app: any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export const ipcMain: any
}
