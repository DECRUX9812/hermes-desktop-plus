from __future__ import annotations

import pytest

from hermes_plus.store import BotIdentities, RevisionConflictError


def test_unknown_profile_reads_as_blank_identity(store):
    identity = BotIdentities(store).get("demo")
    assert (identity.revision, identity.display_name, identity.role) == (0, "", "")


def test_name_change_bumps_revision_and_keeps_other_fields(store):
    bots = BotIdentities(store)
    first = bots.update("demo", {"display_name": "Demo Bot"}, expected_revision=None)
    assert first.revision == 1
    second = bots.update("demo", {"instructions": "Be brief."}, expected_revision=1)
    assert second.revision == 1
    assert second.instructions == "Be brief."
    assert second.display_name == "Demo Bot"


def test_stale_expected_revision_is_rejected(store):
    bots = BotIdentities(store)
    bots.update("demo", {"role": "lead"}, expected_revision=None)
    with pytest.raises(RevisionConflictError):
        bots.update("demo", {"role": "ops"}, expected_revision=0)


def test_unknown_identity_field_is_rejected(store):
    with pytest.raises(ValueError):
        BotIdentities(store).update("demo", {"api_key": "x"}, expected_revision=None)
