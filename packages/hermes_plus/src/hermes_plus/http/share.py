"""Session share links — a revocable, read-only live view of one session.

Port of fork hermes_cli/web_routers/share.py. Differences: grants live in
plus.db (store/share.py) instead of share_grants.json, and the transcript is
read through the STOCK session REST surface (``GET /api/sessions/{id}``,
``GET /api/sessions/{id}/messages``) with the internal upstream token instead
of an in-process SessionDB handle.

The events payload is deliberately narrow: user/assistant text plus tool-call
NAMES only — no tool arguments, no tool results, no system rows.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from hermes_plus import store as grants
from hermes_plus.store import Store

log = logging.getLogger(__name__)

_LIVE_WINDOW_S = 300
_PAGE_LIMIT = 400          # transcript rows returned per poll
_PAGE_SIZE = 500           # stock messages page size
_MAX_ROWS_FETCHED = 5000   # hard stop for the after_id scan
_TEXT_MAX_CHARS = 200_000  # per-message text bound, defense-in-depth

_NOT_FOUND = "share link not found"


async def _stock_get_json(runtime: Any, path: str, params: dict[str, Any]) -> tuple[int, Any]:
    response = await runtime.http_client.get(
        f"{runtime.upstream_http_origin}{path}",
        params=params,
        headers={"X-Hermes-Session-Token": runtime.upstream_token},
    )
    if response.status_code == 404:
        return 404, None
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


def _store(request: Request, profile: str | None) -> Store:
    return request.app.state.runtime.store_for(profile or None)


async def _resolve_grant(request: Request, profile: str, token: str) -> dict[str, Any] | None:
    """The grant row for ``(profile, token)``, or None — a dead link and a
    never-minted one are indistinguishable to a crawler."""
    return grants.resolve(_store(request, profile), token)


async def _fetch_session(runtime: Any, session_id: str) -> dict[str, Any] | None:
    status, detail = await _stock_get_json(runtime, f"/api/sessions/{session_id}", {})
    return detail if status == 200 and isinstance(detail, dict) else None


async def _fetch_messages(runtime: Any, session_id: str, after_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < _MAX_ROWS_FETCHED:
        status, payload = await _stock_get_json(
            runtime,
            f"/api/sessions/{session_id}/messages",
            {"order": "oldest", "limit": _PAGE_SIZE, "offset": offset, "inline_images": "false"},
        )
        if status != 200 or not isinstance(payload, dict):
            break
        page = payload.get("messages")
        if not isinstance(page, list) or not page:
            break
        rows.extend(row for row in page if isinstance(row, dict))
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    if after_id:
        rows = [row for row in rows if isinstance(row.get("id"), int) and row["id"] > after_id]
    return rows[:_PAGE_LIMIT]


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """One message row → a share event, or None to skip it (system/tool rows)."""
    role = row.get("role")
    if role not in ("user", "assistant"):
        return None
    text = row.get("display_content") if isinstance(row.get("display_content"), str) else row.get("content")
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)[:_TEXT_MAX_CHARS] if text else ""
    tools = []
    raw_calls = row.get("tool_calls")
    if isinstance(raw_calls, str):
        try:
            raw_calls = json.loads(raw_calls)
        except json.JSONDecodeError:
            raw_calls = None
    for call in raw_calls or []:
        if not isinstance(call, dict):
            continue
        name = call.get("name") or (call.get("function") or {}).get("name")
        if name:
            tools.append(str(name))
    return {"id": row.get("id"), "role": role, "text": text[:_TEXT_MAX_CHARS],
            "tools": tools, "timestamp": row.get("timestamp")}


def _headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


async def share_create(request: Request) -> JSONResponse:
    runtime = request.app.state.runtime
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    session_id = str(body.get("session_id") or "").strip()
    profile = body.get("profile")
    ttl_seconds = body.get("ttl_seconds")
    if not session_id:
        return JSONResponse({"detail": "session_id required"}, status_code=400)

    session = await _fetch_session(runtime, session_id)
    if session is None:
        return JSONResponse({"detail": "session not found"}, status_code=404)
    try:
        minted = grants.mint(_store(request, profile), session_id, ttl_seconds)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    name = profile or runtime.settings.profile or "default"
    query = f"t={minted['token']}" if not profile else f"p={name}&t={minted['token']}"
    path = f"/share/view?{query}"
    return JSONResponse({
        "token": minted["token"], "expires_at": minted["expires_at"],
        "profile": name, "path": path,
        "url": f"{str(request.base_url).rstrip('/')}{path}"}, headers=_headers())


async def share_revoke(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    revoked = grants.revoke(_store(request, body.get("profile")), str(body.get("token") or ""))
    return JSONResponse({"revoked": revoked})


async def share_list(request: Request) -> JSONResponse:
    session_id = request.query_params.get("session_id", "")
    if not session_id:
        return JSONResponse({"detail": "session_id required"}, status_code=400)
    shares = grants.list_for(_store(request, request.query_params.get("profile")), session_id)
    return JSONResponse({"shares": shares})


async def share_view(request: Request) -> JSONResponse:
    """Public, capability-secured transcript read (PUBLIC_PATHS)."""
    runtime = request.app.state.runtime
    profile = request.query_params.get("p", "")
    token = request.query_params.get("t", "")
    try:
        after_id = int(request.query_params.get("after_id", "0"))
    except ValueError:
        after_id = 0
    if after_id < 0:
        after_id = 0
    grant = await _resolve_grant(request, profile, token)
    if grant is None:
        return JSONResponse({"detail": _NOT_FOUND}, status_code=404, headers=_headers())
    session_id = grant["session_id"]

    session = await _fetch_session(runtime, session_id)
    if session is None:
        return JSONResponse({"detail": _NOT_FOUND}, status_code=404, headers=_headers())
    rows = await _fetch_messages(runtime, session_id, after_id)
    events = [e for e in (_sanitize_row(r) for r in rows) if e is not None]

    now = time.time()
    started_at = session.get("started_at")
    live = (session.get("ended_at") is None
            and (now - (session.get("last_activity_at") or started_at or 0)) < _LIVE_WINDOW_S)
    return JSONResponse(
        {"title": session.get("title") or "Shared session",
         "live": bool(live), "model": session.get("model") or "",
         "events": events, "next_after_id": events[-1]["id"] if events else after_id},
        headers=_headers())


_VIEWER_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Hermes — shared session</title>
<style>
  :root{color-scheme:dark;--bg:#0b0d10;--fg:#e8eaed;--dim:#8b95a1;--line:#22272e;--accent:#5eb1ef}
  *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
  padding:10px 16px;display:flex;gap:8px;align-items:center}
  #live{width:8px;height:8px;border-radius:50%;background:#3f4;box-shadow:0 0 6px #3f4}
  #live.off{background:#555;box-shadow:none}
  main{max-width:760px;margin:0 auto;padding:16px}
  .m{margin:10px 0;padding:10px 14px;border-radius:12px;max-width:92%;white-space:pre-wrap;
  overflow-wrap:anywhere}
  .user{background:#1c2733;margin-left:auto}
  .assistant{background:#14181d;border:1px solid var(--line)}
  .tool{color:var(--dim);font-size:12px;margin:2px 0 2px 14px}
  .tool::before{content:"⚙ ";color:var(--accent)}
  #err{color:#f66;text-align:center;margin-top:40px}
  footer{color:var(--dim);font-size:11px;text-align:center;padding:24px}
</style></head><body>
<header><span id="live" class="off"></span><strong id="title">Hermes — shared session</strong>
<span style="margin-left:auto;color:var(--dim);font-size:11px" id="model"></span></header>
<main id="chat"></main><div id="err"></div>
<footer>Read-only live view · shared via Hermes</footer>
<script>
const P = new URLSearchParams(location.search).get('p') || 'default';
const T = new URLSearchParams(location.search).get('t') || '';
const chat = document.getElementById('chat'), err = document.getElementById('err');
let after = 0, dead = false;
const esc = s => { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; };
function render(ev){
  const div = document.createElement('div');
  div.className = 'm ' + ev.role;
  div.innerHTML = esc(ev.text || '');
  chat.appendChild(div);
  for (const name of ev.tools || []){
    const t = document.createElement('div');
    t.className = 'tool'; t.textContent = name; chat.appendChild(t);
  }
}
async function poll(){
  if (dead) return;
  try {
    const r = await fetch(`/api/share/view?p=${encodeURIComponent(P)}&t=${encodeURIComponent(T)}&after_id=${after}`);
    if (r.status === 404){ dead = true; err.textContent = 'This share link was revoked or expired.'; document.getElementById('live').className = 'off'; return; }
    const d = await r.json();
    document.getElementById('title').textContent = d.title || 'Shared session';
    document.getElementById('model').textContent = d.model || '';
    document.getElementById('live').className = d.live ? '' : 'off';
    const grow = d.events && d.events.length;
    for (const ev of d.events || []) render(ev);
    after = d.next_after_id || after;
    if (grow) scrollTo(0, document.body.scrollHeight);
  } catch(e) {}
  setTimeout(poll, 2500);
}
poll();
</script></body></html>"""


async def share_viewer_page(request: Request) -> HTMLResponse:
    """The public viewer document — validates the grant before serving so a dead
    link shows the same 404 as the events endpoint."""
    grant = await _resolve_grant(
        request, request.query_params.get("p", ""), request.query_params.get("t", ""))
    if grant is None:
        return JSONResponse({"detail": _NOT_FOUND}, status_code=404, headers=_headers())
    return HTMLResponse(_VIEWER_HTML, headers=_headers())


routes = [
    Route("/api/share/create", share_create, methods=["POST"]),
    Route("/api/share/revoke", share_revoke, methods=["POST"]),
    Route("/api/share/list", share_list, methods=["GET"]),
    Route("/api/share/view", share_view, methods=["GET"]),
    Route("/share/view", share_viewer_page, methods=["GET"]),
]
