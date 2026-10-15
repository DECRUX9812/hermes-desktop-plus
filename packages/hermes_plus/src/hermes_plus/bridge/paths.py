"""Resolve the Hermes home the way Hermes does, using only the ctx object and the environment.

The bridge must not import Hermes internals, so this mirrors upstream's profile rules: ``HERMES_HOME``
names the active home when set; otherwise the default ``~/.hermes``. Named profiles live at
``<root>/profiles/<name>``. ``ctx.profile_name`` (a documented PluginContext property) names the
current profile.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _current_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    if raw:
        return Path(os.path.expanduser(os.path.expandvars(raw)))
    return Path.home() / ".hermes"


def _root_of(home: Path) -> Path:
    if home.parent.name == "profiles":
        return home.parent.parent
    return home


def hermes_home(ctx: object) -> Path:
    """The current profile's Hermes home, honouring HERMES_HOME over ctx.profile_name."""
    current = _current_home()
    name = ""
    try:
        name = str(getattr(ctx, "profile_name", "") or "")
    except Exception:
        name = ""
    if not name or name in ("default", "custom"):
        return current
    if not _PROFILE_NAME_RE.fullmatch(name):
        return current
    return _root_of(current) / "profiles" / name


def plus_home(ctx: object) -> Path:
    """This profile's Hermes Plus data directory (created on demand)."""
    path = hermes_home(ctx) / "hermes-plus"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path
