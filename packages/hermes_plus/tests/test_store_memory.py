from __future__ import annotations

import pytest

from hermes_plus.store import MemoryItemChanged, MemoryNotes, MemoryRejected
from hermes_plus.store.memory import entry_id


def test_duplicate_add_is_a_noop(store):
    notes = MemoryNotes(store)
    notes.add("demo", "memory", "Prefers short answers")
    notes.add("demo", "memory", "Prefers short answers")
    snapshot = notes.snapshot("demo")
    assert [entry.text for entry in snapshot["entries"]] == ["Prefers short answers"]
    assert snapshot["entries"][0].id == entry_id("memory", "Prefers short answers")
    assert snapshot["usage"]["memory"]["used"] == len("Prefers short answers")


def test_budget_rejects_overflow(store):
    with pytest.raises(MemoryRejected):
        MemoryNotes(store).add("demo", "user", "x" * 1400)


def test_invalid_target_is_rejected(store):
    with pytest.raises(MemoryRejected):
        MemoryNotes(store).add("demo", "system", "anything")


def test_replace_rekeys_the_entry(store):
    notes = MemoryNotes(store)
    notes.add("demo", "user", "Works in UTC")
    notes.replace("demo", entry_id("user", "Works in UTC"), "Works in CET")
    entries = notes.snapshot("demo")["entries"]
    assert [(entry.text, entry.id) for entry in entries] == [("Works in CET", entry_id("user", "Works in CET"))]


def test_stale_item_id_is_reported_as_changed(store):
    notes = MemoryNotes(store)
    notes.add("demo", "memory", "Keep it short")
    with pytest.raises(MemoryItemChanged):
        notes.replace("demo", "000000000000", "x")
    with pytest.raises(MemoryItemChanged):
        notes.remove("demo", "000000000000")


def test_remove_deletes_the_entry(store):
    notes = MemoryNotes(store)
    notes.add("demo", "memory", "Keep it short")
    notes.remove("demo", entry_id("memory", "Keep it short"))
    assert notes.snapshot("demo")["entries"] == []
