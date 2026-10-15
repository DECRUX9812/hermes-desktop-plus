from __future__ import annotations

import pytest

from hermes_plus.store import TeamError, TeamNotFound, TeamStore


def _team(teams: TeamStore, lead_decides: bool = False) -> str:
    team = teams.create(name="Alpha", mission="Ship the demo", actor="you", lead_decides=lead_decides, channels={})
    return team["id"]


def test_create_then_get_list_and_audit(store):
    teams = TeamStore(store)
    team_id = _team(teams)
    assert teams.get(team_id)["name"] == "Alpha"
    assert [summary["id"] for summary in teams.list_summaries()] == [team_id]
    assert teams.audit_list(team_id)[0]["action"] == "team.create"


def test_unsafe_or_unknown_team_ids_are_not_found(store):
    teams = TeamStore(store)
    with pytest.raises(TeamNotFound):
        teams.get("../escape")
    with pytest.raises(TeamNotFound):
        teams.get("team_missing")


def test_reporting_cycles_are_refused(store):
    teams = TeamStore(store)
    team_id = _team(teams)
    lead = teams.upsert_member(team_id, slot=None, actor="you", fields={"profile": "lead", "lead": True})
    report = teams.upsert_member(team_id, slot=None, actor="you",
                                 fields={"profile": "builder", "reports_to": lead["slot"]})
    assert report["reports_to"] == lead["slot"]
    with pytest.raises(TeamError):
        teams.upsert_member(team_id, slot=lead["slot"], actor="you", fields={"reports_to": report["slot"]})


def test_requester_cannot_decide_its_own_approval(store):
    teams = TeamStore(store)
    team_id = _team(teams, lead_decides=True)
    teams.upsert_member(team_id, slot=None, actor="you", fields={"profile": "builder", "lead": True})
    request = teams.request_approval(team_id, kind="spend", subject="Raise budget", requested_by="builder",
                                     detail="", payload=None)
    with pytest.raises(TeamError, match="own approval"):
        teams.decide_approval(team_id, request["id"], approve=True, actor="builder", note="")
    decided = teams.decide_approval(team_id, request["id"], approve=True, actor="you", note="ok")
    assert decided["status"] == "approved"


def test_spend_drains_the_budget_until_hard_stop(store):
    teams = TeamStore(store)
    team_id = _team(teams)
    teams.upsert_member(team_id, slot=None, actor="you",
                        fields={"profile": "builder", "monthly_usd": 10, "hard_stop": True})
    assert teams.record_spend(team_id, "builder", 4, actor="system")["remaining_usd"] == 6.0
    final = teams.record_spend(team_id, "builder", 6, actor="system")
    assert final["allowed"] is False
    assert final["reason"] == "budget exhausted"


def test_learnings_dedupe_by_normalized_text(store):
    teams = TeamStore(store)
    team_id = _team(teams)
    teams.add_learning(team_id, text="Use  short answers.", by="builder", source="demo")
    teams.add_learning(team_id, text="use short answers", by="builder", source="demo")
    assert len(teams.get(team_id)["learnings"]) == 1


def test_delete_removes_the_team(store):
    teams = TeamStore(store)
    team_id = _team(teams)
    teams.delete(team_id, actor="you")
    with pytest.raises(TeamNotFound):
        teams.get(team_id)
