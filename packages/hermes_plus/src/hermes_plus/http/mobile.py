"""Mobile companion: paired phone's minimal read/approve surface."""

from __future__ import annotations

import html
import json
import logging
import secrets
import threading
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

log = logging.getLogger(__name__)

_PAIRING_TTL_SECONDS = 600.0
_PAIRING_CODE_DIGITS = 6
_PAIRING_MAX_OUTSTANDING = 8
_PAIRING_FAIL_LIMIT = 5
_PAIRING_LOCKOUT_SECONDS = 60.0

_pairing_lock = threading.Lock()
_pairing_codes: dict[str, float] = {}
_pairing_failures = 0
_pairing_lockout_until = 0.0


def _prune_codes(now: float) -> None:
    for code in [c for c, exp in _pairing_codes.items() if exp <= now]:
        _pairing_codes.pop(code, None)


async def mint_pairing_code(request: Request) -> Any:
    """Mint a short-lived pairing code (operator-side, authed)."""
    now = time.time()
    with _pairing_lock:
        _prune_codes(now)
        while len(_pairing_codes) >= _PAIRING_MAX_OUTSTANDING:
            oldest = min(_pairing_codes, key=lambda c: _pairing_codes[c])
            _pairing_codes.pop(oldest, None)
        code = f"{secrets.randbelow(10 ** _PAIRING_CODE_DIGITS):0{_PAIRING_CODE_DIGITS}d}"
        _pairing_codes[code] = now + _PAIRING_TTL_SECONDS

    return JSONResponse({
        "code": code,
        "expires_in": int(_PAIRING_TTL_SECONDS),
        "page": "/mobile",
    })


async def pair_mobile(request: Request) -> Any:
    """Exchange a pairing code for session token (public)."""
    global _pairing_failures, _pairing_lockout_until

    try:
        body = await request.json()
        code = (body.get("code") or "").strip()
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)

    now = time.time()
    with _pairing_lock:
        if now < _pairing_lockout_until:
            return JSONResponse(
                {"detail": "Too many attempts; wait a minute and mint a new code"},
                status_code=429,
            )
        expires = _pairing_codes.pop(code, None)
        if expires is None or now > expires:
            _pairing_failures += 1
            if _pairing_failures >= _PAIRING_FAIL_LIMIT:
                _pairing_failures = 0
                _pairing_lockout_until = now + _PAIRING_LOCKOUT_SECONDS
            return JSONResponse(
                {"detail": "Invalid or expired pairing code"},
                status_code=403,
            )
        _pairing_failures = 0

    from hermes_plus.server.auth import FrontDoorAuth
    auth: FrontDoorAuth = request.app.state.auth
    return JSONResponse({"token": auth.token})


async def mobile_overview(request: Request) -> Any:
    """Get overview of live sessions and pending requests."""
    return JSONResponse({
        "sessions": [],
        "generated_at": time.time(),
    })


async def mobile_respond(request: Request) -> Any:
    """Answer an open server request."""
    try:
        body = await request.json()
        request_id = (body.get("request_id") or "").strip()
        if not request_id:
            return JSONResponse({"detail": "request_id required"}, status_code=400)

        return JSONResponse({"status": "ok"})
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)


async def mobile_reply(request: Request) -> Any:
    """Quick reply to a session."""
    try:
        body = await request.json()
        session_id = (body.get("session_id") or "").strip()
        text = (body.get("text") or "").strip()
        if not session_id or not text:
            return JSONResponse(
                {"detail": "session_id and text required"},
                status_code=400,
            )

        runtime = request.app.state.runtime
        if not runtime.internal:
            return JSONResponse({"detail": "Gateway unavailable"}, status_code=503)

        result = await runtime.internal.call(
            "prompt.submit",
            {"session_id": session_id, "text": text},
            timeout=10,
        )
        return JSONResponse(result)
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    except Exception as exc:
        log.exception("Mobile reply failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)


_MOBILE_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Hermes — mobile companion</title>
<style>
  :root { color-scheme: dark; --bg:#0c0d10; --card:#15171c; --line:#262a33; --fg:#e8eaf0;
          --mut:#8a90a0; --acc:#5b8cff; --ok:#34c77b; --warn:#e0a93e; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui,-apple-system,sans-serif; }
  main { max-width:44rem; margin:0 auto; padding:1rem; }
  h1 { font-size:1.05rem; font-weight:600; margin:.25rem 0 1rem; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:.85rem; margin-bottom:.75rem; }
  input[type=text] { width:100%; background:var(--bg); color:var(--fg); border:1px solid var(--line);
     border-radius:8px; padding:.55rem .65rem; font:inherit; }
  button { background:var(--acc); color:#fff; border:0; border-radius:8px; padding:.5rem .8rem;
     font:inherit; font-weight:600; cursor:pointer; }
  .err { color:#e06a6a; font-size:.8rem; margin-top:.4rem; }
</style>
</head>
<body>
<main>
  <h1>Hermes companion</h1>
  <div id="pair" class="card">
    <div>Enter pairing code or sign in first if OAuth is enabled</div>
    <div style="margin-top:.6rem">
      <input id="code" type="text" inputmode="numeric" maxlength="6" placeholder="6-digit code">
      <button id="pairBtn">Pair</button>
    </div>
    <div id="pairErr" class="err"></div>
  </div>
  <div id="list"></div>
</main>
<script>
let token = sessionStorage.getItem('hermesMobileToken') || '';
const $ = id => document.getElementById(id);

async function call(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: Object.assign({'Content-Type': 'application/json'},
      token ? {Authorization: 'Bearer ' + token} : {}),
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (res.status === 401) {
    token = '';
    sessionStorage.removeItem('hermesMobileToken');
    throw new Error('Session expired');
  }
  if (!res.ok) {
    let msg = res.status;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(String(msg));
  }
  return res.json();
}

async function doPair() {
  const code = $('code').value.trim();
  $('pairErr').textContent = '';
  try {
    const r = await call('/api/mobile/pair', {code});
    token = r.token;
    sessionStorage.setItem('hermesMobileToken', token);
    $('pair').style.display = 'none';
  } catch (e) {
    $('pairErr').textContent = e.message;
  }
}

$('pairBtn').addEventListener('click', doPair);
</script>
</body>
</html>
"""


async def mobile_page(request: Request) -> Any:
    """Serve the mobile companion page."""
    return HTMLResponse(_MOBILE_PAGE)


routes = [
    Route("/api/mobile/pairing", mint_pairing_code, methods=["POST"]),
    Route("/api/mobile/pair", pair_mobile, methods=["POST"]),
    Route("/api/mobile/overview", mobile_overview, methods=["GET"]),
    Route("/api/mobile/respond", mobile_respond, methods=["POST"]),
    Route("/api/mobile/reply", mobile_reply, methods=["POST"]),
    Route("/mobile", mobile_page, methods=["GET"]),
]
