# ADR: native desktop shell

Status: accepted for phase B planning. No shell build has started.

## Context

The product ships in phases. Phase A is a web add-on: the sidecar (`hermes-plus serve`) serves the `packages/ui` build, multiplexes the `/api/ws` JSON-RPC connection, and fronts a stock `hermes dashboard` bound to loopback. That part works in a plain browser tab today.

Desktop users still want a real application window: a dock or taskbar entry, a tray icon, deep links, and an auto-updater, without opening a terminal. The fork the UI came from ships an Electron shell for that. The question is what shell this project should build next.

Constraints that shape the answer:

- The UI is a React application of roughly 200k lines, including the assistant chat components, a three.js 3D pane (pane3d) and other heavy rendering. Rewriting it is out of the question on this project's budget.
- The backend is the sidecar in every scenario. It owns authentication, its own HTTP routes, the RPC multiplexer and the adapter to stock Hermes. No shell option changes that.
- The runtime contract between server and UI is injection-based and documented in `packages/ui/RUNTIME.md`: the server sets `window.__HERMES_BASE_PATH__`, `__HERMES_SESSION_TOKEN__`, `__HERMES_AUTH_REQUIRED__` and `__HERMES_UI_SURFACE__` before the bundle runs, and the Plus override is `window.__HERMES_PLUS__ = { apiBase, wsBase, basePath }`.
- The UI already detects its host at runtime (`packages/ui/src/lib/platform.ts`, the `data-hermes-desktop-host` predicate) and routes all host calls through a bridge interface (`window.hermesDesktop`, with `browser-desktop-bridge` and `lib/browser-*.ts` shims). The browser is one implementation; Electron is another.

## Options

### Web app only

Keep the browser tab as the shell. This is what phase A ships, and it is done. Effort is zero beyond what exists. Runtime performance is whatever the browser gives, with no install size at all. The full web serving story works by definition, since the sidecar is built for it. A PWA (installable page, service worker for offline shell, push) recovers some of the desktop feel with small effort. What is missing is real OS integration: no tray, no global shortcuts, no file associations, no reliable single-instance behavior, and the user must keep a tab alive or re-launch a URL.

### Tauri v2 (Rust core plus system webview)

A small Rust binary that opens a system webview and loads the existing `packages/ui` build. UI reuse is close to 100 percent: the same Vite output, with a third HostBridge implementation in TypeScript next to the `browser` and `electron` ones. Effort to desktop parity is around one to two weeks: window management, tray, deep links, sidecar launch and supervision, and an updater. The binary is small (roughly 5 to 15 MB) because it ships no rendering engine, and RAM use is low compared to Electron. Web serving is unchanged: the same sidecar serves the web, and the desktop shell is just another client of it. Tauri v2 also targets iOS and Android, which makes it the natural path to phase C. The main risk is Linux: the webview is WebKitGTK, which varies by distribution, and older WebKit builds have WebGL quirks that could hurt pane3d. Rendering also differs slightly across operating systems, so each platform needs a visual pass.

### Electron (the fork's current shell)

The fork already runs the UI in Electron. UI reuse is 100 percent and effort is a repackaging exercise, since the shell exists. Runtime cost is the problem: installers are 150 MB or more, RAM use is high, and each Electron app ships its own Chromium, so the rendering stack is duplicated on the user's disk for every such app. Web serving is unchanged. There is no mobile story at all. This option is the fallback, not the goal: the size and memory cost are the main complaints about the current desktop app, and the HostBridge keeps the door open if Tauri fails in practice.

### Full-native Rust UI (Dioxus, Slint, egui, gpui)

Rebuild the interface in a native Rust toolkit. Runtime performance and potential binary size are the best of all options. Everything else is negative: it discards all of the React code, including the chat component library and the three.js scenes, and costs months. The web serving story is lost, because a native UI is not a web page; Dioxus has a web target but it is partial, so a second web UI would be needed anyway for browser and mobile users. Two UIs to maintain also guarantees feature drift against upstream UI improvements. Rejected.

### Comparison

