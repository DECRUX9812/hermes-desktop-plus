"""Tests for adapter.kanban against the stock Hermes CLI.

Real CLI subprocesses, so these are contract tests: they need the configured
stock binary (HERMES_PLUS_TEST_HERMES) and run in a throwaway HERMES_HOME —
never against the ambient install, which may be a personal one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_plus.adapter.kanban import KanbanError, create_task, list_tasks, show_task
from hermes_resolve import prepend_bin_dir, resolve_stock_hermes

pytestmark = pytest.mark.contract


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary, source = resolve_stock_hermes()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.skip(f"stock Hermes executable is unavailable: {binary} ({source})")
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PLUS_HERMES_BIN", str(binary.resolve()))
    monkeypatch.setenv("PATH", prepend_bin_dir(dict(os.environ))["PATH"])
    return home


def test_kanban_lifecycle(kanban_home: Path):
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


def test_kanban_invalid_task_id(kanban_home: Path):
    with pytest.raises(KanbanError):
        show_task("t_nonexistent_99999999")
