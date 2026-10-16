"""Extras plugins follow the plugin settings rule (S35): tuning knobs are declared in
config_schema, read via ctx.get_config with the legacy HERMES_LANGFUSE_* env vars kept
as a deprecated back-compat layer. Proven against the real stock plugin loader in a
throwaway HERMES_HOME — no mocks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_resolve import prepend_bin_dir, resolve_stock_hermes

pytestmark = pytest.mark.integration

LANGFUSE_SRC = Path(__file__).resolve().parents[2] / "src" / "hermes_plus" / "extras" / "plugins" / "langfuse"

# Runs in the STOCK venv's python: stock's own plugin discovery loads the plugin
# (bundled-user dir scan, plugins.enabled gate) and calls register(ctx) with a genuine
# PluginContext; the plugin's own knob reader then resolves the value.
_READ_KNOB = (
    "import json, sys\n"
    "from hermes_cli.plugins import discover_plugins, get_plugin_manager\n"
    "discover_plugins()\n"
    "loaded = get_plugin_manager()._plugins.get('langfuse')\n"
    "if loaded is None or loaded.module is None:\n"
    "    print('RESULT:' + json.dumps({'error': 'langfuse not loaded',\n"
    "          'loaded': sorted(get_plugin_manager()._plugins)}))\n"
    "    sys.exit(0)\n"
    "mod = loaded.module\n"
    "print('RESULT:' + json.dumps({'capture': mod._knob('capture'),\n"
    "                              'mode': mod._capture_mode()}))\n"
)


def _read_capture(stock_python: str, home: Path, env_extra: dict[str, str]) -> dict:
    result = subprocess.run(
        [stock_python, "-c", _READ_KNOB],
        env=prepend_bin_dir({**os.environ, "HERMES_HOME": str(home), **env_extra}),
        capture_output=True, text=True, timeout=300, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    line = next(row for row in result.stdout.splitlines() if row.startswith("RESULT:"))
    return json.loads(line[len("RESULT:"):].strip())


@pytest.fixture(scope="module")
def langfuse_home(stock_hermes: str, tmp_path_factory) -> Path:
    """A throwaway HERMES_HOME with the langfuse extra installed and enabled."""
    home = tmp_path_factory.mktemp("langfuse-settings-home")
    (home / "plugins").mkdir()
    shutil.copytree(LANGFUSE_SRC, home / "plugins" / "langfuse")
    result = subprocess.run(
        [stock_hermes, "plugins", "enable", "langfuse"],
        env={**os.environ, "HERMES_HOME": str(home)},
        capture_output=True, text=True, timeout=600, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return home


@pytest.fixture(scope="module")
def stock_python(stock_hermes: str) -> str:
    python = Path(stock_hermes).resolve().parent / "python3"
    if not python.is_file():
        pytest.skip(f"stock Hermes venv has no python3 next to the launcher: {python}")
    return str(python)


def _set_capture(stock_hermes: str, home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [stock_hermes, "config", "set", "plugins.entries.langfuse.settings.capture", *args],
        env={**os.environ, "HERMES_HOME": str(home)},
        capture_output=True, text=True, timeout=300, check=True)


def test_langfuse_knob_read_from_config_schema(langfuse_home: Path, stock_hermes: str,
                                               stock_python: str) -> None:
    """A knob set through the real `hermes config set` reaches the plugin's reader."""
    result = _set_capture(stock_hermes, langfuse_home, "full")
    assert "capture" in result.stdout

    resolved = _read_capture(stock_python, langfuse_home, {})
    assert resolved == {"capture": "full", "mode": "full"}


def test_langfuse_setting_outranks_legacy_env_var(langfuse_home: Path, stock_hermes: str,
                                                  stock_python: str) -> None:
    """The deprecated env var loses to an explicit plugin setting."""
    _set_capture(stock_hermes, langfuse_home, "full")
    resolved = _read_capture(stock_python, langfuse_home, {"HERMES_LANGFUSE_CAPTURE": "metadata"})
    assert resolved == {"capture": "full", "mode": "full"}


def test_langfuse_legacy_env_var_still_honoured(langfuse_home: Path, stock_hermes: str,
                                                stock_python: str) -> None:
    """With no setting, the legacy env var (deprecated) is the middle read."""
    subprocess.run(
        [stock_hermes, "config", "unset", "plugins.entries.langfuse.settings.capture"],
        env={**os.environ, "HERMES_HOME": str(langfuse_home)},
        capture_output=True, text=True, timeout=300, check=True)
    resolved = _read_capture(stock_python, langfuse_home, {"HERMES_LANGFUSE_CAPTURE": "metadata"})
    assert resolved == {"capture": "metadata", "mode": "metadata"}

    # No setting, no env var: the raw knob reads empty and the capture mode falls to its default.
    default = _read_capture(stock_python, langfuse_home, {})
    assert default == {"capture": "", "mode": "sanitized"}
