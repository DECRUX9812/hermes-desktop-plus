"""fork.routines.*: Bot Mode routines as stock cron jobs in a profile, each mutation returning the fresh list."""

from __future__ import annotations

from typing import Any

from hermes_plus.adapter import cron, list_profiles
from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.errors import (
    PARAMS_INVALID,
    PROFILE_NOT_FOUND,
    ROUTINE_FAILED,
    ROUTINE_NOT_FOUND,
    RpcFault,
)
from hermes_plus.rpc.params import profile_param, read_text
from hermes_plus.rpc.registry import method
from hermes_plus.store import RoutineMeta

KINDS = ("schedule", "watch")
WATCH_PROMPT = (
    "Check this condition: {condition}\n"
    "If it is not true yet, answer exactly [SILENT] and nothing else. "
    "If it is true, say in one or two sentences what changed."
)


def _profile(params: dict[str, Any]) -> str:
    profile = profile_param(params)
    if profile not in list_profiles():
        raise RpcFault(PROFILE_NOT_FOUND, f"profile not found: {profile}")
    return profile


def _routine(ctx: RpcContext, profile: str, params: dict[str, Any]) -> tuple[str, RoutineMeta]:
    job_id = read_text(params, "id")
    meta = ctx.routines.for_profile(profile).get(job_id)
    if meta is None:
        raise RpcFault(ROUTINE_NOT_FOUND, "routine not found")
    return job_id, meta


def _listing(ctx: RpcContext, profile: str) -> dict[str, Any]:
    metas = ctx.routines.for_profile(profile)
    routines = []
    for job in cron.list_jobs(profile):
        meta = metas.get(str(job.get("id")))
        if meta is None:
            continue
        routine: dict[str, Any] = {
            "id": job["id"],
            "name": job.get("name") or "",
            "kind": meta.kind,
            "schedule": job.get("schedule_display") or "",
            "prompt": job.get("prompt") or "",
            "enabled": bool(job.get("enabled", True)),
            "next_run_at": job.get("next_run_at"),
            "last_run_at": job.get("last_run_at"),
            "last_status": job.get("last_status"),
            "deliver": job.get("deliver"),
        }
        if meta.condition is not None:
            routine["condition"] = meta.condition
        routines.append(routine)
    return {"routines": routines}


def _cron(action):
    try:
        return action()
    except cron.CronError as err:
        raise RpcFault(ROUTINE_FAILED, str(err)) from err


@method("fork.routines.list")
def list_routines(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    return _listing(ctx, _profile(params))


@method("fork.routines.create")
def create_routine(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    name = read_text(params, "name")
    schedule = read_text(params, "schedule")
    kind = read_text(params, "kind")
    prompt = read_text(params, "prompt")
    condition = read_text(params, "condition")
    if not name or not schedule:
        raise RpcFault(PARAMS_INVALID, "name and schedule are required")
    if kind not in KINDS:
        raise RpcFault(PARAMS_INVALID, "kind must be 'schedule' or 'watch'")
    if kind == "schedule" and not prompt:
        raise RpcFault(PARAMS_INVALID, "schedule routines need a prompt")
    if kind == "watch" and not condition:
        raise RpcFault(PARAMS_INVALID, "watch routines need a condition")
    routine_prompt = WATCH_PROMPT.format(condition=condition) if kind == "watch" else prompt
    job_id = _cron(lambda: cron.create(
        profile, name=name, schedule=schedule, prompt=routine_prompt, deliver=f"bot-chat:{profile}"))
    try:
        ctx.routines.put(profile, job_id, RoutineMeta(kind, condition or None))
    except Exception:
        _cron_remove_quietly(profile, job_id)
        raise
    return _listing(ctx, profile)


@method("fork.routines.update")
def update_routine(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    job_id, meta = _routine(ctx, profile, params)
    edits: dict[str, str] = {}
    for key in ("name", "schedule"):
        if params.get(key) is not None:
            value = read_text(params, key)
            if not value:
                raise RpcFault(PARAMS_INVALID, f"{key} cannot be empty")
            edits[key] = value
    new_condition = None
    if meta.kind == "watch":
        if params.get("prompt") is not None:
            raise RpcFault(PARAMS_INVALID, "watch prompts are generated from the condition")
        if params.get("condition") is not None:
            new_condition = read_text(params, "condition")
            if not new_condition:
                raise RpcFault(PARAMS_INVALID, "watch routines need a condition")
            edits["prompt"] = WATCH_PROMPT.format(condition=new_condition)
    elif params.get("condition") is not None:
        raise RpcFault(PARAMS_INVALID, "schedule routines have no condition")
    elif params.get("prompt") is not None:
        prompt = read_text(params, "prompt")
        if not prompt:
            raise RpcFault(PARAMS_INVALID, "schedule routines need a prompt")
        edits["prompt"] = prompt
    if not edits:
        raise RpcFault(PARAMS_INVALID, "no routine updates provided")
    _cron(lambda: cron.edit(profile, job_id, **edits))
    if new_condition is not None:
        ctx.routines.put(profile, job_id, RoutineMeta("watch", new_condition))
    return _listing(ctx, profile)


@method("fork.routines.pause")
def pause_routine(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    job_id, _meta = _routine(ctx, profile, params)
    _cron(lambda: cron.pause(profile, job_id))
    return _listing(ctx, profile)


@method("fork.routines.resume")
def resume_routine(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    job_id, _meta = _routine(ctx, profile, params)
    _cron(lambda: cron.resume(profile, job_id))
    return _listing(ctx, profile)


@method("fork.routines.remove")
def remove_routine(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    job_id, _meta = _routine(ctx, profile, params)
    _cron(lambda: cron.remove(profile, job_id))
    ctx.routines.delete(profile, job_id)
    return _listing(ctx, profile)


@method("fork.routines.run_now")
def run_routine_now(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile = _profile(params)
    job_id, _meta = _routine(ctx, profile, params)
    _cron(lambda: cron.run(profile, job_id))
    return {**_listing(ctx, profile), "started": True}


def _cron_remove_quietly(profile: str, job_id: str) -> None:
    try:
        cron.remove(profile, job_id)
    except cron.CronError:
        pass
