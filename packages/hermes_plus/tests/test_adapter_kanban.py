"""Tests for adapter.kanban against stock Hermes CLI."""

from __future__ import annotations

import pytest

from hermes_plus.adapter.kanban import KanbanError, create_task, list_tasks, show_task


def test_kanban_lifecycle():
    task = create_task(
        "Integration test task",
        body="Testing kanban adapter",
        assignee="test-bot",
        created_by="test-suite",
        idempotency_key="test-key-12345",
    )
    assert "id" in task
    task_id = task["id"]
    assert task["title"] == "Integration test task"
    assert task["status"] in ("ready", "todo")

    # show task
    shown = show_task(task_id)
    assert shown["id"] == task_id
    assert shown["title"] == "Integration test task"

    # list tasks
    tasks = list_tasks()
    assert any(t["id"] == task_id for t in tasks)


def test_kanban_invalid_task_id():
    with pytest.raises(KanbanError):
        show_task("t_nonexistent_99999999")
