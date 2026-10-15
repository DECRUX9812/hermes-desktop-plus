"""Adapter for stock Hermes Kanban operations."""

from __future__ import annotations

import json
import logging
from typing import Any

from hermes_plus.adapter.cli import run_hermes

log = logging.getLogger(__name__)


class KanbanError(RuntimeError):
    """Raised when a Kanban CLI operation fails."""


def create_task(
    title: str,
    *,
    body: str = "",
    assignee: str | None = None,
    created_by: str | None = None,
    idempotency_key: str | None = None,
    board: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Create a Kanban task on stock Hermes."""
    args = ["kanban"]
    if board:
        args.extend(["--board", board])
    args.extend(["create", title])
    if body:
        args.extend(["--body", body])
    if assignee:
        args.extend(["--assignee", assignee])
    if created_by:
        args.extend(["--created-by", created_by])
    if idempotency_key:
        args.extend(["--idempotency-key", idempotency_key])
    args.append("--json")

    proc = run_hermes(args, profile=profile)
    if proc.returncode != 0:
        err_msg = (
            proc.stderr.strip()
            or proc.stdout.strip()
            or f"hermes kanban create failed with exit code {proc.returncode}"
        )
        raise KanbanError(err_msg)

    stdout = proc.stdout.strip()
    if not stdout:
        raise KanbanError("hermes kanban create returned empty output")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise KanbanError(f"failed to parse kanban output: {stdout}") from exc

    if not isinstance(data, dict):
        raise KanbanError(f"unexpected kanban output: {data}")

    return data


def show_task(
    task_id: str,
    *,
    board: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Retrieve details for a single task."""
    args = ["kanban"]
    if board:
        args.extend(["--board", board])
    args.extend(["show", task_id, "--json"])

    proc = run_hermes(args, profile=profile)
    if proc.returncode != 0:
        err_msg = (
            proc.stderr.strip()
            or proc.stdout.strip()
            or f"hermes kanban show failed with exit code {proc.returncode}"
        )
        raise KanbanError(err_msg)

    stdout = proc.stdout.strip()
    if not stdout:
        raise KanbanError("hermes kanban show returned empty output")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise KanbanError(f"failed to parse kanban output: {stdout}") from exc

    if not isinstance(data, dict):
        raise KanbanError(f"unexpected kanban output: {data}")

    return data.get("task", data)


def list_tasks(
    *,
    board: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List tasks on the kanban board."""
    args = ["kanban"]
    if board:
        args.extend(["--board", board])
    args.extend(["list", "--json"])

    proc = run_hermes(args, profile=profile)
    if proc.returncode != 0:
        err_msg = (
            proc.stderr.strip()
            or proc.stdout.strip()
            or f"hermes kanban list failed with exit code {proc.returncode}"
        )
        raise KanbanError(err_msg)

    stdout = proc.stdout.strip()
    if not stdout:
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise KanbanError(f"failed to parse kanban output: {stdout}") from exc

    if not isinstance(data, list):
        raise KanbanError(f"unexpected kanban list output: {data}")

    return data
