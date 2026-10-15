"""Importing this package registers every plus RPC method."""

from hermes_plus.rpc.handlers import (
    bots_mailbox,
    bots_team,
    delegation_reports,
    fork_approvals,
    fork_bot_identity,
    fork_browser,
    fork_computer,
    fork_memory,
    fork_onboarding,
    fork_routines,
    session_side,
)

__all__ = [
    "bots_mailbox",
    "bots_team",
    "delegation_reports",
    "fork_approvals",
    "fork_bot_identity",
    "fork_browser",
    "fork_computer",
    "fork_memory",
    "fork_onboarding",
    "fork_routines",
    "session_side",
]
