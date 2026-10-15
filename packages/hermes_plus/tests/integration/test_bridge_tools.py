"""The registered bot_team tool, loaded by Hermes' own plugin loader, reaches the real sidecar.

Chain under test: bridge tool handler -> loopback POST /api/plus/bridge/rpc (bridge token) ->
rpc.registry -> plus.db, observed back over the sidecar's /api/ws multiplexer.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import time
from pathlib import Path

import pytest

from hermes_plus.adapter import GatewayClient

pytestmark = pytest.mark.integration

LOADER_AND_CALL = """
import json
from hermes_cli.plugins import get_plugin_manager
get_plugin_manager().discover_and_load()
from tools.registry import registry
entry = registry.get_entry("bot_team")
result = entry.handler(
    {"action": "create", "params": {"name": "demo-team", "mission": "prove the bridge"}},
    task_id="test-task")
print("TOOL_RESULT:" + json.dumps(result))
"""

RENDER_SECTIONS = """
import json
from hermes_cli.plugins import get_plugin_manager, render_system_prompt_sections
get_plugin_manager().discover_and_load()
sections = {section.id: section.content for section in render_system_prompt_sections({"session_id": "sess-test"})}
print("SECTIONS:" + json.dumps(sections))
"""


def _free_port() -> int:
    for port in range(9300, 9400):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.skip("no free loopback port in 9300-9399")


@pytest.fixture
def sidecar(installed_home: Path, plus_bin: str, stock_hermes: str):
    port = _free_port()
    process = subprocess.Popen(
        [plus_bin, "serve", "--host", "127.0.0.1", "--port", str(port),
         "--hermes", stock_hermes],
        env={**os.environ, "HERMES_HOME": str(installed_home)},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sidecar_json = installed_home / "hermes-plus" / "sidecar.json"
    deadline = time.monotonic() + 240
    try:
        while time.monotonic() < deadline:
            if sidecar_json.is_file():
                break
            if process.poll() is not None:
                pytest.fail(f"sidecar exited early: {process.stdout.read() if process.stdout else ''}")
            time.sleep(1)
        else:
            pytest.fail("sidecar never wrote sidecar.json")
        # The Runtime's contact card: loopback URL, the bridge token, this pid, mode 0600.
        assert stat.S_IMODE(sidecar_json.stat().st_mode) == 0o600
        contact = json.loads(sidecar_json.read_text())
        assert contact["url"] == f"http://127.0.0.1:{port}"
        assert contact["pid"] == process.pid
        assert contact["token"]
        yield contact
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not sidecar_json.exists(), "the Runtime must remove sidecar.json on stop"


async def test_bot_team_tool_round_trips_through_the_sidecar(installed_home: Path,
                                                             sidecar: dict) -> None:
    stock_python = _stock_python()
    result = subprocess.run(
        [stock_python, "-c", LOADER_AND_CALL],
        env={**os.environ, "HERMES_HOME": str(installed_home)},
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    payload = json.loads(result.stdout.strip().rsplit("TOOL_RESULT:", 1)[1])
    # Tool handlers return a JSON string (stock convention); the loader print
    # above JSON-encodes it once more.
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert "error" not in payload, payload
    team = payload.get("team", payload)
    assert team.get("name") == "demo-team"

    client = GatewayClient(_ws_url(sidecar), sidecar["token"])
    try:
        await client.connect()
        reply = await client.call("bots_team.list", {}, timeout=30)
    finally:
        await client.close()
    names = [t.get("name") for t in reply.get("teams", [])]
    assert "demo-team" in names


def _ws_url(sidecar: dict) -> str:
    """The sidecar's contact card carries an http URL; the gateway client wants ws://."""
    return sidecar["url"].rstrip("/").replace("http://", "ws://", 1).replace(
        "https://", "wss://", 1) + "/api/ws"


async def test_the_team_prompt_section_renders_this_profile_seat(installed_home: Path,
                                                                 sidecar: dict) -> None:
    """The fork's tools/bot_team.py::prompt_section: a profile with a seat gets its team brief."""
    client = GatewayClient(_ws_url(sidecar), sidecar["token"])
    try:
        await client.connect()
        created = await client.call("bots_team.create", {
            "name": "demo-team", "mission": "ship the bridge"}, timeout=30)
        team_id = created["team"]["id"]
        await client.call("bots_team.member.upsert", {
            "team_id": team_id, "profile": "default", "role": "engineer", "title": "Bridge engineer",
            "lead": True}, timeout=30)
    finally:
        await client.close()

    rendered = subprocess.run(
        [_stock_python(), "-c", RENDER_SECTIONS],
        env={**os.environ, "HERMES_HOME": str(installed_home)},
        capture_output=True, text=True, timeout=300)
    assert rendered.returncode == 0, rendered.stderr[-2000:]
    sections = json.loads(rendered.stdout.strip().rsplit("SECTIONS:", 1)[1].strip())
    team = sections["hermes-plus.team"]
    assert team.startswith("## Team")
    assert "ship the bridge" in team and "Bridge engineer" in team


def _stock_python() -> str:
    configured = os.environ.get(
        "HERMES_PLUS_TEST_HERMES",
        str(Path.home() / "Code" / "ship-test" / "venv" / "bin" / "hermes"),
    )
    python = Path(configured).resolve().parent / "python3"
    if not python.is_file():
        pytest.skip(f"stock Hermes venv has no python3 next to the launcher: {python}")
    return str(python)