| Option | React UI reuse | Effort | Runtime and size | Web serving | Mobile | Main risk |
|---|---|---|---|---|---|---|
| Web app only | 100% | done (phase A) | browser | native to the option | PWA only | no OS integration |
| Tauri v2 | ~100% | 1 to 2 weeks | 5 to 15 MB, low RAM | unchanged, sidecar stays backend | iOS and Android via v2 | WebKitGTK on Linux |
| Electron | 100% | repackaging only | 150 MB+, high RAM | unchanged | none | heavy, duplicate Chromium |
| Full-native Rust | 0% | months | best raw numbers | lost, needs a second web UI | partial per toolkit | two UIs, feature drift |

## Decision

Phase B uses Tauri v2, reusing the `packages/ui` build unchanged.

Concretely:

- The Tauri app launches `hermes-plus serve` on loopback as a bundled sidecar, or attaches to a remote sidecar given a URL and token. Port binding stays on `127.0.0.1` for the local case.
- The shell injects `window.__HERMES_PLUS__ = { apiBase, wsBase }` into the webview before the application bundle runs, following the runtime contract in `packages/ui/RUNTIME.md`. `basePath` stays supported for reverse-proxy deployments.
- The UI talks to the host only through the HostBridge interface. The implementations are `browser` and `electron` today; Tauri adds a third with the same surface (host terminal over the sidecar WebSocket, file upload, window tickets, desktop plugins). No component imports the shell directly.
- Full-native Rust UI is rejected. It costs months, discards the entire React codebase, loses web serving, and leaves two UIs to keep in sync with upstream.
- Electron is not chosen for phase B, but it remains the fallback if the WebKitGTK risk turns out to be real (see open questions). Because the HostBridge is the only shell seam, switching later is bounded work.

## Consequences

Positive:

- One UI codebase serves browser, desktop and later mobile users.
- The binary stays small and RAM use stays low.
- The backend is identical in every shell, so upstream compatibility testing (the contract suite against the sidecar) keeps covering the one serving path that exists.
- A future shell swap is a new HostBridge implementation plus injection of the runtime globals.

Negative or risky:

- WebKitGTK varies across Linux distributions. Older builds may lack current WebGL features, which pane3d and other three.js scenes depend on. This needs a spike before B1 is called done.
- Rendering differences across operating systems mean a visual pass per platform instead of one Chromium baseline.
- The updater, code signing and notarization are new work that the web app never needed.
- Sidecar lifecycle (start, health check, port pick, clean shutdown, crash restart) moves into the Rust shell and needs its own tests.

## Phasing

- **Phase A: web add-on (done).** `hermes-plus serve` serves the UI with injected runtime config, multiplexes the gateway connection, and owns auth. HostBridge implementation: `browser`.
- **Phase B: Tauri desktop shell.** Milestones:
  - B1: Tauri window loading a user-supplied sidecar URL; `tauri` HostBridge with feature parity to `browser` (terminal, upload, window tickets, desktop plugins). Includes the Linux WebKitGTK spike on the oldest supported distribution.
  - B2: bundled sidecar: launch and supervise `hermes-plus serve` on loopback with an OS-picked port, health check, clean shutdown, and restart on crash.
  - B3: deep links (for example a `hermes-plus://` scheme opening a session, share view or bot room), tray icon, single-instance lock.
  - B4: auto-updater and signed builds for the three desktop platforms.
- **Phase C: mobile or PWA.** Either a Tauri v2 mobile shell or an installed PWA, attaching to a remote sidecar over the tunnel with token auth. No local Hermes on the device.

What keeps the shell swappable in this repository:

- `packages/ui` has no shell imports in the application code. Host detection is one runtime predicate, and every host call goes through the HostBridge interface.
- The server-to-UI contract is injection of a small set of `window` globals, documented in `packages/ui/RUNTIME.md`. Any shell that can set those before the bundle runs can serve the UI.
- The backend is a separate process with a stable HTTP and WebSocket surface. There is no shell-specific backend code, and the sidecar is equally reachable from a browser tab, a Tauri webview or a phone.

## Phase B plan

This is the build plan for the Tauri v2 shell. Nothing here exists yet; the
milestones are the ones listed under Phasing.

### Layout

