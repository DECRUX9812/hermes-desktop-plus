from __future__ import annotations

from hermes_plus.store import Onboarding
from hermes_plus.store.onboarding import MAX_FAILED_STARTS


def test_fresh_state_is_unseen_with_no_failures(store):
    state = Onboarding(store).state()
    assert (state.intro, state.failed_starts, state.completed_at) == ("unseen", 0, None)


def test_mark_seen_persists(store):
    assert Onboarding(store).mark_seen().intro == "seen"
    assert Onboarding(store).state().intro == "seen"


def test_failed_starts_stop_at_the_cap_and_settle_the_intro(store):
    onboarding = Onboarding(store)
    for _ in range(MAX_FAILED_STARTS + 2):
        last = onboarding.record_failed_start()
    assert last.failed_starts == MAX_FAILED_STARTS
    assert last.intro == "seen"
