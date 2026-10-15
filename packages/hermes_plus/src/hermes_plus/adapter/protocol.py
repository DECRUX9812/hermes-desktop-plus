"""Measured wire contract for upstream's dashboard ``/api/ws`` endpoint.

Facts below were read from upstream ``web_routers/chat_ws.py``, ``web_server_chat.py``,
``web_server_boundary.py``, ``tui_gateway/ws.py``, ``server.py``, ``rpc_dispatch.py`` and
``contracts/events.py``. They describe the stock loopback dashboard, not the native
``hermes-gateway-v1`` transport.
"""

WS_PATH = "/api/ws"
TOKEN_QUERY_PARAMETER = "token"
METHOD_NOT_FOUND_CODE = -32601
READY_EVENT_NAME = "gateway.ready"

# Loopback binding to 127.0.0.1 accepts Host and HTTP(S) Origin hostnames
# localhost, 127.0.0.1, and ::1 (with a numeric port or no port). Origin may
# also be absent; non-http(s) origins are accepted. The peer itself must be
# loopback. A bad credential is closed with 4401 before accept, so Uvicorn
# reports an HTTP 403 handshake rejection rather than a post-upgrade close.
ALLOWED_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
AUTH_REJECTION_CLOSE_CODE = 4401
AUTH_REJECTION_HANDSHAKE_STATUS = 403

# The first post-accept frame is:
# {"jsonrpc":"2.0","method":"event","params":{"type":"gateway.ready",
#  "payload":{"skin":{},"change_events":true,"heartbeat":true,
#             "replay_epoch":"..."}}}
#
# Requests are {"jsonrpc":"2.0","id":<id>,"method":<name>,"params":{...}}.
# Responses echo id and contain either result or error {code,message,data?}.
# Notifications are {"jsonrpc":"2.0","method":"event","params":
#  {"type":<event-name>,"payload":{...},"session_id":<optional-id>}}.
EVENT_NAMES = frozenset({
    "gateway.ready", "skin.changed", "onboarding.ended", "setup.ready", "free_tier.challenge",
    "error", "notice", "message.start", "message.delta", "reasoning.delta", "reasoning.available",
    "thinking.delta", "message.interim", "message.complete", "status.update", "session.usage",
    "session.title", "reaction", "review.summary", "agent.print", "tool.start", "tool.complete",
    "tool.generating", "tool.output_risk", "todo.updated", "notification.show", "notification.clear",
    "tip.show", "session.info", "session.resume_progress", "session.reclaimed", "approval.cancelled",
    "session.control.update", "billing.step_up.verification", "background.complete", "btw.complete",
    "preview.restart.complete", "preview.restart.progress", "moa.reference", "moa.aggregating",
    "moa.progress", "moa.phase", "preview.open", "preview.close", "pane.reveal", "message.reaction",
    "agent.terminal.output", "terminal.close", "browser.progress", "browser.controller.command",
    "browser.controller.cancel", "voice.interrupted", "voice.status", "voice.partial",
    "voice.transcript", "wake.detected", "pet.changed", "pet.generate.progress", "pet.hatch.progress",
    "cron.changed", "sessions.changed", "platforms.changed", "projects.changed", "pairing.changed",
    "bot_relay.outbox.pending",
})
