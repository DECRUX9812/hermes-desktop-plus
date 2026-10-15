"""profiles.list: enumerate profiles and their session/metadata state.

Aggregates stock profile data from upstream with plus-specific metadata (ui_meta, bot mode state).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from hermes_plus.adapter import paths
from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.registry import method

log = logging.getLogger(__name__)

AVATAR_EXTS = ("png", "jpg", "webp")


@method("profiles.list")
async def profiles_list(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    """List Hermes profiles with session and metadata fields.

    Params:
    - include_sessions (default true): add last_session, worker_session, canonical_session

    Returns:
    - profiles: list of profile rows, each with name, path, model, provider, description,
      display_name, skill_count, previous_names, (optionally) session fields, ui_meta,
      ui_meta_revisions, has_avatar
    - bot_mode_protocol: true (this backend injects bot-mode protocol)
    - install_id: stable per-install id
    """
    include_sessions = params.get("include_sessions", True)

    # Call upstream to get the base profile list
    if ctx.upstream is None:
        return {"profiles": [], "bot_mode_protocol": True, "install_id": ""}

    try:
        upstream_result = await ctx.upstream.call("profiles.list", params)
    except Exception as e:
        log.exception("profiles.list call to upstream failed: %s", e)
        # Fallback to a minimal list from local profile dirs
        return await _minimal_profiles_list(ctx)

    profiles = upstream_result.get("profiles", [])
    bot_mode_protocol = upstream_result.get("bot_mode_protocol", True)
    install_id = upstream_result.get("install_id", "")

    # Enhance each profile with plus-specific data (if needed in future)
    # For now, just pass through upstream's response

    return {
        "profiles": profiles,
        "bot_mode_protocol": bot_mode_protocol,
        "install_id": install_id,
    }


async def _minimal_profiles_list(ctx: RpcContext) -> dict[str, Any]:
    """Minimal fallback profile list when upstream is unavailable."""
    def _list_profiles_sync() -> list[dict[str, Any]]:
        """List profiles from the hermes home directory."""
        profiles = []
        try:
            profile_names = paths.list_profiles()
            for name in profile_names:
                home = paths.hermes_home(name)
                profiles.append({
                    "name": name,
                    "path": str(home),
                    "is_default": name == "default",
                    "model": "",
                    "provider": "",
                    "description": "",
                    "display_name": "",
                    "skill_count": 0,
                    "previous_names": [],
                    "has_avatar": _check_has_avatar(home),
                })
        except Exception as e:
            log.exception("Error listing profiles: %s", e)
        return profiles

    profiles = await asyncio.to_thread(_list_profiles_sync)
    return {
        "profiles": profiles,
        "bot_mode_protocol": True,
        "install_id": "",
    }


def _check_has_avatar(profile_dir: Path) -> bool:
    """Check if a profile has an avatar file."""
    try:
        assets_dir = Path(profile_dir) / "assets"
        if assets_dir.is_dir():
            return any((assets_dir / f"avatar.{ext}").is_file() for ext in AVATAR_EXTS)
    except Exception:
        pass
    return False
