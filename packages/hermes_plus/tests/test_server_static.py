"""Static UI serving: runtime-global injection, escaping, and cache rules."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_plus import __version__
from hermes_plus.server import static
from hermes_plus.server.runtime import Runtime
from hermes_plus.server.static import _HASHED_ASSET, _inject, _script_escape, _ui_root
from hermes_plus.settings import ServeSettings


def _runtime(tmp_path: Path, **kwargs) -> Runtime:
    settings = ServeSettings(ui_dir=tmp_path, **kwargs)
    return Runtime(settings)


def _injected_plus_config(html: str) -> dict:
    """The `__HERMES_PLUS__` object the bootstrap script assigns, as parsed JSON.

    Asserting on the serialised text would pin the JSON spacing; the renderer
    reads the parsed value, so the tests read it the same way.
    """
    match = re.search(r"window\.__HERMES_PLUS__=(\{.*?\});", html, re.S)
    assert match is not None, html
    return json.loads(match.group(1))


def test_script_escape_blocks_script_breakout():
    assert "</" not in _script_escape("</script><script>alert(1)</script>")
    assert _script_escape("plain") == '"plain"'


def test_inject_adds_globals_before_head_close(tmp_path: Path):
    runtime = _runtime(tmp_path)
    html = _inject("<!doctype html><html><head><title>t</title></head><body></body></html>",
                   runtime.settings, runtime)
    assert html.index("<script>") < html.index("</head>")
    assert f'window.__HERMES_SESSION_TOKEN__="{runtime.auth.token}";' in html
    assert 'window.__HERMES_BASE_PATH__="";' in html
    assert "window.__HERMES_AUTH_REQUIRED__=true;" in html
    assert 'window.__HERMES_UI_SURFACE__="dashboard";' in html
    assert "window.__HERMES_PLUS__={" in html
    assert _injected_plus_config(html)["version"] == __version__


def test_injected_bootstrap_script_parses_and_defines_the_globals(tmp_path: Path):
    """The renderer installs its browser bridge off these globals.

    A syntax error in this script is invisible to a substring assertion but
    fatal in the browser: `window.hermesDesktop` never appears and the app
    boots into "Desktop IPC bridge is unavailable". So the script is executed.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to parse the injected bootstrap script")
    runtime = _runtime(tmp_path)
    html = _inject("<!doctype html><html><head></head><body></body></html>", runtime.settings, runtime)
    match = re.search(r"<script>(.*?)</script>", html, re.S)
    assert match is not None, html
    probe = (
        "globalThis.window = globalThis;"
        + match.group(1)
        + ";console.log(JSON.stringify({token:window.__HERMES_SESSION_TOKEN__,"
        "basePath:window.__HERMES_BASE_PATH__,authRequired:window.__HERMES_AUTH_REQUIRED__,"
        "surface:window.__HERMES_UI_SURFACE__,plus:window.__HERMES_PLUS__}));"
    )
    result = subprocess.run([node, "-e", probe], capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["token"] == runtime.auth.token
    assert payload["authRequired"] is True
    assert payload["surface"] == "dashboard"
    assert payload["basePath"] == ""
    assert payload["plus"]["version"] == __version__
    assert payload["plus"]["basePath"] == "/"
    assert payload["plus"]["compat"]["status"] == getattr(runtime.compat, "status", "unknown")


def test_inject_never_contains_the_upstream_token(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.upstream_token = "upstream-secret-token"
    html = _inject("<html><head></head><body></body></html>", runtime.settings, runtime)
    assert "upstream-secret-token" not in html


def test_inject_base_path_and_auth_none(tmp_path: Path):
    runtime = _runtime(tmp_path, base_path="/plus", auth="none")
    html = _inject("<html></html>", runtime.settings, runtime)
    assert 'window.__HERMES_BASE_PATH__="/plus";' in html
    assert "window.__HERMES_AUTH_REQUIRED__=false;" in html
    assert _injected_plus_config(html)["basePath"] == "/plus"


def test_hashed_asset_detection():
    assert _HASHED_ASSET.search("app-B12cD34e.js")
    assert _HASHED_ASSET.search("index-DqR9_xK2.css")
    assert not _HASHED_ASSET.search("index.html")
    assert not _HASHED_ASSET.search("favicon.svg")


def test_ui_root_prefers_settings_dir(tmp_path: Path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html></html>")
    settings = ServeSettings(ui_dir=ui)
    assert _ui_root(settings) == ui


def test_ui_root_returns_none_for_missing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # `_ui_root` falls back to the checkout's own packages/ui build, which
    # exists whenever the UI has been built (the clean-install proof builds
    # it), so the fallback is pointed away for this test.
    monkeypatch.setattr(static, "_DEV_UI_DIR", tmp_path / "no-dev-build")
    settings = ServeSettings(ui_dir=tmp_path / "absent")
    assert _ui_root(settings) is None
