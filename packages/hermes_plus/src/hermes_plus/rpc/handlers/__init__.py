"""Importing this package registers every plus RPC method."""

from hermes_plus.rpc.handlers import (
    bots_mailbox,
    bots_team,
    fork_approvals,
    fork_bot_identity,
    fork_memory,
    fork_onboarding,
    fork_routines,
    session_side,
)

__all__ = [
    "bots_mailbox",
    "bots_team",
    "fork_approvals",
    "fork_bot_identity",
    "fork_memory",
    "fork_onboarding",
    "fork_routines",
    "session_side",
]
