"""Event taps record approvals and tool activity into a real plus.db."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_plus.server.runtime import Runtime
from hermes_plus.server.taps import TAPS, TapContext
from hermes_plus.settings import ServeSettings
from hermes_plus.store.approvals import ApprovalJournal


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    instance = Runtime(ServeSettings(auth="none", ui_dir=tmp_path / "ui"))
    yield instance
    for store in list(instance._stores.values()):
        store.close()
    instance._stores.clear()


@pytest.fixture
def context(runtime: Runtime) -> TapContext:
    return TapContext(runtime, None)


def _tap(context: TapContext, frame: dict) -> None:
    for tap in TAPS:
        tap(context, frame)


def test_approval_request_and_client_response_are_journaled(runtime, context):
    _tap(context, {
        "jsonrpc": "2.0",
        "id": "srq-7",
        "method": "approval",
        "params": {
            "session_id": "sess-1",
            "request_id": "req-1",
            "command": "ls -la",
            "description": "List files",
            "tool_name": "terminal",
            "tool_call_id": "tc-1",
        },
    })
    assert context.approvals == {"srq-7": "req-1"}
    _tap(context, {"jsonrpc": "2.0", "id": "srq-7", "result": {"choice": "once"}})

    journal = ApprovalJournal(runtime.store_for(None))
    rows = journal.list_for_session("sess-1")
    assert len(rows) == 1
    assert rows[0]["request_id"] == "req-1"
    assert rows[0]["status"] == "allowed"
    assert rows[0]["choice"] == "once"
    assert rows[0]["command"] == "ls -la"
    assert rows[0]["tool_call_id"] == "tc-1"


def test_approval_cancelled_event_withdraws_pending(runtime, context):
    _tap(context, {
        "jsonrpc": "2.0",
        "id": "srq-9",
        "method": "approval",
        "params": {"session_id": "sess-2", "request_id": "req-2", "command": "rm x"},
    })
    _tap(context, {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "approval.cancelled",
                   "payload": {"request_id": "req-2", "reason": "interrupted"}},
    })
    row = ApprovalJournal(runtime.store_for(None)).get("req-2")
    assert row["status"] == "withdrawn"
    assert row["reason"] == "interrupted"


def test_tool_events_are_recorded(runtime, context):
    _tap(context, {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "tool.start",
                   "payload": {"tool_id": "tc-9", "name": "terminal", "context": {}},
                   "session_id": "sess-3"},
    })
    _tap(context, {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "tool.complete",
                   "payload": {"tool_id": "tc-9", "name": "terminal", "duration_s": 1.5},
                   "session_id": "sess-3"},
    })
    store = runtime.store_for(None)
    with store.read() as conn:
        rows = conn.execute(
            "SELECT event_name, tool_name, payload_json FROM tool_activity "
            "WHERE session_id = 'sess-3' ORDER BY event_name"
        ).fetchall()
    assert [row["event_name"] for row in rows] == ["tool.complete", "tool.start"]
    assert rows[0]["tool_name"] == "terminal"
    assert json.loads(rows[1]["payload_json"])["tool_id"] == "tc-9"


def test_taps_ignore_unrelated_frames(runtime, context):
    _tap(context, {"jsonrpc": "2.0", "method": "event",
                   "params": {"type": "message.delta", "payload": {"text": "hi"}}})
    _tap(context, {"jsonrpc": "2.0", "id": "client-1", "result": {"ok": True}})
    store = runtime.store_for(None)
    with store.read() as conn:
        approvals = conn.execute("SELECT COUNT(*) AS n FROM approvals").fetchone()["n"]
        tools = conn.execute("SELECT COUNT(*) AS n FROM tool_activity").fetchone()["n"]
    assert approvals == 0
    assert tools == 0


def test_store_for_is_lazy_and_cached(runtime):
    first = runtime.store_for(None)
    assert first is runtime.store_for(None)
    assert first.path.name == "plus.db"
    assert first.schema_version() >= 2


def test_store_for_profiles_get_separate_databases(runtime):
    default = runtime.store_for(None)
    named = runtime.store_for("demo")
    assert default is not named
    assert named.path.parent.parent.name == "demo"
    assert default.path.parent.parent.name != "demo"
