"""Hermes profile paths and Hermes Plus data directories."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_IDENTITY_MARKERS = ("config.yaml", ".env", "SOUL.md", "profile.yaml", "auth.json", "state.db")


def _expand_home(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path)))


def _native_home() -> Path:
    suffix = os.environ.get("HERMES_DATA_DIR_SUFFIX", "")
    if sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA", "").strip() else (
            Path.home() / "AppData" / "Local"
        )
        return base / f"hermes{suffix}"
    return Path.home() / f".hermes{suffix}"


def _current_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    return _expand_home(raw) if raw else _native_home()


def _root_of(home: Path) -> Path:
    native = _native_home()
    try:
        home.resolve(strict=False).relative_to(native.resolve(strict=False))
    except ValueError:
        return home.parent.parent if home.parent.name == "profiles" else home
    return native


def hermes_home(profile: str | None = None) -> Path:
    """Return the active Hermes home or a named profile's home."""
    current = _current_home()
    if profile is None:
        return current
    if profile.casefold() == "default":
        return _root_of(current)
    if not _PROFILE_NAME_RE.fullmatch(profile):
        raise ValueError(f"invalid Hermes profile name: {profile!r}")
    return _root_of(current) / "profiles" / profile


def list_profiles() -> list[str]:
    """Return ``default`` and named profile directories carrying identity markers."""
    root = _root_of(_current_home())
    profiles = root / "profiles"
    names = ["default"]
    try:
        entries = sorted(profiles.iterdir())
    except OSError:
        return names
    for entry in entries:
        if (entry.name == "default" or not _PROFILE_NAME_RE.fullmatch(entry.name)
                or not entry.is_dir() or (profiles / ".deleted" / entry.name).exists()):
            continue
        if any((entry / marker).is_file() or (entry / marker).is_symlink() for marker in _IDENTITY_MARKERS):
            names.append(entry.name)
    return names


def plus_home(profile: str | None = None) -> Path:
    """Return and create this profile's Hermes Plus data directory."""
    path = hermes_home(profile) / "hermes-plus"
    path.mkdir(parents=True, exist_ok=True)
    return path