```
apps/tauri/                       new workspace; not part of the Python or UI builds
  src-tauri/
    Cargo.toml                    tauri, tauri-plugin-updater, tauri-plugin-deep-link,
                                  tauri-plugin-single-instance, tauri-plugin-shell
    tauri.conf.json               window, bundle, updater endpoint, externalBin, CSP
    capabilities/default.json     only the permissions the HostBridge needs
    src/main.rs                   window setup, tray, deep links, single-instance lock
    src/sidecar.rs                sidecar launch, port pick, health check, shutdown
    src/hostbridge.rs             the native commands the HostBridge calls
    icons/
  ui-host/
    hostbridge.ts                 the `tauri` HostBridge implementation
    index.ts                      installs `window.hermesDesktop` before the app bundle runs
  package.json                    builds ui-host/ and stages the packages/ui output
```

`apps/tauri/` consumes `packages/ui`'s Vite output. It does not fork the
renderer: the same `dist-webapp` is loaded, and `apps/tauri/ui-host/hostbridge.ts`
is the only new shell code. The build order is `packages/ui`, then `apps/tauri`,
and the shell's own tests do not touch `packages/ui` sources.

### HostBridge

The shell seam is `window.hermesDesktop`, detected at runtime by
`packages/ui/src/lib/platform.ts` and documented in `packages/ui/RUNTIME.md`.
`tauri` is the third implementation, next to `browser` and `electron`. Nothing
below it changes: every call still goes to the sidecar's HTTP and WebSocket
surface, so the shell adds no backend code.

| Concern | `browser` today | `tauri` implementation |
|---|---|---|
| Host terminal | WebSocket to `/api/host-terminal` | the same WebSocket; no native PTY |
| File upload | multipart `POST /api/chat/file-upload` | the same route, plus a native file picker |
| Child windows | `window.open` plus the `/api/webapp/window-ticket` handoff | `WebviewWindow` plus the same ticket handoff |
| Desktop plugins | HTTP from the sidecar | the same routes |
| Window state, tray, notifications | unavailable | Tauri window API and plugins |
| Deep links | unavailable | `tauri-plugin-deep-link`, mapped to a session or share route |

The one rule that keeps the shell swappable: no component may import the shell
directly, and the shell may not add a backend route. If a feature needs one, it
belongs in the sidecar, where browser and mobile users get it too.

### Sidecar launch

Three modes behind one process contract, all of them attaching to the same
`hermes-plus serve` HTTP and WebSocket surface:

1. **Attach to a remote sidecar.** The user supplies a URL and a token; the
   shell injects `window.__HERMES_PLUS__ = { apiBase, wsBase }` and stores the
   credential in the OS keychain. This is the first thing to build (B1), because
   it needs no sidecar lifecycle code at all.
2. **Use the user's own install.** The shell resolves `hermes-plus` on `PATH`
   (or a path the user set) and spawns it. This is the mode for anyone who
   installed with `install.sh`.
3. **Bundled sidecar.** A PyInstaller one-file build of `hermes-plus`, shipped
   as a Tauri `externalBin` and spawned by the shell.

For modes 2 and 3 the shell owns the lifecycle:

- **Port.** `hermes-plus serve` validates its port and rejects `0`, so the shell
  picks a free loopback port itself and passes `--port`. The child is spawned
  with `--host 127.0.0.1` and `--auth token`; the token is generated per launch
  and handed to the webview, never written to disk.
- **Readiness.** Poll `GET /healthz` until it reports `status: ok`, with a
  bounded timeout. The payload already carries `version`, `hermes_commit` and
  `compat`, so the shell can show a compatibility banner without a second call.
- **Shutdown.** SIGTERM and wait for exit; the sidecar shuts its stock
  `hermes dashboard` child down with it. A crash restarts once, then surfaces
  the failure instead of looping.
- **Hermes is still required.** The sidecar spawns a stock `hermes dashboard`
  child, so bundling our sidecar does not remove the Hermes dependency. Modes 2
  and 3 both need a stock Hermes install; the shell discovers it the same way
  `install.sh` does (`HERMES_PLUS_HERMES_BIN`, then `PATH`, then the standard
  locations) and offers to run upstream's installer if it is missing.
