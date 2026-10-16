"""Which plus methods take their plus.db from the request's ``profile`` param.

The fork annotates the handlers that bind a profile per call with
``@_profile_scoped`` (``tui_gateway/server.py:641``): the profile named by
``params['profile']`` — or, for a session-bound call, the live session's profile
home — scopes HERMES_HOME, config and secrets for that one call. The handlers
WITHOUT it never read a profile out of their params: ``bots_team.*`` and
``bots_mailbox.*`` are install-wide there ("The store is install-wide (one team
can span profiles), so like the mailbox these handlers take no profile scope",
``tui_gateway/methods_bot_team.py:1``).

Our plus.db is per profile, so that same split decides where a method reads and
writes. A profile-scoped method selects the store from ``params['profile']``;
every other method follows the CONNECTION's profile (``?profile=`` on
``/api/ws``, else the serve profile). This is what keeps ``profile`` usable as
data — a team seat's teammate profile, a mailbox note's sender — instead of
silently re-pointing the call at that bot's empty database.
"""

from __future__ import annotations

SELECTOR = "selector"
CONNECTION = "connection"

# The fork's @_profile_scoped surface we implement, by namespace.
PROFILE_SCOPED_NAMESPACES = (
    "delegation",
    "fork.approvals",
    "fork.bot.identity",
    "fork.browser",
    "fork.computer",
    "fork.memory",
    "fork.routines",
    "mcp_apps",
    "session",
)

# Plus-owned namespaces the fork leaves unscoped, so their `profile` fields are data.
CONNECTION_SCOPED_NAMESPACES = (
    "bots_mailbox",
    "bots_team",
    "fork.onboarding",
)


def classify(name: str) -> str:
    """How ``name`` picks its plus.db: SELECTOR, CONNECTION, or "" when undecided."""
    if name.startswith(tuple(f"{namespace}." for namespace in PROFILE_SCOPED_NAMESPACES)):
        return SELECTOR
    if name.startswith(tuple(f"{namespace}." for namespace in CONNECTION_SCOPED_NAMESPACES)):
        return CONNECTION
    return ""


def selects_store_from_params(name: str) -> bool:
    """Whether ``params['profile']`` selects this method's plus.db.

    Total by design: an undecided name — a client-made-up ``fork.unknown.deep``,
    which the multiplexer still routes here — is NOT profile-scoped, so a data
    field can never pick a database. tests/test_rpc_profile_scope.py fails when a
    REGISTERED method has no decision, so a new namespace cannot slip in.
    """
    return classify(name) == SELECTOR
