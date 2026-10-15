"""Mobile companion (fork web_routers/mobile.py): a paired phone's read/approve surface.

Pairing codes are minted through the authed ``POST /api/mobile/pairing`` and
spent at the public, code-gated ``POST /api/mobile/pair`` for the sidecar
session token. Unlike the fork (in-memory), codes live in plus.db so a minted
code survives a sidecar restart; the failure limiter stays in memory.

Data calls reach STOCK tui_gateway handlers over the internal gateway client:
``request.answer`` for approvals, ``prompt.submit`` for quick replies,
``session.active_list`` + ``session.events.since`` for the overview.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from hermes_plus import store as pairings
from hermes_plus.store import Store

log = logging.getLogger(__name__)

_REQUEST_VALUE_MAX_CHARS = 800
_REPLY_MAX_CHARS = 20_000
_SUMMARY_KEYS = ("command", "description", "question", "message", "prompt", "title")
_MAX_OVERVIEW_SESSIONS = 32

# Fork mobile.py:41-44 — repeated misses briefly lock the public pair endpoint.
_PAIRING_FAIL_LIMIT = 5
_PAIRING_LOCKOUT_SECONDS = 60.0

_lock = threading.Lock()
_failures = 0
_lockout_until = 0.0


class _UpstreamError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def _gateway_rpc(runtime: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Call a stock gateway RPC on the internal client; map RpcError to HTTP like the fork."""
    if runtime.internal is None:
        raise _UpstreamError(503, "Gateway is not connected")
    try:
        result = await runtime.internal.call(method, params, timeout=30)
    except Exception as exc:
        code = getattr(exc, "code", None)
        message = str(getattr(exc, "message", None) or exc)
        if code == 4001:
            raise _UpstreamError(404, message) from exc
        if code == 4090:
            raise _UpstreamError(409, message) from exc
        raise _UpstreamError(400, message) from exc
    return result if isinstance(result, dict) else {}


def _store(request: Request) -> Store:
    return request.app.state.runtime.store_for()


def _password_gated(request: Request) -> bool:
    # Fork refuses under OAuth sign-in; our analog is password auth, where the
    # phone signs in through the dashboard login instead of a bearer token.
    return request.app.state.runtime.settings.auth == "password"


def _trim_request(req: dict[str, Any]) -> dict[str, Any]:
    """Wire view of an open server→client request: the fields a phone needs."""
    params = req.get("params") if isinstance(req.get("params"), dict) else {}
    out: dict[str, Any] = {
        "id": req.get("id"),
        "method": req.get("method"),
        "session_id": params.get("session_id"),
    }
    for key in _SUMMARY_KEYS:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            out["summary"] = value[:_REQUEST_VALUE_MAX_CHARS]
            break
    if isinstance(params.get("choices"), list):
        out["choices"] = [str(c)[:100] for c in params["choices"][:8]]
    if params.get("allow_permanent") is False:
        out["allow_permanent"] = False
    if isinstance(params.get("questions"), list):
        questions = []
        for q in params["questions"][:16]:
            if not isinstance(q, dict):
                continue
            entry: dict[str, Any] = {
                "qid": str(q.get("qid") or ""),
                "question": str(q.get("question") or "")[:_REQUEST_VALUE_MAX_CHARS],
            }
            if isinstance(q.get("choices"), list):
                entry["choices"] = [str(c)[:100] for c in q["choices"][:8]]
            if q.get("multi_select"):
                entry["multi_select"] = True
            questions.append(entry)
        out["questions"] = questions
    return out


