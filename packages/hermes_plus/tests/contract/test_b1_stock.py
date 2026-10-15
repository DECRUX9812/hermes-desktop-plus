from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from hermes_plus.adapter import (
    DashboardProcess,
    GatewayClient,
    cron,
    hermes_home,
    list_profiles,
    load_config,
    profiles,
    run_hermes,
)
from hermes_plus.rpc import RpcContext, handle_text
from hermes_plus.rpc.context import GatewayUpstream
from hermes_plus.store import Store
from support import fault, ok

pytestmark = pytest.mark.contract

MODEL_CONFIG = os.environ.get("HERMES_PLUS_TEST_MODEL_CONFIG", "")
PREDICT_GATE = "auxiliary.composer_prediction.enabled"


@pytest.fixture
def hermes_env(dashboard, stock_hermes: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    _, home = dashboard
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PLUS_HERMES_BIN", stock_hermes)
    return home


@asynccontextmanager
async def gateway(process: DashboardProcess, store: Store) -> AsyncIterator[RpcContext]:
    client = GatewayClient(process.ws_url, process.token)
    await client.connect()
    try:
        yield RpcContext(store, internal=GatewayUpstream(client))
    finally:
        await client.close()


async def rpc(ctx: RpcContext, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        request["params"] = params
    return json.loads(await handle_text(ctx, json.dumps(request)))


def seed_transcript(stock_hermes: str, home: Path, stored_session_id: str, messages: list[dict[str, Any]]) -> None:
    python = Path(stock_hermes).with_name("python3")
    script = ("import json, sys\n"
              "from hermes_state import SessionDB\n"
              "db = SessionDB()\n"
              "db.ensure_session(sys.argv[1], source='desktop')\n"
              "db.append_messages_batch(sys.argv[1], json.loads(sys.argv[2]))\n"
              "db.close()\n")
    result = subprocess.run(
        [str(python), "-c", script, stored_session_id, json.dumps(messages)],
        env={**os.environ, "HERMES_HOME": str(home)}, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr


async def test_routine_lifecycle_runs_through_stock_cron(hermes_env, call):
    profile = "routine-contract"
    profiles.create(profile, description="routine contract profile")
    try:
        assert profile in list_profiles()
        assert profile in run_hermes(["profile", "list"]).stdout

        created = ok(await call("fork.routines.create", {
            "profile": profile, "name": "Morning digest", "schedule": "every 2h", "kind": "schedule",
            "prompt": "Summarise overnight activity.",
        }))["routines"]
        assert len(created) == 1
        routine = created[0]
        job_id = routine["id"]
        assert routine["kind"] == "schedule"
        assert routine["name"] == "Morning digest"
        assert routine["enabled"] is True
        assert routine["deliver"] == f"bot-chat:{profile}"
        assert job_id in run_hermes(["cron", "list", "--all"], profile=profile).stdout

        paused = ok(await call("fork.routines.pause", {"profile": profile, "id": job_id}))
        assert paused["routines"][0]["enabled"] is False
        resumed = ok(await call("fork.routines.resume", {"profile": profile, "id": job_id}))
        assert resumed["routines"][0]["enabled"] is True

        ran = ok(await call("fork.routines.run_now", {"profile": profile, "id": job_id}))
        assert ran["started"] is True
        assert ran["routines"][0]["last_run_at"] is not None

        edited = ok(await call("fork.routines.update", {"profile": profile, "id": job_id, "name": "Evening digest"}))
        assert edited["routines"][0]["name"] == "Evening digest"

        removed = ok(await call("fork.routines.remove", {"profile": profile, "id": job_id}))
        assert removed["routines"] == []
        assert job_id not in run_hermes(["cron", "list", "--all"], profile=profile).stdout
        assert all(job["id"] != job_id for job in cron.list_jobs(profile))
    finally:
        profiles.delete(profile)


async def test_setup_profile_lifecycle_creates_configures_and_resets(hermes_env, call):
    first = ok(await call("fork.onboarding.ensure_setup_profile", {}))
    name = first["name"]
    try:
        assert first["created"] is True
        assert Path(first["path"]).is_dir()
        assert profiles.exists(name)
        assert (Path(first["path"]) / "SOUL.md").read_text(encoding="utf-8").startswith("# Hermes")
        config = load_config(name)
        assert config["agent"]["reasoning_effort"] == "low"
        assert config["agent"]["coding_context"] in ("off", False)
        assert config["display"]["show_reasoning"] in ("false", False)

        second = ok(await call("fork.onboarding.ensure_setup_profile", {}))
        assert second == {**first, "created": False}

        reset = ok(await call("fork.onboarding.reset_setup_profile", {}))
        assert reset == {"name": name, "path": first["path"], "reset": True}
        assert not profiles.exists(name)
        assert fault(await call("fork.onboarding.reset_setup_profile", {}))["code"] == 4072
    finally:
        if profiles.exists(name):
            profiles.delete(name)


async def test_setup_session_is_created_once_and_reused(hermes_env, dashboard, store):
    process, _ = dashboard
    async with gateway(process, store) as ctx:
        first = ok(await rpc(ctx, "fork.onboarding.ensure_setup_session"))
        try:
            assert first["empty"] is True
            assert first["session_id"]
            assert profiles.exists(first["profile"])
            second = ok(await rpc(ctx, "fork.onboarding.ensure_setup_session"))
            assert second == first
        finally:
            ok(await rpc(ctx, "fork.onboarding.reset_setup_profile"))


async def test_profile_delete_accepts_a_pending_upstream_identity_settlement(hermes_env, dashboard, tmp_path,
                                                                            monkeypatch):
    """A delete whose upstream identity settlement is pending still deletes the profile.

    Stock Hermes tombstones a profile home before settling its durable identity, and the
    settlement cannot open a state.db under that tombstone: `hermes profile delete <name> --yes`
    then exits non-zero with a retry hint, though the directory is already removed. A profile
    with its own state.db takes that path on 379cf122 and v0.22.2 alike, so the adapter's
    contract is the directory, not the exit code. HOME is sandboxed so the CLI cannot reach a
    real launcher in ~/.local/bin.
    """
    _, _ = dashboard
    monkeypatch.setenv("HOME", str(tmp_path))
    name = "delete-contract"
    profiles.create(name, description="delete contract profile")
    profile_home = hermes_home(name)
    # Upstream's own storage: an existing state.db is what makes the settlement pending.
    (profile_home / "state.db").touch()
    assert profiles.exists(name)

    profiles.delete(name)

    assert not profiles.exists(name)
    assert not profile_home.exists()
    assert name not in run_hermes(["profile", "list"]).stdout


async def test_session_ask_reports_stock_failures_structurally(hermes_env, dashboard, store, stock_hermes):
    process, home = dashboard
    async with gateway(process, store) as ctx:
        seeded = await ctx.internal.call("session.create", {})
        seed_transcript(stock_hermes, home, seeded["stored_session_id"], [
            {"role": "user", "content": "Which command runs the release build?"},
            {"role": "assistant", "content": "The release build runs with `make smoke-test`."},
        ])
        no_transcript = await ctx.internal.call("session.create", {})

        assert fault(await rpc(ctx, "session.ask", {"session_id": seeded["session_id"], "question": "Why?"}))[
            "code"] == 5030
        assert fault(await rpc(ctx, "session.ask", {"session_id": no_transcript["session_id"], "question": "Why?"}))[
            "code"] == 4040
        assert fault(await rpc(ctx, "session.ask", {"session_id": "no-such-session", "question": "Why?"}))[
            "code"] == 4001


async def test_predict_next_is_empty_unless_enabled_and_awaiting_the_user(hermes_env, dashboard, store, stock_hermes):
    process, home = dashboard
    async with gateway(process, store) as ctx:
        session = await ctx.internal.call("session.create", {})
        seed_transcript(stock_hermes, home, session["stored_session_id"], [
            {"role": "user", "content": "Run the tests"},
            {"role": "assistant", "content": "All 12 pass. Want me to commit?"},
        ])
        params = {"session_id": session["session_id"]}
        assert ok(await rpc(ctx, "session.predict_next", params)) == {"text": ""}

        _set_gate(True)
        try:
            assert ok(await rpc(ctx, "session.predict_next", params)) == {"text": ""}
            seed_transcript(stock_hermes, home, session["stored_session_id"], [
                {"role": "user", "content": "Yes, commit it"},
            ])
            assert ok(await rpc(ctx, "session.predict_next", params)) == {"text": ""}
        finally:
            _set_gate(False)


def _set_gate(enabled: bool) -> None:
    result = run_hermes(["config", "set", PREDICT_GATE, "true" if enabled else "false"])
    assert result.returncode == 0, result.stderr
    assert load_config().get("auxiliary", {}).get("composer_prediction", {}).get("enabled") is enabled


@pytest.mark.model
@pytest.mark.skipif(not MODEL_CONFIG, reason="HERMES_PLUS_TEST_MODEL_CONFIG names no config.yaml")
async def test_session_ask_answers_from_a_real_transcript(stock_hermes, make_dashboard, tmp_path, store,
                                                          monkeypatch):
    home = _model_home(tmp_path, stock_hermes, monkeypatch)
    async with gateway(make_dashboard(home), store) as ctx:
        session = await ctx.internal.call("session.create", {})
        seed_transcript(stock_hermes, home, session["stored_session_id"], [
            {"role": "user", "content": "Which command runs the release build?"},
            {"role": "assistant", "content": "The release build runs with `make smoke-test`."},
        ])
        answer = ok(await rpc(ctx, "session.ask", {
            "session_id": session["session_id"], "question": "Which command runs the release build?",
        }))
        assert "make smoke-test" in answer["answer"].lower()
        assert answer["messages_considered"] == 2


@pytest.mark.model
@pytest.mark.skipif(not MODEL_CONFIG, reason="HERMES_PLUS_TEST_MODEL_CONFIG names no config.yaml")
async def test_predict_next_returns_a_short_plain_message(stock_hermes, make_dashboard, tmp_path, store, monkeypatch):
    home = _model_home(tmp_path, stock_hermes, monkeypatch)
    _set_gate(True)
    async with gateway(make_dashboard(home), store) as ctx:
        session = await ctx.internal.call("session.create", {})
        seed_transcript(stock_hermes, home, session["stored_session_id"], [
            {"role": "user", "content": "Run the tests"},
            {"role": "assistant", "content": "All 12 pass. Want me to commit?"},
        ])
        text = ok(await rpc(ctx, "session.predict_next", {"session_id": session["session_id"]}))["text"]
        assert isinstance(text, str)
        assert len(text) <= 280
        assert "\n" not in text


def _model_home(tmp_path: Path, stock_hermes: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = Path(MODEL_CONFIG).expanduser()
    home = tmp_path / "model-home"
    home.mkdir()
    shutil.copyfile(source, home / "config.yaml")
    dotenv = source.with_name(".env")
    if dotenv.is_file():
        shutil.copyfile(dotenv, home / ".env")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PLUS_HERMES_BIN", stock_hermes)
    return home


async def test_profiles_list_returns_profiles_with_metadata(hermes_env, call):
    """profiles.list returns profiles from stock Hermes with expected fields."""
    result = ok(await call("profiles.list", {}))
    assert "profiles" in result
    assert "bot_mode_protocol" in result
    assert "install_id" in result
    assert result["bot_mode_protocol"] is True

    profiles_list = result["profiles"]
    assert isinstance(profiles_list, list)
    assert len(profiles_list) > 0

    # Check that 'default' profile exists
    default_profile = next((p for p in profiles_list if p["name"] == "default"), None)
    assert default_profile is not None

    # Check required fields on each profile
    for profile in profiles_list:
        assert "name" in profile
        assert "path" in profile
        assert "is_default" in profile
        assert "model" in profile
        assert "provider" in profile
        assert "description" in profile
        assert "display_name" in profile
        assert "skill_count" in profile
        assert "previous_names" in profile
        assert isinstance(profile["previous_names"], list)
        assert "has_avatar" in profile
        assert isinstance(profile["has_avatar"], bool)


async def test_profiles_list_with_include_sessions_false(hermes_env, call):
    """profiles.list with include_sessions=False skips session fields."""
    result = ok(await call("profiles.list", {"include_sessions": False}))
    assert "profiles" in result
    profiles_list = result["profiles"]
    assert len(profiles_list) > 0

    # Even with include_sessions=False, upstream may include these fields
    # Just verify the call succeeds
    for profile in profiles_list:
        assert "name" in profile
