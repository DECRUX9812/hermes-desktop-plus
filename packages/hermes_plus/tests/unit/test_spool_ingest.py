"""Spool round trip: the bridge's real writer, the sidecar's real ingester, a real SQLite file."""

from __future__ import annotations

import json
import os
from pathlib import Path

from hermes_plus.bridge.spool import Spool
from hermes_plus.server.spool import SpoolIngester
from hermes_plus.store import BrowserThumbnails, DelegationReports, ToolCalls, open_store
from hermes_plus.store.bridge_records import report_outcome


def _screenshot(tmp_path: Path) -> Path:
    # A real 1x1 PNG.
    path = tmp_path / "shot.png"
    path.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"))
    return path


def test_spool_ingests_into_store_rows(tmp_path: Path) -> None:
    plus_home = tmp_path / "plus-home"
    store = open_store(plus_home)
    spool = Spool(plus_home)

    assert spool.append("tool_call", "sess-1", {
        "tool_name": "terminal", "tool_call_id": "call-1", "args": {"command": "ls"},
        "result": "file.txt", "status": "ok", "duration_ms": 12,
        "error_type": None, "error_message": None,
    })
    assert spool.append("delegation", "sess-1", {
        "delegation_id": "del-1", "state": "completed", "group": "research",
        "results": [{"status": "completed", "summary": "found it"},
                    {"status": "failed", "error": "boom"}],
        "child_session_ids": {"0": "child-a"}, "total_duration_seconds": 4.2,
        "duration_ms": 4200,
    })
    assert spool.append("browser_thumbnail", "sess-1", {
        "tool_call_id": "call-2", "tool_name": "browser_vision",
        "url": "https://example.com/", "title": "Example",
        "screenshot_path": str(_screenshot(tmp_path)), "captured_at": None, "snapshot": "<html>",
    })
    assert spool.append("approval_request", "sess-1", {
        "command": "rm -rf /tmp/demo", "description": "delete demo files",
        "session_key": "sess-1", "surface": "cli",
    })

    stats = SpoolIngester(store, plus_home).ingest_once()
    assert stats["tool_call"] == 1
    assert stats["delegation"] == 1
    assert stats["browser_thumbnail"] == 1
    assert stats["approval_request"] == 1
    assert stats["skipped"] == 0

    calls = ToolCalls(store).list_for_session("sess-1")
    assert [call["tool_call_id"] for call in calls] == ["call-1"]
    assert calls[0]["tool_name"] == "terminal"
    assert calls[0]["status"] == "ok"

    reports = DelegationReports(store).list_for_session("sess-1")
    assert len(reports) == 1
    report = reports[0]
    assert report["delegation_id"] == "del-1"
    assert report["outcome"] == "needs_decision"
    assert report["task_count"] == 2 and report["completed_count"] == 1 and report["failed_count"] == 1
    assert report["duration_seconds"] == 4.2
    assert report["child_session_ids"] == {"0": "child-a"}
    assert report["group"] == "research"

    thumb = BrowserThumbnails(store).get("call-2")
    assert thumb is not None and thumb["url"] == "https://example.com/"
    kept = Path(thumb["image_path"])
    assert kept.is_file() and kept.stat().st_size > 0
    assert str(plus_home / "files" / "thumbs") in str(kept)


def test_restart_does_not_double_ingest(tmp_path: Path) -> None:
    plus_home = tmp_path / "plus-home"
    store = open_store(plus_home)
    spool = Spool(plus_home)
    for index in range(3):
        assert spool.append("tool_call", "sess-1", {"tool_call_id": f"call-{index}"})

    first = SpoolIngester(store, plus_home).ingest_once()
    second = SpoolIngester(store, plus_home).ingest_once()
    assert first["tool_call"] == 3
    assert second["tool_call"] == 0
    assert len(ToolCalls(store).list_for_session("sess-1")) == 3


def test_malformed_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    plus_home = tmp_path / "plus-home"
    store = open_store(plus_home)
    spool = Spool(plus_home)
    assert spool.append("tool_call", "sess-1", {"tool_call_id": "call-1"})
    with open(plus_home / "spool" / "999999-decoy.jsonl", "ab") as handle:
        handle.write(b"{not json\n")

    stats = SpoolIngester(store, plus_home).ingest_once()
    assert stats["tool_call"] == 1
    assert stats["skipped"] == 1
    assert len(ToolCalls(store).list_for_session("sess-1")) == 1


def test_fully_ingested_old_files_are_deleted(tmp_path: Path) -> None:
    plus_home = tmp_path / "plus-home"
    store = open_store(plus_home)
    spool = Spool(plus_home)
    assert spool.append("tool_call", "sess-1", {"tool_call_id": "call-1"})
    old_file = next((plus_home / "spool").glob("*.jsonl"))
    ancient = old_file.stat().st_mtime_ns - 90 * 60 * 60 * 1_000_000_000  # 90h ago
    os.utime(old_file, ns=(ancient, ancient))

    stats = SpoolIngester(store, plus_home).ingest_once()
    assert stats["tool_call"] == 1
    assert not old_file.exists()


