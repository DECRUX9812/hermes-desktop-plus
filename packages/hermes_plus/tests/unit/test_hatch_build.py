"""Tests for the hatchling build hook in hatch_build.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("hatchling")

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _load_hook_class():
    path = Path(__file__).resolve().parents[2] / "hatch_build.py"
    spec = importlib.util.spec_from_file_location("hatch_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.UIBuildHook


def _make_hook(hook_class, root: Path):
    # BuildHookInterface's constructor and its `root`/`target_name` attributes
    # have churned across hatchling releases (name-mangled properties), so use
    # a subclass that overrides those properties instead of calling super().
    class Hook(hook_class):
        def __init__(self) -> None:
            self._root = str(root)

        @property
        def root(self):  # type: ignore[override]
            return self._root

        @property
        def target_name(self):  # type: ignore[override]
            return "wheel"

    return Hook()


def test_copies_built_ui_and_sets_force_include(tmp_path):
    hook_class = _load_hook_class()
    root = tmp_path / "hermes_plus"
    root.mkdir()
    built_ui = tmp_path / "ui" / "dist-webapp"
    built_ui.mkdir(parents=True)
    (built_ui / "index.html").write_text("<html></html>")

    hook = _make_hook(hook_class, root)
    build_data = {}
    hook.initialize("standard", build_data)

    copied = root / "src" / "hermes_plus" / "ui_dist" / "index.html"
    assert copied.is_file()
    assert copied.read_text() == "<html></html>"
    assert build_data["force_include"] == {
        str(root / "src" / "hermes_plus" / "ui_dist"): "hermes_plus/ui_dist",
    }


def test_missing_ui_raises_without_allow_no_ui(tmp_path, monkeypatch):
    hook_class = _load_hook_class()
    root = tmp_path / "hermes_plus"
    root.mkdir()
    monkeypatch.setenv("HERMES_PLUS_SKIP_UI_BUILD", "1")
    monkeypatch.delenv("HERMES_PLUS_ALLOW_NO_UI", raising=False)

    hook = _make_hook(hook_class, root)
    build_data = {}
    with pytest.raises(RuntimeError, match="HERMES_PLUS_ALLOW_NO_UI"):
        hook.initialize("standard", build_data)
    assert not (root / "src" / "hermes_plus" / "ui_dist" / "index.html").exists()
    assert "force_include" not in build_data


def test_missing_ui_allowed_with_allow_no_ui(tmp_path, monkeypatch):
    hook_class = _load_hook_class()
    root = tmp_path / "hermes_plus"
    root.mkdir()
    monkeypatch.setenv("HERMES_PLUS_SKIP_UI_BUILD", "1")
    monkeypatch.setenv("HERMES_PLUS_ALLOW_NO_UI", "1")

    hook = _make_hook(hook_class, root)
    build_data = {}
    hook.initialize("standard", build_data)

    assert not (root / "src" / "hermes_plus" / "ui_dist" / "index.html").exists()
    assert "force_include" not in build_data


def test_editable_install_skips_ui(tmp_path, monkeypatch):
    hook_class = _load_hook_class()
    root = tmp_path / "hermes_plus"
    root.mkdir()
    monkeypatch.delenv("HERMES_PLUS_SKIP_UI_BUILD", raising=False)
    monkeypatch.delenv("HERMES_PLUS_ALLOW_NO_UI", raising=False)

    hook = _make_hook(hook_class, root)
    build_data = {}
    hook.initialize("editable", build_data)

    assert not (root / "src" / "hermes_plus" / "ui_dist" / "index.html").exists()
    assert "force_include" not in build_data


def test_existing_ui_dist_is_not_recopied(tmp_path):
    hook_class = _load_hook_class()
    root = tmp_path / "hermes_plus"
    ui_dist = root / "src" / "hermes_plus" / "ui_dist"
    ui_dist.mkdir(parents=True)
    (ui_dist / "index.html").write_text("<html>existing</html>")

    hook = _make_hook(hook_class, root)
    build_data = {}
    hook.initialize("standard", build_data)

    assert (ui_dist / "index.html").read_text() == "<html>existing</html>"
    assert build_data["force_include"] == {
        str(ui_dist): "hermes_plus/ui_dist",
    }