async def _overview_sessions(runtime: Any) -> list[dict[str, Any]]:
    result = await _gateway_rpc(runtime, "session.active_list", {})
    rows = result.get("sessions") if isinstance(result.get("sessions"), list) else []
    overview: list[dict[str, Any]] = []
    for row in rows[:_MAX_OVERVIEW_SESSIONS]:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "")
        pending = ""
        requests: list[dict[str, Any]] = []
        try:
            replay = await _gateway_rpc(runtime, "session.events.since", {"session_id": sid, "last_seen": 0})
            open_requests = replay.get("open_requests")
            if isinstance(open_requests, list) and open_requests:
                requests = [
                    _trim_request(r) for r in open_requests if isinstance(r, dict)
                ]
                pending = str(requests[0].get("method") or "")
        except _UpstreamError:
            pass
        overview.append({
            "session_id": sid,
            "session_key": str(row.get("session_key") or ""),
            "title": str(row.get("title") or ""),
            "profile": runtime.settings.profile or "",
            "running": row.get("status") == "running",
            "cwd": "",
            "source": "",
            "last_active": float(row.get("last_active") or 0),
            "pending": pending,
            "requests": requests,
        })
    overview.sort(key=lambda s: (not s["pending"], not s["running"], -s["last_active"]))
    return overview


async def mint_pairing_code(request: Request) -> JSONResponse:
    """Mint a short-lived pairing code. Operator-side call (authed /api route)."""
    if _password_gated(request):
        return JSONResponse(
            {"detail": "This backend uses password sign-in — open /mobile on the phone and sign in there instead"},
            status_code=409,
        )
    minted = pairings.mint(_store(request))
    return JSONResponse(minted)


async def pair_mobile(request: Request) -> JSONResponse:
    """Exchange a valid pairing code for the sidecar session token (public)."""
    global _failures, _lockout_until
    if _password_gated(request):
        return JSONResponse(
            {"detail": "This backend uses password sign-in — open /mobile and sign in there instead"},
            status_code=409,
        )
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    code = str((body or {}).get("code") or "").strip() if isinstance(body, dict) else ""

    now = time.time()
    with _lock:
        if now < _lockout_until:
            return JSONResponse(
                {"detail": "Too many attempts; wait a minute and mint a new code"}, status_code=429)
        if not pairings.redeem(_store(request), code):
            _failures += 1
            if _failures >= _PAIRING_FAIL_LIMIT:
                _failures = 0
                _lockout_until = now + _PAIRING_LOCKOUT_SECONDS
            return JSONResponse({"detail": "Invalid or expired pairing code"}, status_code=403)
        _failures = 0

    auth = request.app.state.auth
    if request.app.state.runtime.settings.auth != "token" or not auth.token:
        return JSONResponse({"detail": "Token auth is not active on this backend"}, status_code=503)
    return JSONResponse({"token": auth.token})


async def mobile_overview(request: Request) -> JSONResponse:
    sessions = await _overview_sessions(request.app.state.runtime)
    return JSONResponse({"sessions": sessions, "generated_at": time.time()})


