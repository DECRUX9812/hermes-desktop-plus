"""The bridge plugin is discovered, enabled and register() ran, all through real Hermes."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

LOADER_SCRIPT = """
import json
from hermes_cli.plugins import get_plugin_manager
get_plugin_manager().discover_and_load()
from tools.registry import registry
print("TOOLSETS:" + json.dumps(sorted(registry.get_tool_names_for_toolset("bot_room"))))
"""


def test_install_enabled_the_bridge(installed_home: Path, stock_hermes: str) -> None:
    listing = subprocess.run(
        [stock_hermes, "plugins", "list", "--json", "--no-bundled"],
        env={**os.environ, "HERMES_HOME": str(installed_home)},
        capture_output=True, text=True, timeout=300, check=True)
    entries = json.loads(listing.stdout)
    bridge = next(entry for entry in entries if entry["name"] == "hermes-plus")
    assert bridge["status"] == "enabled"


def test_register_ran_through_upstreams_loader(installed_home: Path) -> None:
    stock_python = _stock_python()
    result = subprocess.run(
        [stock_python, "-c", LOADER_SCRIPT],
        env={**os.environ, "HERMES_HOME": str(installed_home)},
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]

    bridge_json = json.loads((installed_home / "hermes-plus" / "bridge.json").read_text())
    assert bridge_json["plugin"] == "hermes-plus"
    assert bridge_json["version"]
    assert "hook.post_tool_call" in bridge_json["features"]
    assert "prompt.hermes-plus.policies" in bridge_json["features"]

    toolset = result.stdout.strip().rsplit("TOOLSETS:", 1)[1].strip()
    registered = json.loads(toolset)
    for expected in ("bot_team", "bot_mailbox", "update_task"):
        assert expected in registered, f"{expected} was not registered by the bridge"


def _stock_python() -> str:
    configured = os.environ.get(
        "HERMES_PLUS_TEST_HERMES",
        str(Path.home() / "Code" / "ship-test" / "venv" / "bin" / "hermes"),
    )
    binary = Path(configured).resolve()
    python = binary.parent / "python3"
    if not python.is_file():
        pytest.skip(f"stock Hermes venv has no python3 next to the launcher: {python}")
    return str(python)


def test_install_is_idempotent_and_keeps_user_skills(installed_home: Path, plus_bin: str,
                                                     stock_hermes: str) -> None:
    skill = installed_home / "skills" / "first-task" / "SKILL.md"
    skill.write_text("# user edited this skill\n", encoding="utf-8")
    result = subprocess.run(
        [plus_bin, "install", "--hermes", stock_hermes],
        env={**os.environ, "HERMES_HOME": str(installed_home)},
        capture_output=True, text=True, timeout=600, check=True)
    assert "Kept user-modified skill 'first-task'." in result.stdout
    assert skill.read_text(encoding="utf-8") == "# user edited this skill\n"
