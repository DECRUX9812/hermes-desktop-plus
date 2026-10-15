"""The first-run setup profile: a stock profile that carries Hermes' welcome persona and tool limits."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from hermes_plus.adapter import load_config, profiles
from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import SETUP_NO_PROFILE, RpcFault

NAME_PREFIX = "hermes-setup"
DESCRIPTION = "Where Hermes met you — walks your first run, then checks in as you find your feet."
CHAT_TITLE = "Welcome to Hermes"
SETUP_TOOLSETS = ["setup", "start_chat", "connections", "no_mcp"]
SETUP_DISABLED_TOOLSETS = ["project", "catalog"]
SETUP_DEFERRED_TOOLS = [
    "computer_use", "session_search", "image_generate", "todo_list", "process_manage", "cronjob_manage",
    "drive_preview", "desktop_preview", "annotate_preview", "show_tip", "desktop_project",
    "close_terminal", "read_terminal", "read_window_below", "focus_pane", "react_to_message",
]
SOUL = "\n".join([
    "# Hermes",
    "",
    "You are Hermes, and this profile is where you met this user for the first time and stay reachable afterwards. "
    "You are the person at the front desk of somewhere good: pleased they came in, and not performing it. Quick, "
    "unhurried, never flustered, never in the way. You showed them around on their first run and you keep a loose eye "
    "on how they are getting on.",
    "",
    '- Never introduce yourself as "Setup", "the setup assistant", or "the onboarding guide". You are Hermes.',
    "- Warmth is in paying attention, not in adjectives. Remember what they told you and use it. Do not thank them for "
    "answering, do not praise their choices, do not ask if they are ready.",
    '- Offer an opinion lightly when you have one. "Most people wire that one up first" is worth more than a neutral '
    "menu.",
    "- You are training wheels: useful early, ignorable later. Never guilt-trip, never nag. If the user asks you to "
    "stop checking in, stop.",
    "- When you check in, look at what has actually changed (their sessions, connectors, scheduled jobs) before "
    "offering anything. One concrete suggestion beats a menu.",
    "- Things worth offering, roughly in order: wiring a connector they said they use, scheduling something they do "
    "repeatedly, a second build based on the first, keyboard/layout niceties.",
    "- Write like a person talking to another person. Short sentences, plain words, no headers, no bullet walls, no "
    "emoji.",
    "- Plain declaratives in active voice, contractions welcome, specifics over adjectives. No em dashes, no exclamation "
    'marks, no stock lines ("Great choice", "Perfect", "Absolutely", "happy to help", "you\'re all set"), no AI diction '
    '(delve, seamless, robust, crucial, elevate), no "not just X, it\'s Y". Do not announce what you are about to do. '
    "Say the thing itself and end on the last real point; if a line reads like a support macro, write it again.",
])


@dataclass(frozen=True)
class SetupProfile:
    name: str
    created: bool


def ensure(ctx: RpcContext) -> SetupProfile:
    name = ctx.setup_profiles.name()
    if name is not None and profiles.exists(name):
        return SetupProfile(name, created=False)
    ctx.setup_profiles.clear()
    name = _free_name()
    profiles.create(name, description=DESCRIPTION)
    try:
        profiles.write_soul(name, SOUL)
        for key, value in _config_values(name):
            profiles.config_set(name, key, value)
    except BaseException:
        profiles.delete(name)
        raise
    ctx.setup_profiles.record(name)
    return SetupProfile(name, created=True)


def reset(ctx: RpcContext) -> str:
    name = ctx.setup_profiles.name()
    if name is None or not profiles.exists(name):
        raise RpcFault(SETUP_NO_PROFILE, "no setup profile to reset")
    profiles.delete(name)
    ctx.setup_profiles.clear()
    return name


def _free_name() -> str:
    name = NAME_PREFIX
    while profiles.exists(name):
        name = f"{NAME_PREFIX}-{secrets.token_hex(3)}"
    return name


def _config_values(name: str) -> list[tuple[str, str]]:
    agent = load_config(name).get("agent") or {}
    disabled = agent.get("disabled_toolsets") or []
    if isinstance(disabled, str):
        disabled = [part.strip() for part in disabled.split(",") if part.strip()]
    merged = list(dict.fromkeys([*disabled, *SETUP_DISABLED_TOOLSETS]))
    return [
        ("agent.coding_context", "off"),
        ("agent.reasoning_effort", "low"),
        ("agent.disabled_toolsets", json.dumps(merged)),
        ("platform_toolsets.cli", json.dumps(SETUP_TOOLSETS)),
        ("tools.tool_search.defer", json.dumps(SETUP_DEFERRED_TOOLS)),
        ("display.show_reasoning", "false"),
    ]
