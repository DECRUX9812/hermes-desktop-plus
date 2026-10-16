"""Which plus methods let `params['profile']` select their plus.db.

`bots_team.*` and `bots_mailbox.*` carry `profile` as data (a seat's teammate, a
note's sender). Reading it as the store selector answered those calls out of that
bot's own empty plus.db — "no team 'team_…'" for a seat a user added from the Team
page. The rule mirrors the fork's per-handler @_profile_scoped annotation.
"""

from __future__ import annotations

import pytest

from hermes_plus.rpc import methods
from hermes_plus.rpc.profile_scope import (
    CONNECTION_SCOPED_NAMESPACES,
    PROFILE_SCOPED_NAMESPACES,
    UnclassifiedMethod,
    selects_store_from_params,
)

DATA_PROFILE_METHODS = (
    "bots_team.list",
    "bots_team.get",
    "bots_team.create",
    "bots_team.member.upsert",
    "bots_team.member.remove",
    "bots_team.goal.upsert",
    "bots_team.audit.list",
    "bots_mailbox.list",
    "bots_mailbox.send",
    "bots_mailbox.update",
    "fork.onboarding.state",
    "fork.onboarding.ensure_setup_profile",
    "fork.onboarding.ensure_setup_session",
)

SELECTOR_PROFILE_METHODS = (
    "fork.bot.identity.get",
    "fork.bot.identity.set",
    "fork.memory.list",
    "fork.memory.add",
    "fork.routines.list",
    "fork.routines.create",
    "fork.approvals.list",
    "fork.approvals.decide",
    "fork.browser.thumbnail",
    "fork.computer.timeline",
    "mcp_apps.list",
    "mcp_apps.read_ui",
    "mcp_apps.call",
    "session.ask",
    "session.predict_next",
    "delegation.reports",
)


def test_every_registered_method_has_a_selector_decision():
    """A new namespace must pick a side, never inherit a store by accident."""
    unclassified = []
    for name in methods():
        try:
            selects_store_from_params(name)
        except UnclassifiedMethod:
            unclassified.append(name)
    assert unclassified == []


@pytest.mark.parametrize("name", DATA_PROFILE_METHODS)
def test_data_profile_methods_follow_the_connection(name: str):
    assert selects_store_from_params(name) is False
    assert name in methods()


@pytest.mark.parametrize("name", SELECTOR_PROFILE_METHODS)
def test_selector_profile_methods_take_the_param(name: str):
    assert selects_store_from_params(name) is True
    assert name in methods()


def test_namespaces_do_not_overlap():
    assert not set(PROFILE_SCOPED_NAMESPACES) & set(CONNECTION_SCOPED_NAMESPACES)


def test_an_unknown_plus_namespace_is_refused():
    with pytest.raises(UnclassifiedMethod):
        selects_store_from_params("fork.something-new.get")
    with pytest.raises(UnclassifiedMethod):
        selects_store_from_params("bots_teams.list")
