"""Static UI serving: runtime-global injection, escaping, and cache rules."""

from __future__ import annotations

from pathlib import Path

from hermes_plus import __version__
from hermes_plus.server.runtime import Runtime
from hermes_plus.server.static import _HASHED_ASSET, _inject, _script_escape, _ui_root
from hermes_plus.settings import ServeSettings


def _runtime(tmp_path: Path, **kwargs) -> Runtime:
    settings = ServeSettings(ui_dir=tmp_path, **kwargs)
    return Runtime(settings)


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
    assert f"version:{_script_escape(__version__)}" in html


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
    assert "basePath:\"/plus\"" in html


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


def test_ui_root_returns_none_for_missing_dir(tmp_path: Path):
    settings = ServeSettings(ui_dir=tmp_path / "absent")
    assert _ui_root(settings) is None
