"""The bridge plugin is discovered, enabled and register() ran, all through real Hermes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

LOADER_SCRIPT = """
import json
from hermes_cli.plugins import get_plugin_manager, render_system_prompt_sections
get_plugin_manager().discover_and_load()
from tools.registry import registry
from toolsets import resolve_toolset
print("TOOLSETS:" + json.dumps(sorted(registry.get_tool_names_for_toolset("bot_room"))))
sections = {section.id: section.content for section in render_system_prompt_sections({"session_id": "sess-test"})}
print("SECTIONS:" + json.dumps(sections))
print("RESOLVED:" + json.dumps({name: sorted(resolve_toolset(name))
                                for name in ("bot_room", "visualization", "git")}))
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
    for expected in ("hook.post_tool_call", "hook.pre_tool_call", "prompt.hermes-plus.policies",
                     "prompt.hermes-plus.team"):
        assert expected in bridge_json["features"], bridge_json["features"]

    toolset = result.stdout.strip().rsplit("TOOLSETS:", 1)[1].splitlines()[0].strip()
    registered = json.loads(toolset)
    for expected in ("bot_team", "bot_mailbox", "update_task"):
        assert expected in registered, f"{expected} was not registered by the bridge"

    # The registered prompt sections render through upstream's own renderer. The team section is
    # empty (and so omitted) while no team holds this profile; the skill-gated ones are installed.
    sections = json.loads(result.stdout.strip().rsplit("SECTIONS:", 1)[1].splitlines()[0].strip())
    assert "hermes-plus.policies" in sections
    assert "first-task" in sections["hermes-plus.first-task"]
    assert "initiate-setup" in sections["hermes-plus.initiate-setup"]
    # No team holds this profile in this fresh home, so the team section is empty and omitted.
    assert "hermes-plus.team" not in sections

    # The toolsets the bridge's tools declare resolve through stock Hermes' own toolset registry,
    # so a session that enables them (config `toolsets:`) really gets the tools.
    resolved = json.loads(result.stdout.strip().rsplit("RESOLVED:", 1)[1].strip())
    assert {"bot_team", "bot_mailbox", "update_task"} <= set(resolved["bot_room"])
    assert "create_visualization" in resolved["visualization"]
    assert {"git_ship", "open_pr"} <= set(resolved["git"])


def _stock_python() -> str:
    configured = os.environ.get("HERMES_PLUS_TEST_HERMES") or shutil.which("hermes") or "hermes"
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
