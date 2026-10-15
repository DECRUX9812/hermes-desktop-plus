"""The bridge's hooks, dispatched by Hermes' own hook dispatcher, land in the sidecar's store.

Chain under test: upstream ``invoke_hook`` / ``_dispatch_pre_tool_call_hooks`` -> the bridge's
registered callbacks -> a real spool file under HERMES_HOME -> ``SpoolIngester`` -> plus.db rows.
No model is involved: the hooks are the real dispatch path a tool call takes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_plus.server.spool import SpoolIngester
from hermes_plus.store import ApprovalJournal, DelegationReports, Store, ToolCalls, open_store

pytestmark = pytest.mark.integration

DISPATCH_HOOKS = """
import json
from hermes_cli.plugins import get_plugin_manager, _dispatch_pre_tool_call_hooks
from hermes_cli.lifecycle import invoke_hook
get_plugin_manager().discover_and_load()

invoke_hook("post_tool_call", tool_name="terminal", args={"command": "ls"}, result="file.txt",
            session_id="sess-1", tool_call_id="call-1", turn_id="turn-1", task_id="task-1",
            api_request_id="api-1", duration_ms=7, status="ok", error_type=None,
            error_message=None, middleware_trace=[])
invoke_hook("post_tool_call", tool_name="delegate_task", args={"tasks": []},
            result=json.dumps({"delegation_id": "del-1", "state": "completed",
                               "results": [{"status": "completed", "summary": "found the answer"}]}),
            session_id="sess-1", tool_call_id="call-2", turn_id="turn-1", task_id="task-1",
            api_request_id="api-1", duration_ms=1200, status="ok", error_type=None,
            error_message=None, middleware_trace=[])
invoke_hook("pre_approval_request", command="rm -rf /tmp/demo", description="delete demo files",
            pattern_key="rm", session_key="sess-1", surface="cli", session_id="sess-1",
            tool_call_id="call-3", turn_id="turn-1")
invoke_hook("post_approval_response", command="rm -rf /tmp/demo", description="delete demo files",
            pattern_key="rm", session_key="sess-1", surface="cli", session_id="sess-1",
            tool_call_id="call-3", turn_id="turn-1", choice="once")

blocks = []
for index in range(7):
    message, _args = _dispatch_pre_tool_call_hooks(
        "terminal", {"command": "pwd"}, task_id="task-1", session_id="sess-1",
        tool_call_id=f"call-repeat-{index}", turn_id="turn-2", api_request_id="api-2",
        middleware_trace=[])
    blocks.append(message)
print("BLOCKS:" + json.dumps(blocks))
"""


def _stock_python() -> str:
    configured = os.environ.get("HERMES_PLUS_TEST_HERMES") or shutil.which("hermes") or "hermes"
    python = Path(configured).resolve().parent / "python3"
    if not python.is_file():
        pytest.skip(f"stock Hermes venv has no python3 next to the launcher: {python}")
    return str(python)


def _dispatch(installed_home: Path) -> list[str | None]:
    result = subprocess.run(
        [_stock_python(), "-c", DISPATCH_HOOKS],
        env={**os.environ, "HERMES_HOME": str(installed_home)},
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().rsplit("BLOCKS:", 1)[1].strip())


@pytest.fixture(scope="module")
def ingested(installed_home: Path) -> Store:
    """Run the hooks in the real Hermes process, then ingest the spool the bridge wrote."""
    plus_home = installed_home / "hermes-plus"
    spool_files = list((plus_home / "spool").glob("*.jsonl"))
    assert not spool_files, "a fresh home must start without spool files"

    blocks = _dispatch(installed_home)
    # The retry-loop policy: six identical calls run, the seventh is refused with the diagnostic.
    assert blocks[:6] == [None] * 6, blocks
    assert blocks[6] and "retry loop" in blocks[6], blocks

    spool_files = sorted((plus_home / "spool").glob("*.jsonl"))
    assert spool_files, "the bridge wrote no spool file"
    kinds = {json.loads(line)["kind"] for line in spool_files[0].read_text().splitlines()}
    assert {"tool_call", "delegation", "approval_request", "approval_decision"} <= kinds

    store = open_store(plus_home)
    stats = SpoolIngester(store, plus_home).ingest_once()
    assert stats["skipped"] == 0, stats
    assert stats["tool_call"] == 2 and stats["delegation"] == 1
    assert stats["approval_request"] == 1 and stats["approval_decision"] == 1
    return store


def test_tool_calls_and_delegations_reach_the_store(ingested: Store) -> None:
    calls = ToolCalls(ingested).list_for_session("sess-1")
    assert [call["tool_call_id"] for call in calls] == ["call-1", "call-2"]
    assert calls[0]["tool_name"] == "terminal" and calls[0]["status"] == "ok"
    assert calls[0]["duration_ms"] == 7
    # result_excerpt/args_json hold JSON, so a string result keeps its quotes.
    assert json.loads(calls[0]["result_excerpt"]) == "file.txt"

    reports = DelegationReports(ingested).list_for_session("sess-1")
    assert len(reports) == 1
    report = reports[0]
    assert report["delegation_id"] == "del-1" and report["outcome"] == "done"
    assert report["summary"] == "found the answer" and report["task_count"] == 1


def test_the_approval_journal_records_request_and_decision(ingested: Store) -> None:
    entries = ApprovalJournal(ingested).list_for_session("sess-1")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["command"] == "rm -rf /tmp/demo"
    assert entry["tool_call_id"] == "call-3"
    assert entry["choice"] == "once" and entry["status"] == "allowed"
