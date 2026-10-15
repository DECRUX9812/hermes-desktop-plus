from __future__ import annotations

from support import fault, ok


async def test_get_unknown_profile_returns_blank_identity(call):
    identity = ok(await call("fork.bot.identity.get", {"profile": "demo"}))
    assert identity == {
        "profile": "demo", "display_name": "", "role": "", "instructions": "",
        "model": {"provider": "", "default": ""}, "revision": 0,
    }


async def test_profile_is_required(call):
    assert fault(await call("fork.bot.identity.get", {}))["code"] == 4063
    assert fault(await call("fork.bot.identity.set", {"role": "lead"}))["code"] == 4063


async def test_blank_display_name_is_refused(call):
    error = fault(await call("fork.bot.identity.set", {"profile": "demo", "display_name": "   "}))
    assert error["code"] == 4092


async def test_set_persists_and_bumps_revision_on_name_change(call):
    set_reply = ok(await call("fork.bot.identity.set", {
        "profile": "demo", "display_name": "Demo Bot", "role": "Support", "instructions": "Be brief.",
        "provider": "example-provider", "model": "example-model",
    }))
    assert set_reply["revision"] == 1
    assert set_reply["model"] == {"provider": "example-provider", "default": "example-model"}
    assert ok(await call("fork.bot.identity.get", {"profile": "demo"}))["display_name"] == "Demo Bot"


async def test_stale_expected_revision_is_a_conflict(call):
    ok(await call("fork.bot.identity.set", {"profile": "demo", "role": "lead"}))
    error = fault(await call("fork.bot.identity.set", {"profile": "demo", "role": "ops", "expected_revision": 0}))
    assert error["code"] == 4091
    current = ok(await call("fork.bot.identity.set", {"profile": "demo", "role": "ops", "expected_revision": 1}))
    assert current["role"] == "ops"


async def test_non_integer_expected_revision_is_invalid_params(call):
    error = fault(await call("fork.bot.identity.set", {"profile": "demo", "expected_revision": "one"}))
    assert error["code"] == -32602
