# Security

`hermes-plus serve` puts a full agent in a browser tab: shell tools, a host
terminal, file reads and writes, uploads, and the chat that drives them. This
page says exactly what that exposes, what the front door protects, and what to
do before you put it on a tunnel.

## What is exposed

One process, the sidecar, is the only thing that listens. Stock Hermes stays on
loopback (`127.0.0.1`) as a child of the sidecar, and nothing else is reachable
from outside.

Behind the front door, one authenticated client can:

- run prompts, which can run shell commands and tools as **the user the sidecar
  runs as**;
- open a **host terminal** (`WS /api/host-terminal`) — a real PTY shell, in the
  user's home or any directory, with full retention of the scrollback;
- read and download files the server user can read. That includes the stock
  files route: `/api/files/download` serves any path its token holder names
  (upstream behaviour, unchanged here);
- upload files (16 MiB each) and attach them to prompts;
- create, revoke and read **share links**; mint **mobile pairing codes**;
- change settings, profiles, bot teams, and the plugin rail.

There are no roles. Any authenticated client is the operator. Treat the login
credential, and any share link or pairing code, as equivalent to a shell on the
host.

## What the front door protects

Every route and every WebSocket requires the session credential, except a small
public set that carries its own capability:

| Public path | What secures it |
| --- | --- |
| `/healthz` | nothing; version and compatibility status only |
| `/login` | nothing; the password form (password mode only) |
| `/api/share/view`, `/share/view` | a 256-bit share token in the URL, bound to one session, read-only, revocable, optional expiry |
| `/api/mobile/pair` | a 6-digit single-use pairing code, 10-minute TTL, rate-limited |
| `/webapp/window-session` | a single-use 30-second ticket, same-origin request, loopback peer for launch tickets |
| `/webapp/launch`, `/webapp/window` | credential-free handoff documents; they contain no secret |
| `/api/plus/bridge/rpc` | loopback clients only, plus the process token |

`--auth token` (default) prints one login URL with a random token; opening it
sets an `HttpOnly`, `SameSite=Lax` cookie (`Secure` when the request arrived
over https, directly or through a trusted proxy). `--auth password` serves a
login form and signs the cookie with a key at
`<HERMES_HOME>/hermes-plus/session.key` (mode 0600) bound to the current
password, so a restart does not log anyone out and a password change does.
`--auth none` is refused on a non-loopback host.

Two more rules are enforced on top:

- **Origin.** A request that carries a browser `Origin` is refused (403) unless
  its host and port match the sidecar's own `Host` — on unsafe methods (POST,
  PUT, PATCH, DELETE) and on every WebSocket upgrade. `SameSite=Lax` alone does
  not stop same-site attackers: another port on `localhost`, or another
  subdomain of the same registrable domain behind a tunnel, is same-site, so the
  cookie would ride along. Requests with no `Origin` at all (curl, the bridge
  plugin, the CLI, a phone app) are never refused and keep authenticating with
  the token.
- **Headers.** HTML is served with `X-Frame-Options: DENY` and
  `Content-Security-Policy: frame-ancestors 'none'` (the chat cannot be framed
  and clickjacked), every response carries `X-Content-Type-Options: nosniff`,
  and downloads are `Content-Disposition: attachment`, so an uploaded `.html`
  cannot execute as the sidecar's origin.

The logs never carry a capability: uvicorn's access log is off, and our own
records the method, the path with sensitive query values redacted, and the
status. The **startup banner is the exception** — `hermes-plus serve` prints the
one-time login URL, which contains the token, because that is how you get in.
Keep the service log out of shared pastebins.

## Before you expose it on a tunnel

1. **Use `--auth password`, not `token`, and put an identity layer in front.**
   With Cloudflare Tunnel, add **Cloudflare Access** (Zero Trust SSO/MFA) on the
   hostname. A token URL is a bearer credential in a URL: it lands in browser
   history, and anyone who sees it is the operator.
2. **Set `HERMES_PLUS_TRUST_PROXY_HEADERS=1`** when a reverse proxy terminates
   the connection, so the cookie is marked `Secure` and the login limiter keys
   on the address the proxy saw rather than the caller's own claim.
3. **Make sure the proxy forwards `X-Forwarded-Host`.** The Origin check accepts
   the request's `Host` header; a proxy that rewrites `Host` to the sidecar's
   loopback address must forward the original name in `X-Forwarded-Host` (this
   is the only case in which that header is consulted).
4. **If the UI is hosted by something else** — a native shell's webview, an
   Electron window on `file://` — name that origin in
   `HERMES_PLUS_ALLOWED_ORIGINS` (comma-separated, e.g.
   `tauri://localhost`), or its POSTs and WebSockets will be refused as
   cross-origin.
5. **Leave the host terminal off unless you mean it.** It is enabled by default
   only when the sidecar binds to a loopback address; `--host 0.0.0.0` (or the
   Docker container, which binds 0.0.0.0) leaves it off, and
   `HERMES_PLUS_HOST_TERMINAL=1` turns it on. A browser tab with a host shell is
   the sharpest edge this project has.
6. **Share links are public URLs.** They show the session's user and assistant
   text and tool *names* (never arguments, results or system rows), they are
   revocable, and they can be given a TTL. Treat one as public the moment you
   send it.
7. **Keep stock Hermes on loopback.** Do not expose the `hermes dashboard` child
   or its token; the sidecar is the front door.
8. **Serve https end to end.** A tunnel does this for you; a plain-http LAN bind
   does not, and the cookie then has no `Secure` flag.

## Known limits, accepted

- **Single-user by design.** There is no per-user isolation inside the sidecar:
  one credential, one operator, one HERMES_HOME.
- **The stock files route is a read-any-path primitive** for an authenticated
  client (upstream's own behaviour). It is not a boundary crossing — that client
  can already run shell commands — but it is why file *tickets* exist: a ticket
  admits exactly one route and query, and a download ticket is single-use.
- **`GET /login` in token mode is a 401**, not a redirect: there is no form to
  serve. The one-time token URL is the way in.
- **The sidecar trusts its own host.** A multi-tenant host where other local
  users can reach the loopback port is out of scope: they can reach the token in
  the process's environment or the login URL in the log.

## Reporting a problem

Open a private report on the repository: **Security → Report a vulnerability**
(GitHub private security advisories). Please include the version
(`hermes-plus --version`), the auth mode, and a reproduction. Do not open a
public issue for anything that lets a request past the front door, leaks a
capability, or runs code.

## How this is tested

`packages/hermes_plus/tests/integration/test_server_security.py` walks the
mounted routes of the real app object and hits each one without a credential;
`tests/test_server_auth_origin.py` and `tests/test_server_hygiene.py` cover the
front-door policy and the log/header rules. They run against a real sidecar on
real stock Hermes, and every behavioural case in them fails against the
pre-hardening code.
