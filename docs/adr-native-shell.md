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

## Open questions

- What is the minimum WebKitGTK version we support on Linux, and does pane3d (three.js WebGL) render correctly there? Decide after the B1 spike.
- How is the sidecar bundled for desktop: a single-file executable of `hermes-plus`, or a bundled Python runtime? This affects updater size and the Tauri external-binaries configuration.
- How does a desktop user attach to a remote sidecar: paste URL and token in a dialog, or read a config file? B1 needs an answer for the simplest flow.
- Phase C: Tauri mobile or PWA? The deciding factor is how much of the HostBridge surface mobile browsers can carry; the host terminal is the hard part.
- Which deep link targets matter on day one, and does the scheme name conflict with anything already registered on user machines?