def test_report_outcome_buckets() -> None:
    done = [{"status": "completed"}, {"status": "success"}]
    failed = [{"status": "failed"}, {"status": "timeout"}]
    mixed = [{"status": "completed"}, {"status": "error"}]
    assert report_outcome(done) == "done"
    assert report_outcome(failed) == "failed"
    assert report_outcome(mixed) == "needs_decision"
    assert report_outcome([{"status": "completed", "truncated": True}]) == "needs_decision"


async def test_fork_browser_rpc_serves_the_ingested_thumbnail(tmp_path: Path) -> None:
    import base64

    from hermes_plus.rpc import RpcContext, handle_text

    plus_home = tmp_path / "plus-home"
    store = open_store(plus_home)
    spool = Spool(plus_home)
    screenshot = _screenshot(tmp_path)
    assert spool.append("browser_thumbnail", "sess-1", {
        "tool_call_id": "call-9", "tool_name": "browser_vision",
        "url": "https://example.com/", "title": "Example",
        "screenshot_path": str(screenshot), "captured_at": None, "snapshot": "page text",
    })
    stats = SpoolIngester(store, plus_home).ingest_once()
    assert stats["browser_thumbnail"] == 1

    ctx = RpcContext(store)
    thumb = json.loads(await handle_text(ctx, json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "fork.browser.thumbnail",
        "params": {"tool_call_id": "call-9"}})))
    result = thumb["result"]
    assert result["found"] is True
    assert result["mime"] == "image/png"
    assert base64.b64decode(result["data_base64"]) == screenshot.read_bytes()
    assert result["url"] == "https://example.com/"

    snap = json.loads(await handle_text(ctx, json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "fork.browser.snapshot",
        "params": {"tool_call_id": "call-9"}})))
    assert snap["result"] == {"found": True, "text": "page text",
                              "url": "https://example.com/", "title": "Example"}


async def test_fork_computer_timeline_derives_steps_from_tool_calls(tmp_path: Path) -> None:
    from hermes_plus.rpc import RpcContext, handle_text

    plus_home = tmp_path / "plus-home"
    store = open_store(plus_home)
    spool = Spool(plus_home)
    assert spool.append("tool_call", "sess-1", {
        "tool_name": "computer", "tool_call_id": "step-1", "args": {"command": "xdotool click"},
        "result": json.dumps({"success": True, "output": "clicked"}), "status": "ok",
        "duration_ms": 1500, "error_type": None, "error_message": None,
    })
    stats = SpoolIngester(store, plus_home).ingest_once()
    assert stats["tool_call"] == 1

    ctx = RpcContext(store)
    reply = json.loads(await handle_text(ctx, json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "fork.computer.timeline",
        "params": {"profile": "default", "session_id": "sess-1"}})))
    result = reply["result"]
    assert result["session_id"] == "sess-1"
    step = result["steps"][0]
    assert step["id"] == "step-1" and step["index"] == 0
    # The fork's kind rules: computer-use tools are neither terminal, file, browser nor search.
    assert step["kind"] == "other"
    assert step["status"] == "done"
    assert step["detail"]["command"] == "xdotool click"


async def test_computer_step_detail_rides_the_tool_call_into_the_timeline(tmp_path: Path) -> None:
    """The bridge's normalized step detail (a computer_step record) is merged into the timeline."""
    from hermes_plus.rpc import RpcContext, handle_text

    plus_home = tmp_path / "plus-home"
    store = open_store(plus_home)
    spool = Spool(plus_home)
    assert spool.append("tool_call", "sess-1", {
        "tool_name": "terminal", "tool_call_id": "step-2", "args": {"command": "git status"},
        "result": "On branch ship/s7\nnothing to commit", "status": "ok",
        "duration_ms": 900, "error_type": None, "error_message": None,
    })
    assert spool.append("computer_step", "sess-1", {
        "tool_call_id": "step-2", "tool_name": "terminal", "status": "ok", "error_message": None,
        "detail": {"command": "git status", "path": "/tmp/demo/repo"},
    })
    stats = SpoolIngester(store, plus_home).ingest_once()
    assert stats["tool_call"] == 1 and stats["computer_step"] == 1 and stats["skipped"] == 0

    stored = ToolCalls(store).list_for_session("sess-1")[0]
    assert json.loads(stored["detail_json"]) == {"command": "git status", "path": "/tmp/demo/repo"}

    reply = json.loads(await handle_text(RpcContext(store), json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "fork.computer.timeline",
        "params": {"profile": "default", "session_id": "sess-1"}})))
    step = reply["result"]["steps"][0]
    assert step["kind"] == "terminal"
    assert step["title"] == "Ran git status"
    assert step["detail"]["command"] == "git status"
    assert step["detail"]["path"] == "/tmp/demo/repo"
    # The derived detail still fills what the bridge did not send.
    assert step["detail"]["output_tail"] == "On branch ship/s7\nnothing to commit"
