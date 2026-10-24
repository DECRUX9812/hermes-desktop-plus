"""The bridge's reported version: read from its own plugin.yaml, never a
stale hardcoded constant (that constant drifted to 0.1.0 while the manifest
said 0.1.2, so every doctor run warned `bridge_version`)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import yaml

from hermes_plus import bridge

BRIDGE_DIR = Path(bridge.__file__).resolve().parent


def test_bridge_version_matches_its_own_manifest() -> None:
    manifest = yaml.safe_load((BRIDGE_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert bridge.VERSION == manifest["version"]


def test_bridge_version_matches_the_package_version() -> None:
    from hermes_plus import __version__

    assert bridge.VERSION == __version__


def test_bridge_version_falls_back_to_the_pinned_constant(tmp_path: Path) -> None:
    # The reading helper returns None for a missing manifest...
    assert bridge._version_from_plugin_yaml(tmp_path / "plugin.yaml") is None
    # ...and for a manifest without a version line.
    empty = tmp_path / "no-version.yaml"
    empty.write_text("name: hermes-plus\n", encoding="utf-8")
    assert bridge._version_from_plugin_yaml(empty) is None
    # The fallback constant must then agree with the real manifest.
    manifest = yaml.safe_load((BRIDGE_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert bridge.FALLBACK_VERSION == manifest["version"]


def test_bridge_version_parses_a_quoted_or_bare_manifest_version(tmp_path: Path) -> None:
    for text in ('version: "9.9.9"', "version: '9.9.9'", "version: 9.9.9"):
        manifest = tmp_path / "plugin.yaml"
        manifest.write_text(f"name: hermes-plus\n{text}\n", encoding="utf-8")
        assert bridge._version_from_plugin_yaml(manifest) == "9.9.9"


def test_bridge_stays_stdlib_only() -> None:
    """The bridge is loaded by stock Hermes' own interpreter; it must not grow
    a third-party import (yaml included) — the manifest is parsed by regex."""
    source = (BRIDGE_DIR / "__init__.py").read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_.]*)", source, re.MULTILINE)
    stdlib_allowed = {"__future__", "json", "logging", "os", "re", "time", "pathlib", "typing"}
    local = {name for name in imports if name.split(".")[0] in {"hooks", "paths", "policies", "prompts", "spool", "tools"}}
    assert set(imports) - stdlib_allowed - local == set()
