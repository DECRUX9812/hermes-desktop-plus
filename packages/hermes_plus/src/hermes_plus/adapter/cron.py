"""A profile's stock cron jobs: jobs.json is read directly, every mutation goes through `hermes cron`."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from hermes_plus.adapter.cli import run_hermes
from hermes_plus.adapter.paths import hermes_home

_CREATED_JOB = re.compile(r"Created job: (\S+)")


class CronError(Exception):
    pass


def list_jobs(profile: str) -> list[dict[str, Any]]:
    path = hermes_home(profile) / "cron" / "jobs.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return [job for job in jobs or [] if isinstance(job, dict)]


def create(profile: str, *, name: str, schedule: str, prompt: str, deliver: str) -> str:
    result = _run(profile, ["cron", "create", "--name", name, "--deliver", deliver, "--", schedule, prompt])
    match = _CREATED_JOB.search(result.stdout)
    if match is None:
        raise CronError(_detail(result) or "cron create did not report a job id")
    return match.group(1)


def edit(profile: str, job_id: str, *, name: str | None = None, schedule: str | None = None,
         prompt: str | None = None) -> None:
    args = ["cron", "edit", job_id]
    for flag, value in (("--name", name), ("--schedule", schedule), ("--prompt", prompt)):
        if value is not None:
            args += [flag, value]
    _run(profile, args)


def pause(profile: str, job_id: str) -> None:
    _run(profile, ["cron", "pause", job_id])


def resume(profile: str, job_id: str) -> None:
    _run(profile, ["cron", "resume", job_id])


def remove(profile: str, job_id: str) -> None:
    _run(profile, ["cron", "remove", job_id])


def run(profile: str, job_id: str) -> None:
    result = run_hermes(["cron", "run", job_id], profile=profile)
    # The CLI exits 1 when the triggered job itself fails; the trigger still happened.
    if result.returncode != 0 and "Triggered job:" not in result.stdout:
        raise CronError(_detail(result) or "hermes cron run failed")


def _run(profile: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    result = run_hermes(args, profile=profile)
    if result.returncode != 0:
        raise CronError(_detail(result) or f"hermes {' '.join(args[:2])} failed")
    return result


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "").strip()
