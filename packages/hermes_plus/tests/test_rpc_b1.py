from __future__ import annotations

from pathlib import Path

import pytest

from support import fault, ok


@pytest.fixture
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _profile(home: Path, name: str) -> None:
    path = home / "profiles" / name
    path.mkdir(parents=True)
    (path / "config.yaml").write_text("", encoding="utf-8")


async def test_session_ask_needs_session_and_question(call):
    assert fault(await call("session.ask", {"question": "why"}))["code"] == 4000
    assert fault(await call("session.ask", {"session_id": "s1"}))["code"] == 4033


async def test_session_ask_without_gateway_is_feature_unavailable(call):
    error = fault(await call("session.ask", {"session_id": "s1", "question": "why"}))
    assert error["code"] == -32004
    assert error["data"] == {"capability": "session.ask"}


async def test_predict_next_needs_session(call):
    assert fault(await call("session.predict_next", {}))["code"] == 4000


async def test_predict_next_without_gateway_is_feature_unavailable(call):
    error = fault(await call("session.predict_next", {"session_id": "s1"}))
    assert error["code"] == -32004
    assert error["data"] == {"capability": "session.predict_next"}


async def test_routines_need_a_profile(call):
    assert fault(await call("fork.routines.list", {}))["code"] == 4063


async def test_routines_reject_unknown_profile(call, hermes_home):
    assert fault(await call("fork.routines.list", {"profile": "ghost"}))["code"] == 4064


async def test_routines_list_is_empty_without_cron_jobs(call, hermes_home):
    _profile(hermes_home, "demo")
    assert ok(await call("fork.routines.list", {"profile": "demo"})) == {"routines": []}


async def test_routine_actions_on_unknown_routine_are_not_found(call, hermes_home):
    _profile(hermes_home, "demo")
    for action in ("pause", "resume", "remove", "run_now"):
        error = fault(await call(f"fork.routines.{action}", {"profile": "demo", "id": "nope"}))
        assert error["code"] == 4044, action
    error = fault(await call("fork.routines.update", {"profile": "demo", "id": "nope", "name": "x"}))
    assert error["code"] == 4044


async def test_routine_create_validates_before_touching_cron(call, hermes_home):
    _profile(hermes_home, "demo")
    base = {"profile": "demo", "name": "Digest", "schedule": "every 1h", "kind": "schedule", "prompt": "summarise"}
    assert fault(await call("fork.routines.create", {**base, "name": ""}))["code"] == 4002
    assert fault(await call("fork.routines.create", {**base, "kind": "nope"}))["code"] == 4002
    assert fault(await call("fork.routines.create", {**base, "prompt": ""}))["code"] == 4002
    watch = {**base, "kind": "watch", "prompt": "", "condition": ""}
    assert fault(await call("fork.routines.create", watch))["code"] == 4002


async def test_setup_session_needs_a_gateway(call):
    assert fault(await call("fork.onboarding.ensure_setup_session", {}))["code"] == -32004


async def test_reset_without_setup_profile_is_not_found(call, hermes_home):
    assert fault(await call("fork.onboarding.reset_setup_profile", {}))["code"] == 4072
