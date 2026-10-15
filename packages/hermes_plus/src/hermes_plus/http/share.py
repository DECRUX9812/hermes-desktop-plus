"""Session sharing with temporary public links."""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

log = logging.getLogger(__name__)


async def create_share(request: Request) -> Any:
    """Create a new share grant for a session."""
    runtime = request.app.state.runtime
    try:
        body = await request.json()
        session_id = (body or {}).get("session_id", "").strip()
        if not session_id:
            return JSONResponse({"detail": "session_id required"}, status_code=400)

        share_id = secrets.token_urlsafe(16)
        now = time.time()

        store = runtime.store_for()
        with store.write() as conn:
            conn.execute(
                "INSERT INTO share_grants (id, session_id, shared_at, created_by) VALUES (?, ?, ?, ?)",
                (share_id, session_id, now, "web"),
            )

        return JSONResponse({
            "id": share_id,
            "session_id": session_id,
            "shared_at": now,
            "url": f"/share/view?token={share_id}",
        })
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    except Exception as exc:
        log.exception("Share creation failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)


async def list_shares(request: Request) -> Any:
    """List all active share grants."""
    runtime = request.app.state.runtime
    try:
        store = runtime.store_for()
        with store.read() as conn:
            rows = conn.execute(
                "SELECT id, session_id, shared_at FROM share_grants WHERE revoked_at IS NULL ORDER BY shared_at DESC"
            ).fetchall()

        return JSONResponse({
            "shares": [
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "shared_at": row["shared_at"],
                }
                for row in rows
            ]
        })
    except Exception as exc:
        log.exception("Share list failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)


async def revoke_share(request: Request) -> Any:
    """Revoke a share grant."""
    runtime = request.app.state.runtime
    try:
        body = await request.json()
        share_id = (body or {}).get("id", "").strip()
        if not share_id:
            return JSONResponse({"detail": "id required"}, status_code=400)

        now = time.time()
        store = runtime.store_for()
        with store.write() as conn:
            cursor = conn.execute(
                "UPDATE share_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, share_id),
            )
            if cursor.rowcount == 0:
                return JSONResponse({"detail": "Share not found"}, status_code=404)

        return JSONResponse({"ok": True})
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    except Exception as exc:
        log.exception("Share revocation failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)


async def view_share_api(request: Request) -> Any:
    """Get a shared session transcript (API, public with token)."""
    runtime = request.app.state.runtime
    try:
        token = request.query_params.get("token", "").strip()
        if not token:
            return JSONResponse({"detail": "token required"}, status_code=400)

        store = runtime.store_for()
        with store.read() as conn:
            row = conn.execute(
                "SELECT session_id FROM share_grants WHERE id = ? AND revoked_at IS NULL",
                (token,),
            ).fetchone()

        if not row:
            return JSONResponse({"detail": "Share not found or revoked"}, status_code=404)

        session_id = row["session_id"]

        if not runtime.internal:
            return JSONResponse({"detail": "Gateway unavailable"}, status_code=503)

        try:
            history = await runtime.internal.call(
                "session.history",
                {"session_id": session_id},
                timeout=10,
            )
            return JSONResponse({"session_id": session_id, "transcript": history})
        except Exception as exc:
            log.exception("Failed to fetch session history")
            return JSONResponse({"detail": "Failed to fetch session"}, status_code=500)
    except Exception as exc:
        log.exception("Share view failed")
        return JSONResponse({"detail": str(exc)}, status_code=500)


async def view_share_page(request: Request) -> Any:
    """Serve the shared session viewer page (public)."""
    token = request.query_params.get("token", "").strip()
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset=utf-8><title>Shared Session</title></head>
<body style="font-family:monospace; white-space:pre-wrap; word-wrap:break-word; margin:1rem;">
<h1>Shared Session</h1>
<p>Token: {token if token else '(none)'}</p>
<p><a href="/api/share/view?token={token}">View as JSON</a></p>
</body></html>
""")


routes = [
    Route("/api/share/create", create_share, methods=["POST"]),
    Route("/api/share/list", list_shares, methods=["GET"]),
    Route("/api/share/revoke", revoke_share, methods=["POST"]),
    Route("/api/share/view", view_share_api, methods=["GET"]),
    Route("/share/view", view_share_page, methods=["GET"]),
]
