"""Prompt sections for the fork's policies and setup prompts.

The fork carried these on first user messages (``start_chat`` tail, ``/initiate-setup`` command).
Stock Hermes' only plugin surface for prompts is ``register_system_prompt_section``, whose rendered
content is frozen into each new session's system prompt, so every section here is static per
session (the prompt-caching invariant). Conditions the fork checked per message are approximated
per session: the matching skill must be installed under ``<HERMES_HOME>/skills/``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from . import paths

log = logging.getLogger(__name__)

_POLICIES_SECTION = """\
Turn guardrails (Hermes Plus): this session runs under the fork's turn policies.
- A hard session cost cap may stop the loop when reached (config `agent.policies.max_cost_usd`);
  if a turn ends with a cost-cap diagnostic, report it plainly and wait for the user.
- A retry-loop tripwire stops a turn that calls the same tool with the same arguments repeatedly
  (default 6 identical calls). If it trips, change approach or ask the user instead of retrying."""

_FIRST_TASK_SECTION = """\
Setup handoff (Hermes Plus): if this conversation was opened to hand off a setup task, follow the
installed `first-task` skill in <skills>/first-task/SKILL.md: work the connect/install cards first,
then the task. Surface only the visible ask; the skill's detail is for you."""

_INITIATE_SETUP_SECTION = """\
Initiate setup (Hermes Plus): when the user asks to set up their machine, follow the installed
`initiate-setup` skill in <skills>/initiate-setup/SKILL.md. Ask the name question and the colour
card first, read machine facts from the skill's JSON block, and never re-ask a closed card."""


def _skills_dir() -> Path:
    return paths.hermes_home(None) / "skills"


def _skill_installed(name: str) -> bool:
    return (_skills_dir() / name / "SKILL.md").is_file()


def register_sections(register: Any) -> list[str]:
    """Register the sections through the guarded ``register_system_prompt_section``."""
    registered: list[str] = []
    candidates: tuple[tuple[str, str | Callable[[Any], str]], ...] = (
        ("hermes-plus.policies", _POLICIES_SECTION),
        ("hermes-plus.first-task", _first_task_content),
        ("hermes-plus.initiate-setup", _initiate_setup_content),
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