async def mobile_respond(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    request_id = str((body or {}).get("request_id") or "").strip() if isinstance(body, dict) else ""
    result = body.get("result") if isinstance(body, dict) else None
    if not request_id or not isinstance(result, dict):
        return JSONResponse({"detail": "request_id and an object result are required"}, status_code=400)
    runtime = request.app.state.runtime
    answer = await _gateway_rpc(runtime, "request.answer", {"id": request_id, "result": result})
    return JSONResponse({"status": answer.get("status", "ok")})


async def mobile_reply(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    sid = str((body or {}).get("session_id") or "").strip() if isinstance(body, dict) else ""
    text = str((body or {}).get("text") or "").strip() if isinstance(body, dict) else ""
    if not sid or not text:
        return JSONResponse({"detail": "session_id and text are required"}, status_code=400)
    if len(text) > _REPLY_MAX_CHARS:
        return JSONResponse({"detail": f"Reply exceeds {_REPLY_MAX_CHARS} characters"}, status_code=413)
    return JSONResponse(await _gateway_rpc(
        request.app.state.runtime, "prompt.submit", {"session_id": sid, "text": text}))


async def mobile_page() -> HTMLResponse:
    """The paired web client. Static shell only — every data call goes through
    the authed ``/api/mobile/*`` surface, so the page itself carries no secrets."""
    return HTMLResponse(_MOBILE_PAGE)


routes = [
    Route("/api/mobile/pairing", mint_pairing_code, methods=["POST"]),
    Route("/api/mobile/pair", pair_mobile, methods=["POST"]),
    Route("/api/mobile/overview", mobile_overview, methods=["GET"]),
    Route("/api/mobile/respond", mobile_respond, methods=["POST"]),
    Route("/api/mobile/reply", mobile_reply, methods=["POST"]),
    Route("/mobile", mobile_page, methods=["GET"]),
]




# Verbatim fork page (web_routers/mobile.py:262-460): the paired web client.
async def mobile_page():
    """The paired web client. Static shell only — every data call goes through
    the authed ``/api/mobile/*`` surface, so the page itself carries no secrets."""
    return HTMLResponse(_MOBILE_PAGE)


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
  .row { display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; }
  .grow { flex:1; min-width:0; }
  .mut { color:var(--mut); font-size:.8rem; }
  .pill { border:1px solid var(--line); border-radius:99px; padding:.05rem .5rem; font-size:.72rem; color:var(--mut); }
  .pill.wait { color:var(--warn); border-color:var(--warn); }
  .pill.run { color:var(--ok); border-color:var(--ok); }
  input[type=text], textarea, input[type=password] { width:100%; background:var(--bg); color:var(--fg);
     border:1px solid var(--line); border-radius:8px; padding:.55rem .65rem; font:inherit; font-size:.9rem; }
  button { background:var(--acc); color:#fff; border:0; border-radius:8px; padding:.5rem .8rem; font:inherit;
     font-size:.85rem; font-weight:600; cursor:pointer; }
  button.sec { background:transparent; border:1px solid var(--line); color:var(--fg); }
  button.deny { background:transparent; border:1px solid var(--warn); color:var(--warn); }
  button:disabled { opacity:.5; cursor:default; }
  .req { border-top:1px solid var(--line); margin-top:.6rem; padding-top:.6rem; }
  .req .sum { font-size:.85rem; white-space:pre-wrap; word-break:break-word; }
  .btns { display:flex; gap:.4rem; margin-top:.5rem; flex-wrap:wrap; }
  .err { color:#e06a6a; font-size:.8rem; margin-top:.4rem; }
  code { font-size:.8rem; }
</style>
</head>
<body>
<main>
  <h1>Hermes companion</h1>
  <div id="pair" class="card" hidden>
    <div class="mut">Enter the pairing code shown in the desktop app (Webhooks &rarr; Pair a phone),
      or sign in first if this deployment uses OAuth.</div>
    <div class="row" style="margin-top:.6rem">
      <input id="code" class="grow" type="text" inputmode="numeric" maxlength="6" placeholder="6-digit code">
      <button id="pairBtn">Pair</button>
    </div>
    <div id="pairErr" class="err"></div>
  </div>
  <div id="list"></div>
  <div class="mut" id="status"></div>
</main>
<script>
const API = {
  overview: '/api/mobile/overview',
  pair: '/api/mobile/pair',
  respond: '/api/mobile/respond',
  reply: '/api/mobile/reply'
};
let token = new URLSearchParams(location.hash.slice(1)).get('token')
  || sessionStorage.getItem('hermesMobileToken') || '';
if (token) sessionStorage.setItem('hermesMobileToken', token);
if (location.hash) history.replaceState(null, '', location.pathname);

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function call(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: Object.assign({'Content-Type': 'application/json'},
      token ? {Authorization: 'Bearer ' + token} : {}),
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (res.status === 401) { token = ''; sessionStorage.removeItem('hermesMobileToken'); showPair('Session expired — pair again.'); throw new Error('unauthorized'); }
  if (!res.ok) {
    let msg = res.status;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(String(msg));
  }
  return res.json();
}

function showPair(msg) {
  $('pair').hidden = false;
  if (msg) $('pairErr').textContent = msg;
}

async function doPair() {
  const code = $('code').value.trim();
  $('pairErr').textContent = '';
  try {
    const r = await call(API.pair, {code});
    token = r.token;
    sessionStorage.setItem('hermesMobileToken', token);
    $('pair').hidden = true;
    refresh();
  } catch (e) {
    $('pairErr').textContent = e.message;
  }
}

async function respond(requestId, result) {
  await call(API.respond, {request_id: requestId, result});
  await refresh();
}

async function sendReply(sid) {
  const el = document.getElementById('reply-' + sid);
  const text = (el.value || '').trim();
  if (!text) return;
  el.value = '';
  await call(API.reply, {session_id: sid, text});
  await refresh();
}

function reqCard(r) {
  const m = esc(r.method || '');
  let inner = '<div class="req"><div class="mut">' + m + '</div>';
  if (r.summary) inner += '<div class="sum">' + esc(r.summary) + '</div>';
  if (r.method === 'approval') {
    const choices = (r.choices && r.choices.length ? r.choices : ['once', 'deny']);
    inner += '<div class="btns">' + choices.map(c =>
      '<button class="' + (c === 'deny' ? 'deny' : 'sec') + '" data-act="choice" data-id="' + esc(r.id) +
      '" data-choice="' + esc(c) + '">' + esc(c) + '</button>').join('') + '</div>';
  } else if (r.method === 'clarify') {
    const qs = r.questions && r.questions.length ? r.questions : [{qid: '', question: r.summary || ''}];
    inner += qs.map(q =>
      '<div class="mut" style="margin-top:.4rem">' + esc(q.question || '') + '</div>' +
      '<input type="text" id="q-' + esc(r.id) + '-' + esc(q.qid) + '" placeholder="Answer">').join('') +
      '<div class="btns"><button data-act="clarify" data-id="' + esc(r.id) + '">Send answers</button></div>';
  } else {
    inner += '<input type="text" id="raw-' + esc(r.id) + '" placeholder=\'Result JSON, e.g. {"answer":"yes"}\'>' +
      '<div class="btns"><button class="sec" data-act="raw" data-id="' + esc(r.id) + '">Send</button></div>';
  }
  return inner + '</div>';
}

async function refresh() {
  if (!token) { showPair(); return; }
  try {
    const d = await call(API.overview);
    const list = $('list');
    if (!d.sessions.length) {
      list.innerHTML = '<div class="card mut">No live sessions on this backend.</div>';
    } else {
      list.innerHTML = d.sessions.map(s => {
        const state = s.pending
          ? '<span class="pill wait">waiting</span>'
          : (s.running ? '<span class="pill run">running</span>' : '<span class="pill">idle</span>');
        const reqs = (s.requests || []).map(reqCard).join('');
        return '<div class="card"><div class="row"><div class="grow"><b>' + esc(s.title || s.session_key || s.session_id) +
          '</b> <span class="mut">' + esc(s.profile || '') + '</span></div>' + state + '</div>' + reqs +
          '<div class="row" style="margin-top:.6rem"><input class="grow" type="text" id="reply-' + esc(s.session_id) +
          '" placeholder="Quick reply"><button class="sec" data-act="reply" data-id="' + esc(s.session_id) + '">Send</button></div></div>';
      }).join('');
    }
    $('status').textContent = 'Updated ' + new Date(d.generated_at * 1000).toLocaleTimeString();
  } catch (e) {
    if (e.message !== 'unauthorized') $('status').textContent = String(e.message);
  }
}

document.addEventListener('click', async ev => {
  const b = ev.target.closest('button[data-act]');
  if (!b) return;
  try {
    if (b.dataset.act === 'choice') {
      await respond(b.dataset.id, {choice: b.dataset.choice});
    } else if (b.dataset.act === 'clarify') {
      const answers = {};
      document.querySelectorAll('[id^="q-' + b.dataset.id + '-"]').forEach(el => {
        const qid = el.id.slice(b.dataset.id.length + 3);
        if (qid) answers[qid] = el.value;
      });
      await respond(b.dataset.id, Object.keys(answers).length ? {answers}
        : {answer: document.getElementById('q-' + b.dataset.id + '-')?.value || ''});
    } else if (b.dataset.act === 'raw') {
      const raw = document.getElementById('raw-' + b.dataset.id).value.trim();
      await respond(b.dataset.id, raw ? JSON.parse(raw) : {});
    } else if (b.dataset.act === 'reply') {
      await sendReply(b.dataset.id);
    }
  } catch (e) {
    $('status').textContent = String(e.message);
  }
});
$('pairBtn').addEventListener('click', doPair);
refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>
"""


