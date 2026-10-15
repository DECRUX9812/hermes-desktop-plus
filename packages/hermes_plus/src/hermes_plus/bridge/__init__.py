"""The Hermes Plus bridge: a stdlib-only directory plugin for stock Hermes.

``register(ctx)`` hooks the fork's features onto Hermes' public plugin API — tools, hooks and
prompt sections — and records what landed in ``<plus_home>/bridge.json`` so ``hermes-plus doctor``
can verify the version handshake. It never imports hermes_plus (the sidecar's venv) or Hermes
internals, and never raises out of ``register()``: a missing ``ctx`` API logs one warning and
skips that feature.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from . import hooks, paths, prompts, tools

log = logging.getLogger(__name__)

VERSION = "0.1.0"
PLUGIN_NAME = "hermes-plus"


def register(ctx: Any = None) -> None:
    if ctx is None:
        return
    try:
        _register(ctx)
    except Exception:
        log.exception("hermes-plus bridge registration failed; the stock agent continues without it")


def _guarded(ctx: Any, getattr_name: str, feature: str) -> Any:
    api = getattr(ctx, getattr_name, None)
    if api is None:
        log.warning("hermes-plus: this Hermes exposes no ctx.%s; skipping %s", getattr_name, feature)
    return api


def _register(ctx: Any) -> None:
    features: list[str] = []

    register_hook = _guarded(ctx, "register_hook", "hooks")
    if register_hook is not None:
        features.extend(hooks.register_hooks(register_hook))

    register_section = _guarded(ctx, "register_system_prompt_section", "prompt sections")
    if register_section is not None:
        features.extend(prompts.register_sections(register_section))

    register_tool = _guarded(ctx, "register_tool", "tools")
    if register_tool is not None:
        features.extend(tools.register_tools(register_tool))

    _write_bridge_json(paths.plus_home(ctx), features)


def _write_bridge_json(plus_home: Path, features: list[str]) -> None:
    """The version handshake: what this bridge is and what it registered, for hermes-plus doctor."""
    body = json.dumps({
        "plugin": PLUGIN_NAME,
        "version": VERSION,
        "features": features,
        "registered_at": time.time(),
    }, ensure_ascii=False)
    try:
        fd = os.open(plus_home / "bridge.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, body.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        log.warning("hermes-plus: could not write bridge.json in %s", plus_home)
