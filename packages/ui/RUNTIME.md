# Browser-hosted runtime contract

`packages/ui` is a browser-hosted renderer. The server serves the Vite output,
injects the bootstrap values below before the application bundle runs, and
keeps the API and WebSocket routes under the same reverse-proxy prefix.

## HTML bootstrap

The server must set these properties on `window`:

| Property | Meaning |
| --- | --- |
| `__HERMES_BASE_PATH__` | Optional reverse-proxy prefix, with or without a leading slash. The renderer normalizes it and prefixes every HTTP and WebSocket route. The fork injects it in `hermes_cli/web_server_dashboard.py:188-197`. |
| `__HERMES_SESSION_TOKEN__` | The dashboard session token for an ungated page. Do not inject it when cookie authentication is enabled or when serving the private Webapp launch page. The fork emits it conditionally in `hermes_cli/web_server_dashboard.py:157-177`. |
| `__HERMES_AUTH_REQUIRED__` | `true` when the server requires cookie authentication. In this mode the renderer obtains a one-use WebSocket ticket from `/api/auth/ws-ticket`; it does not send a session token. The fork selects this mode in `hermes_cli/web_server_dashboard.py:171-194`. |
| `__HERMES_UI_SURFACE__` | The surface name, normally `dashboard` or `webapp`. The browser bridge treats `webapp` as a private launch session. The server writes it in `hermes_cli/web_server_dashboard.py:171-195`; the surface policy is defined in `hermes_cli/web_server_surface.py:30-46`. |
| `__HERMES_PLUS__` | Optional Hermes Desktop Plus override: `{ apiBase?, wsBase?, basePath? }`. `apiBase` and `wsBase` may be absolute URLs or URL prefixes. `basePath` overrides `__HERMES_BASE_PATH__`. Runtime values take precedence over `VITE_HERMES_API_BASE`, then same-origin behavior is used. |

The document root must carry `data-hermes-desktop-host="browser"` (the
browser bridge also accepts the bootstrap predicate for the first module
evaluation). This is the predicate in
`packages/ui/src/lib/platform.ts:30-57`.

## Routes

The browser bridge calls these server routes:

| Route | Use |
| --- | --- |
| `GET/POST /api/*` | JSON API calls, including gateway/profile/files routes dispatched by `src/api/client.ts` and `src/lib/browser-desktop-bridge.ts`. |
| `WebSocket /api/ws` | Gateway JSON-RPC transport, with `token` in the query for token sessions. |
| `POST /api/auth/ws-ticket` | Creates a short-lived WebSocket ticket for cookie-authenticated sessions. |
| `WebSocket /api/host-terminal` | Browser terminal host bridge, with the same token or ticket query credential. The fork route is `hermes_cli/web_routers/webapp.py:35-55`. |
| `POST /api/chat/file-upload` | Stages a browser-selected attachment. The client sends a multipart field named `file`; the fork route is `hermes_cli/web_routers/uploads.py:149-194`. |
| `GET /api/desktop-plugins/root` | Returns the desktop plugin root. |
| `GET /api/desktop-plugins/list` | Lists all plugins, or one plugin with `name`. |
| `GET /api/desktop-plugins/source` | Reads a plugin source file with `name` and `file`. |
| `POST /api/webapp/window-ticket` | Mints a one-use child-window ticket. |
| `POST /webapp/window-session` | Exchanges `X-Hermes-Window-Ticket` for a session token. |
| `GET /webapp/launch` | Credential-free launch handoff page for the server-opened tab. |
| `GET /webapp/window` | Credential-free handoff page for a child window. |

The window handoff route intentionally uses a request header rather than a
query token. The fork validates and consumes that ticket in
`hermes_cli/web_routers/webapp.py:135-175`, then stores the returned token in
tab-scoped session storage in
`packages/ui/src/lib/browser-launch-session.ts:16-34`.

## Authentication and token flow

For an ungated dashboard, the server injects `__HERMES_SESSION_TOKEN__` and
the renderer sends it as `X-Hermes-Session-Token` on HTTP requests. For an
authenticated dashboard, the browser sends same-origin credentials and first
calls `/api/auth/ws-ticket` for a one-use WebSocket ticket. The renderer uses
the ticket as the `ticket` WebSocket query parameter.

For `webapp`, the server does not place a token in HTML. The launch URL
contains a `#hermes-session=<token>` fragment, which is removed from the
address bar and saved in `sessionStorage` by `consumeWebappSession`
(`packages/ui/src/lib/browser-launch-session.ts:16-34`). A restarted or
missing launch grant produces `WEBAPP_LAUNCH_REQUIRED`; the server must
provide a fresh private launch link. The fork's private surface policy is
`hermes_cli/web_server_surface.py:30-46`.

The transport implementation is
`packages/ui/src/lib/browser-transport.ts:103-177` and
`packages/ui/src/lib/browser-transport.ts:276-324`. It keeps the existing
injected globals compatible while allowing the Plus runtime override and
`VITE_HERMES_API_BASE` build setting.
