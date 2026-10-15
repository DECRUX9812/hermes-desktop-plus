"""Prompt sections for the fork's policies, its setup prompts and a bot's team context.

The fork carried the setup prompts on first user messages (``start_chat`` tail, ``/initiate-setup``
command) and its policies in the agent loop; stock Hermes' only plugin surface for prompts is
``register_system_prompt_section``, whose rendered content is frozen into each new session's system
prompt. Every section here is therefore static per session (the prompt-caching invariant), and the
per-message conditions the fork checked become per-session ones: the matching skill must be
installed under ``<HERMES_HOME>/skills/``, or the profile must hold a seat on a team.

What is not portable: the fork's policies *enforced* a cost cap and stopped a stuck turn from the
loop. The retry tripwire is enforced by ``policies.py`` through ``pre_tool_call``; the cost cap has
no public hook and is not claimed here.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from . import paths, sidecar

log = logging.getLogger(__name__)

# The fork's agent/system_prompt.py::_team_parts limits (tools/bot_team.py).
_PROMPT_MAX_TEAMS = 2
_PROMPT_TEAMS_SCANNED = 20
_PROMPT_RPC_TIMEOUT = 2.0
_PROMPT_TEAM_BUDGET = 4.0  # total wall clock the section may spend on the sidecar
_MAX_TEAM_CHARS = 3800  # upstream caps a section at 4000 chars

_POLICIES_SECTION = """\
Turn guardrails (Hermes Plus): this session runs under the fork's turn policies.
- The retry-loop tripwire refuses a tool call that repeats the same tool with the same arguments 6+
  times in one turn. If a tool call comes back blocked with that diagnostic, change approach or ask
  the user instead of retrying it.
- A session cost cap (`agent.policies.max_cost_usd`) is not enforced on stock Hermes: watch spend
  yourself and stop when the user's budget is reached."""

_FIRST_TASK_SECTION = """\
Setup handoff (Hermes Plus): if this conversation was opened to hand off a setup task, follow the
installed `first-task` skill in <skills>/first-task/SKILL.md: work the connect/install cards first,
then the task. Surface only the visible ask; the skill's detail is for you."""

_INITIATE_SETUP_SECTION = """\
Initiate setup (Hermes Plus): when the user asks to set up their machine, follow the installed
`initiate-setup` skill in <skills>/initiate-setup/SKILL.md. Ask the name question and the colour
card first, read machine facts from the skill's JSON block, and never re-ask a closed card."""


def _home() -> Path:
    return paths.hermes_home(None)


def _profile_name(home: Path) -> str:
    """The active profile's name the way the fork's tools/bot_mode_probe.py reads it."""
    return home.name if home.parent.name == "profiles" else "default"


def _skill_installed(name: str) -> bool:
    return (_home() / "skills" / name / "SKILL.md").is_file()


def register_sections(register: Any) -> list[str]:
    """Register the sections through the guarded ``register_system_prompt_section``."""
    registered: list[str] = []
    candidates: tuple[tuple[str, str | Callable[[Any], str]], ...] = (
        ("hermes-plus.policies", _POLICIES_SECTION),
        ("hermes-plus.first-task", _first_task_content),
        ("hermes-plus.initiate-setup", _initiate_setup_content),
        ("hermes-plus.team", _team_content),
    )
    for section_id, content in candidates:
        try:
            register(section_id, content)
        except Exception:
            log.debug("prompt section %s was not accepted by this Hermes", section_id)
        else:
            registered.append(f"prompt.{section_id}")
    return registered


def _first_task_content(session_info: Any) -> str:
    if not _skill_installed("first-task"):
        return ""
    return _FIRST_TASK_SECTION


def _initiate_setup_content(session_info: Any) -> str:
    if not _skill_installed("initiate-setup"):
        return ""
    return _INITIATE_SETUP_SECTION


def _team_content(session_info: Any) -> str:
    """The fork's ``tools/bot_team.py::prompt_section``: "## Team" plus one brief per team the
    profile holds a seat on, rendered from the sidecar (the team store lives in plus.db).

    Empty when the sidecar is not running, when the profile sits on no team, or on any malformed
    reply — a missing team section must never break a session start. The whole lookup is bounded by
    ``_PROMPT_TEAM_BUDGET`` so a wedged sidecar cannot stall the first model call.
    """
    deadline = time.monotonic() + _PROMPT_TEAM_BUDGET
    plus_home = paths.plus_home(None)
    profile = _profile_name(_home())
    listing = sidecar.call(plus_home, "bots_team.list", {}, timeout=_PROMPT_RPC_TIMEOUT)
    teams = listing.get("teams") if isinstance(listing, dict) else None
    if not isinstance(teams, list):
        return ""
    blocks: list[str] = []
    for summary in teams[:_PROMPT_TEAMS_SCANNED]:
        team_id = summary.get("id") if isinstance(summary, dict) else None
        if not isinstance(team_id, str) or not team_id:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        brief = sidecar.call(plus_home, "bots_team.brief", {"team_id": team_id, "member": profile},
                             timeout=min(_PROMPT_RPC_TIMEOUT, remaining))
        text = brief.get("brief") if isinstance(brief, dict) else None
        if isinstance(text, str) and text.strip():
            blocks.append(text.strip())
        if len(blocks) >= _PROMPT_MAX_TEAMS:
            break
    if not blocks:
        return ""
    return ("## Team\n" + "\n\n".join(blocks))[:_MAX_TEAM_CHARS]
