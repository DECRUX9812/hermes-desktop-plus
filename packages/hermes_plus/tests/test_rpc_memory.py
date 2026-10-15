from __future__ import annotations

from support import fault, ok

from hermes_plus.store.memory import entry_id


async def test_add_then_list_returns_entries_and_usage(call):
    listing = ok(await call("fork.memory.add", {"profile": "demo", "target": "memory", "text": "Keep it short"}))
    assert listing["entries"] == [{"id": entry_id("memory", "Keep it short"), "target": "memory",
                                   "text": "Keep it short"}]
    assert listing["usage"]["memory"] == {"used": len("Keep it short"), "limit": 2200}
    assert listing["usage"]["user"] == {"used": 0, "limit": 1375}
    assert ok(await call("fork.memory.list", {"profile": "demo"})) == listing


async def test_invalid_target_is_memory_rejected(call):
    assert fault(await call("fork.memory.add", {"profile": "demo", "target": "system", "text": "x"}))["code"] == 4094


async def test_empty_text_is_memory_rejected(call):
    assert fault(await call("fork.memory.add", {"profile": "demo", "target": "user", "text": "  "}))["code"] == 4094


async def test_over_budget_add_is_rejected(call):
    error = fault(await call("fork.memory.add", {"profile": "demo", "target": "user", "text": "x" * 1400}))
    assert error["code"] == 4094


async def test_update_with_stale_id_is_item_changed(call):
    error = fault(await call("fork.memory.update", {"profile": "demo", "id": "000000000000", "text": "x"}))
    assert error["code"] == 4093


async def test_update_rewrites_the_entry(call):
    ok(await call("fork.memory.add", {"profile": "demo", "target": "user", "text": "Works in UTC"}))
    listing = ok(await call("fork.memory.update", {
        "profile": "demo", "id": entry_id("user", "Works in UTC"), "text": "Works in CET",
    }))
    assert [entry["text"] for entry in listing["entries"]] == ["Works in CET"]


async def test_remove_deletes_and_stale_remove_is_item_changed(call):
    ok(await call("fork.memory.add", {"profile": "demo", "target": "memory", "text": "Keep it short"}))
    listing = ok(await call("fork.memory.remove", {"profile": "demo", "id": entry_id("memory", "Keep it short")}))
    assert listing["entries"] == []
    assert fault(await call("fork.memory.remove", {"profile": "demo", "id": "000000000000"}))["code"] == 4093


async def test_memory_requires_a_profile(call):
    assert fault(await call("fork.memory.list", {}))["code"] == 4063
