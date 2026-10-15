"""bots_team.*: teams, org chart, goals, budgets, approvals, learnings, briefs and packs, persisted in plus.db."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hermes_plus.adapter.kanban import create_task
from hermes_plus.rpc.context import RpcContext, require_upstream
from hermes_plus.rpc.errors import FEATURE_UNAVAILABLE, TEAM_FAILED, TEAM_NOT_FOUND, TEAM_REFUSED, RpcFault
from hermes_plus.rpc.params import read_text
from hermes_plus.rpc.registry import method
from hermes_plus.store import TeamError, TeamNotFound

MEMBER_KEYS = ("profile", "role", "title", "skills", "plugins", "credentials", "status",
               "monthly_usd", "hard_stop", "reports_to", "lead")
GOAL_KEYS = ("title", "detail", "parent_id", "owner", "status")
TEAM_KEYS = ("name", "mission", "lead_decides", "channels")
KANBAN_CAPABILITY = "kanban.task.create"


def _team(name: str) -> Callable[[Callable[[RpcContext, dict[str, Any]], Any]], Any]:
    def register(fn: Callable[[RpcContext, dict[str, Any]], Any]) -> Any:
        def guarded(ctx: RpcContext, params: dict[str, Any]) -> Any:
            try:
                return fn(ctx, params)
            except RpcFault:
                raise
            except TeamNotFound as err:
                raise RpcFault(TEAM_NOT_FOUND, f"no team '{err.args[0]}'") from err
            except TeamError as err:
                raise RpcFault(TEAM_REFUSED, str(err)) from err
            except Exception as err:
                raise RpcFault(TEAM_FAILED, str(err)) from err

        return method(name)(guarded)

    return register


def _require(params: dict[str, Any], *keys: str) -> None:
    missing = [key for key in keys if params.get(key) in (None, "")]
    if missing:
        raise RpcFault(TEAM_REFUSED, f"{', '.join(missing)} required")


def _actor(params: dict[str, Any], default: str = "you") -> str:
    return read_text(params, "actor") or default


def _pick(params: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: params[key] for key in keys if key in params}


@_team("bots_team.list")
def list_teams(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    return {"teams": ctx.teams.list_summaries()}


@_team("bots_team.get")
def get_team(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id")
    return ctx.teams.view(params["team_id"])


@_team("bots_team.room_lead")
def room_lead(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    members = params.get("members")
    lead = ctx.teams.room_lead(members if isinstance(members, list) else [])
    return lead or {"lead": None, "lead_slot": "", "lead_title": "", "team_id": None, "team_name": ""}


@_team("bots_team.create")
def create_team(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    team = ctx.teams.create(
        name=params.get("name"), mission=params.get("mission") or "", actor=_actor(params),
        lead_decides=bool(params.get("lead_decides")), channels=params.get("channels"))
    return ctx.teams.view(team["id"])


@_team("bots_team.update")
def update_team(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id")
    ctx.teams.update(params["team_id"], actor=_actor(params), fields=_pick(params, TEAM_KEYS))
    return ctx.teams.view(params["team_id"])


@_team("bots_team.delete")
def delete_team(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id")
    ctx.teams.delete(params["team_id"], actor=_actor(params))
    return {"ok": True}


@_team("bots_team.member.upsert")
def upsert_member(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id")
    team_id = params["team_id"]
    member = ctx.teams.upsert_member(team_id, slot=params.get("slot") or None, actor=_actor(params),
                                     fields=_pick(params, MEMBER_KEYS))
    return {"member": member, **ctx.teams.view(team_id)}


@_team("bots_team.member.remove")
def remove_member(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "member")
    ctx.teams.remove_member(params["team_id"], params["member"], actor=_actor(params))
    return ctx.teams.view(params["team_id"])


@_team("bots_team.goal.upsert")
def upsert_goal(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id")
    team_id = params["team_id"]
    goal = ctx.teams.upsert_goal(team_id, goal_id=params.get("goal_id") or None, actor=_actor(params),
                                 fields=_pick(params, GOAL_KEYS))
    return {"goal": goal, **ctx.teams.view(team_id)}


@_team("bots_team.goal.link_task")
def link_task(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "goal_id", "task_id")
    team_id = params["team_id"]
    goal = ctx.teams.link_task(team_id, params["goal_id"], params["task_id"],
                               unlink=bool(params.get("unlink")), actor=_actor(params))
    return {"goal": goal, **ctx.teams.view(team_id)}


@_team("bots_team.goal.spawn_task")
def spawn_task(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "goal_id", "title")
    team_id, goal_id = params["team_id"], params["goal_id"]
    assignee, brief, idempotency_key = ctx.teams.spawn_brief(
        team_id, goal_id, params.get("assignee") or None, title=params["title"], body=params.get("body") or ""
    )
    actor = _actor(params)
    task = create_task(
        params["title"],
        body=brief,
        assignee=assignee,
        created_by=f"team:{team_id}",
        idempotency_key=idempotency_key,
        board=params.get("board"),
        profile=params.get("profile"),
    )
    task_id = task["id"]
    ctx.teams.link_task(team_id, goal_id, task_id, unlink=False, actor=actor)
    return {"task_id": task_id, "assignee": assignee, **ctx.teams.view(team_id)}


@_team("bots_team.approval.request")
def request_approval(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "kind", "subject", "requested_by")
    approval = ctx.teams.request_approval(
        params["team_id"], kind=params["kind"], subject=params["subject"],
        requested_by=params["requested_by"], detail=params.get("detail") or "", payload=params.get("payload"))
    return {"approval": approval}


@_team("bots_team.approval.decide")
def decide_approval(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "approval_id")
    team_id = params["team_id"]
    approval = ctx.teams.decide_approval(team_id, params["approval_id"], approve=bool(params.get("approve")),
                                         actor=_actor(params), note=params.get("note") or "")
    return {"approval": approval, **ctx.teams.view(team_id)}


@_team("bots_team.budget.check")
def check_budget(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "member")
    return ctx.teams.check_budget(params["team_id"], params["member"])


@_team("bots_team.budget.record")
def record_spend(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "member", "usd")
    return ctx.teams.record_spend(params["team_id"], params["member"], params["usd"],
                                  actor=_actor(params, default="system"))


@_team("bots_team.learning.add")
def add_learning(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "text", "by")
    item = ctx.teams.add_learning(params["team_id"], text=params["text"], by=params["by"],
                                  source=params.get("source") or "")
    return {"learning": item}


@_team("bots_team.learning.remove")
def remove_learning(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "learning_id")
    ctx.teams.remove_learning(params["team_id"], params["learning_id"], actor=_actor(params))
    return ctx.teams.view(params["team_id"])


@_team("bots_team.brief")
def brief(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id", "member")
    return {"brief": ctx.teams.brief(params["team_id"], params["member"], params.get("goal_id") or None)}


@_team("bots_team.pack.export")
def export_pack(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id")
    return {"pack": ctx.teams.export_pack(params["team_id"])}


@_team("bots_team.pack.import")
def import_pack(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    team = ctx.teams.import_pack(params.get("pack"), name=params.get("name") or None, actor=_actor(params))
    return ctx.teams.view(team["id"])


@_team("bots_team.audit.list")
def audit_list(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    _require(params, "team_id")
    return {"entries": ctx.teams.audit_list(params["team_id"], params.get("limit"))}
