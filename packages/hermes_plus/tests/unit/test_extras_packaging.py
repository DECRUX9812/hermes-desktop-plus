"""Contract: the new opt-in extras ship in the wheel and install like langfuse.

`hermes-plus install --extras <name>` installs (and enables) any plugin under
`src/hermes_plus/extras/plugins/<name>` — the loop in cli.py's
`_install_bridge_and_extras` is generic, exactly like the langfuse extra. The
wheel packaging is generic too: `.py` files ride in via `packages = ...`, and
pyproject.toml's `include` patterns carry the data files. This test pins that
each new extra (a) exists as a manifest-bearing plugin and (b) contains only
file types the wheel config actually packages — so a new asset type fails here
instead of silently vanishing from the released wheel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import yaml

EXTRAS_DIR = Path(__file__).resolve().parents[2] / "src" / "hermes_plus" / "extras" / "plugins"

# The 0.2.0 opt-in plugins, alongside the ones already shipped before them.
NEW_EXTRAS = ("group-rooms", "command-room", "home-feed", "activity", "vscode")

# Mirrors pyproject.toml's [tool.hatch.build.targets.wheel] `include` patterns
# (plus `.py`, carried by `packages = ["src/hermes_plus"]`). Keep in sync.
PACKAGED_SUFFIXES = {".py", ".yaml", ".md", ".json", ".png", ".jpg", ".js"}


@pytest.mark.parametrize("slug", NEW_EXTRAS)
def test_extra_is_a_packaged_plugin(slug: str) -> None:
    extra = EXTRAS_DIR / slug
    manifest = extra / "plugin.yaml"
    assert manifest.is_file(), f"extra '{slug}' has no plugin.yaml; it would not install via --extras"

    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert data.get("name") == slug, f"plugin.yaml name {data.get('name')!r} does not match the extra slug '{slug}'"

    shipped = [p for p in extra.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    assert shipped, f"extra '{slug}' ships no files"
    strays = sorted(str(p.relative_to(extra)) for p in shipped if p.suffix.lower() not in PACKAGED_SUFFIXES)
    assert not strays, (
        f"extra '{slug}' has files the wheel config does not package: {strays}; "
        "add an include pattern to pyproject.toml or drop the files"
    )
