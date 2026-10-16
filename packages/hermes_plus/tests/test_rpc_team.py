from __future__ import annotations

import os

import pytest
from hermes_resolve import resolve_stock_hermes

from support import fault, ok


async def _create(call, name: str = "Alpha") -> str:
    view = ok(await call("bots_team.create", {"name": name, "mission": "Ship the demo", "actor": "you"}))
    return view["team"]["id"]


def _hermes_available() -> bool:
    binary, source = resolve_stock_hermes()
    return binary.is_file() and os.access(binary, os.X_OK)


async def test_team_lifecycle_create_seat_read_and_audit(call):
    team_id = await _create(call)
    seated = ok(await call("bots_team.member.upsert", {
        "team_id": team_id, "actor": "you", "profile": "lead", "role": "Lead", "lead": True,
        "monthly_usd": 20,
    }))
    assert seated["member"]["profile"] == "lead"
    assert seated["team"]["members"][0]["lead"] is True

    view = ok(await call("bots_team.get", {"team_id": team_id}))
    assert view["team"]["name"] == "Alpha"
    assert "tree" in view and "rollup" in view

    entries = ok(await call("bots_team.audit.list", {"team_id": team_id}))["entries"]
    assert [entry["action"] for entry in entries] == ["member.add", "team.create"]


async def test_list_summarises_teams(call):
    team_id = await _create(call)
    teams = ok(await call("bots_team.list"))["teams"]
    assert [team["id"] for team in teams] == [team_id]


async def test_unknown_team_is_team_not_found(call):
    assert fault(await call("bots_team.get", {"team_id": "team_missing"}))["code"] == 4111


async def test_missing_required_fields_are_team_refused(call):
    assert fault(await call("bots_team.get", {}))["code"] == 4110
    assert fault(await call("bots_team.member.upsert", {}))["code"] == 4110


async def test_create_without_a_name_is_refused(call):
    assert fault(await call("bots_team.create", {"mission": "x"}))["code"] == 4110


async def test_goal_tree_links_a_task_under_a_parent(call):
    team_id = await _create(call)
    ok(await call("bots_team.member.upsert", {"team_id": team_id, "profile": "lead", "lead": True}))
    parent = ok(await call("bots_team.goal.upsert", {"team_id": team_id, "title": "Launch"}))["goal"]
    child = ok(await call("bots_team.goal.upsert", {
        "team_id": team_id, "title": "Write docs", "parent_id": parent["id"], "owner": "lead",
    }))["goal"]
    linked = ok(await call("bots_team.goal.link_task", {
        "team_id": team_id, "goal_id": child["id"], "task_id": "task_1",
    }))
    assert linked["goal"]["task_ids"] == ["task_1"]
    assert linked["goal"]["parent_id"] == parent["id"]


# spawn_task delegates to the Hermes CLI; without a binary the RPC answers a
# structured 5110 instead of creating a task.
@pytest.mark.skipif(
    not _hermes_available(),
    reason="stock Hermes binary is unavailable (no HERMES_PLUS_TEST_HERMES, no hermes on PATH); "
    "bots_team.goal.spawn_task delegates to the Hermes CLI",
)
async def test_spawn_task_creates_kanban_task_and_links_to_goal(call, tmp_path, monkeypatch):
    # The in-process handler resolves Hermes like the sidecar does
    # (HERMES_PLUS_HERMES_BIN, then PATH) and spawns a real CLI subprocess —
    # same convention as the kanban adapter tests: point it at the stock
    # binary under test inside a throwaway HERMES_HOME.
    binary, _ = resolve_stock_hermes()
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PLUS_HERMES_BIN", str(binary))
    team_id = await _create(call)
    ok(await call("bots_team.member.upsert", {"team_id": team_id, "profile": "lead", "lead": True}))
    goal = ok(await call("bots_team.goal.upsert", {"team_id": team_id, "title": "Launch"}))["goal"]
    result = ok(await call("bots_team.goal.spawn_task", {
        "team_id": team_id, "goal_id": goal["id"], "title": "Draft spec", "assignee": "lead",
    }))
    assert "task_id" in result
    assert result["assignee"] == "lead"
    assert "team" in result
    team_goals = result["team"]["goals"]
    matching_goal = next(g for g in team_goals if g["id"] == goal["id"])
    assert result["task_id"] in matching_goal["task_ids"]


async def test_spawn_task_for_unknown_goal_is_refused_before_capability_check(call):
    team_id = await _create(call)
    error = fault(await call("bots_team.goal.spawn_task", {
        "team_id": team_id, "goal_id": "goal_missing", "title": "Draft",
    }))
    assert error["code"] == 4110


async def test_requester_cannot_decide_its_own_approval(call):
    team_id = await _create(call)
    ok(await call("bots_team.update", {"team_id": team_id, "lead_decides": True}))
    ok(await call("bots_team.member.upsert", {"team_id": team_id, "profile": "builder", "lead": True}))
    request = ok(await call("bots_team.approval.request", {
        "team_id": team_id, "kind": "spend", "subject": "Raise budget", "requested_by": "builder",
    }))["approval"]
    refused = fault(await call("bots_team.approval.decide", {
        "team_id": team_id, "approval_id": request["id"], "approve": True, "actor": "builder",
    }))
    assert refused["code"] == 4110
    decided = ok(await call("bots_team.approval.decide", {
        "team_id": team_id, "approval_id": request["id"], "approve": True, "actor": "you",
    }))
    assert decided["approval"]["status"] == "approved"


async def test_budget_record_then_check(call):
    team_id = await _create(call)
    ok(await call("bots_team.member.upsert", {"team_id": team_id, "profile": "builder",
                                             "monthly_usd": 10, "hard_stop": True}))
    spent = ok(await call("bots_team.budget.record", {"team_id": team_id, "member": "builder", "usd": 10}))
    assert spent["allowed"] is False
    checked = ok(await call("bots_team.budget.check", {"team_id": team_id, "member": "builder"}))
    assert checked["reason"] == "budget exhausted"


async def test_learning_add_dedupes_and_remove_drops_it(call):
    team_id = await _create(call)
    first = ok(await call("bots_team.learning.add", {
        "team_id": team_id, "text": "Use short answers.", "by": "builder",
    }))["learning"]
    ok(await call("bots_team.learning.add", {"team_id": team_id, "text": "use short answers", "by": "builder"}))
    view = ok(await call("bots_team.learning.remove", {"team_id": team_id, "learning_id": first["id"]}))
    assert view["team"]["learnings"] == []


async def test_brief_and_pack_round_trip(call):
    team_id = await _create(call)
    ok(await call("bots_team.member.upsert", {"team_id": team_id, "profile": "lead", "role": "Lead",
                                             "lead": True}))
    brief = ok(await call("bots_team.brief", {"team_id": team_id, "member": "lead"}))["brief"]
    assert isinstance(brief, str) and brief

    pack = ok(await call("bots_team.pack.export", {"team_id": team_id}))["pack"]
    imported = ok(await call("bots_team.pack.import", {"pack": pack, "name": "Beta", "actor": "you"}))
    assert imported["team"]["name"] == "Beta"
    assert imported["team"]["id"] != team_id


async def test_non_numeric_audit_limit_is_team_failed(call):
    team_id = await _create(call)
    assert fault(await call("bots_team.audit.list", {"team_id": team_id, "limit": "many"}))["code"] == 5110


async def test_room_lead_is_null_without_a_covering_team(call):
    reply = ok(await call("bots_team.room_lead", {"members": ["demo"]}))
    assert reply["lead"] is None
