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


class UnclassifiedMethod(LookupError):
    """A plus-owned method with no store-selector decision: add it to one table above."""


def selects_store_from_params(name: str) -> bool:
    """Whether ``params['profile']`` selects this method's plus.db.

    Raises UnclassifiedMethod for a plus-owned name that neither table covers, so
    a new namespace cannot inherit a store by accident.
    """
    if name.startswith(tuple(f"{namespace}." for namespace in PROFILE_SCOPED_NAMESPACES)):
        return True
    if name.startswith(tuple(f"{namespace}." for namespace in CONNECTION_SCOPED_NAMESPACES)):
        return False
    raise UnclassifiedMethod(name)