- **PyInstaller payload.** The binary must carry `ui_dist/`, `bridge/`,
  `compat/matrix.json` and `store/migrations/*.sql` as data. A wheel that ships
  `ui_dist/` is the same payload in a different container, so the CI wheel check
  already guards the file list; the PyInstaller spec needs its own assertion for
  the same four paths.

### Updater

`tauri-plugin-updater` with a signed manifest, and a channel setting that
mirrors the release flow:

- The shell's own update replaces the app bundle, including the bundled sidecar
  binary. When the bundled sidecar is in use, `hermes-plus update` must be
  disabled in the shell (the package manager that would upgrade it is not the
  one that installed it) and the shell is the only updater.
- Modes 1 and 2 keep `hermes-plus update` working as it does today, and the
  shell surfaces the version mismatch between itself and the attached sidecar
  rather than hiding it.
- Signing is new work the web app never needed: Tauri's updater signature
  (minisign) for the manifest, plus platform code signing (Apple notarization,
  Windows Authenticode) for the installers. B4 owns all of it.
- `hermes update` is never the shell's business: it upgrades stock Hermes, which
  stays a separate install under a separate home.

### Linux WebKitGTK risks, and the test plan

The webview on Linux is WebKitGTK, and the risk is concrete:

- **pane3d and other three.js scenes need WebGL2.** `@react-three/fiber` and
  `three` are in `packages/ui/package.json`; pane3d is a real route, not a
  garnish. Older WebKitGTK builds have incomplete WebGL2 and inconsistent
  `OffscreenCanvas` behaviour, and a missing context blanks the pane.
- **The terminal uses `@xterm/addon-webgl`.** The host terminal in
  `/api/host-terminal` renders through the WebGL addon when it can; WebKitGTK
  differences can make the glyph atlas misbehave instead of failing loudly.
- **Rendering differs per platform.** There is no single Chromium baseline any
  more, so each platform needs its own visual pass rather than one shared one.

Test plan, in the order the milestones need it:

1. **B1 spike, manual, on the oldest supported distribution.** Open a Tauri
   window on the `packages/ui` build, navigate to pane3d, and compare a
   screenshot against the same route in the browser build. Record the WebKitGTK
   version in the ADR and pick the minimum from what actually renders.
2. **Automated webview smoke.** `tauri-driver` plus a WebDriver smoke that loads
   the UI in the webview and asserts a WebGL2 context is created on the pane3d
   route (`canvas.getContext('webgl2')` non-null) and that the chat route renders
   a transcript. This is the regression guard that survives the manual pass.
3. **Degradation contract.** With WebGL unavailable, assert the app still loads
   chat, sessions and settings, and that the terminal falls back to the DOM
   renderer. A blank pane is a failure; a labelled fallback is not.
4. **Platform matrix, manual, per release.** Ubuntu 24.04 and the current Fedora
   release at minimum, plus Debian stable, each with the pane3d screenshot and
   the terminal check.

If the spike fails, the fallback is Electron. It is a bounded change precisely
because the HostBridge is the only shell seam and the `electron` implementation
already exists in the fork's renderer, so the cost is repackaging rather than a
rewrite.

## Open questions

Settled by the Phase B plan above:

- Sidecar packaging: a PyInstaller one-file `hermes-plus` shipped as a Tauri
  `externalBin`, with "attach to a remote sidecar" and "use the user's own
  install" as the other two modes. Stock Hermes stays a separate install in
  every mode.
- Remote attach: a URL plus a token, entered in a dialog and kept in the OS
  keychain. There is no config file to hand-edit.

Still open:

- What is the minimum WebKitGTK version we support on Linux, and does pane3d (three.js WebGL) render correctly there? Decide after the B1 spike, using the test plan above.
- Phase C: Tauri mobile or PWA? The deciding factor is how much of the HostBridge surface mobile browsers can carry; the host terminal is the hard part.
- Which deep link targets matter on day one, and does the scheme name conflict with anything already registered on user machines?
- How does the shell present a version skew between itself and an attached sidecar: a banner, a blocking dialog, or a nudge to update one of the two?
