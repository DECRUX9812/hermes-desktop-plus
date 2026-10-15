"""Worktree ensure/merge tests over a real git repository in a temp dir."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from conftest import Sidecar

pytestmark = pytest.mark.integration


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30, check=True)
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "demo@example.com")
    _git(root, "config", "user.name", "demo")
    (root / "README.md").write_text("demo repo\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def test_worktree_ensure_restores_missing_dir(sidecar: Sidecar, repo: Path):
    worktree = repo.parent / "wt-demo"
    _git(repo, "branch", "session/demo")
    _git(repo, "worktree", "add", str(worktree), "session/demo")
    (worktree / "note.txt").write_text("wip\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "wip")
    # Simulate the lost-directory case the fork restores: delete behind git's back.
    subprocess.run(["rm", "-rf", str(worktree)], check=True, timeout=30)

    reply = httpx.post(
        f"{sidecar.url}/api/git/worktree/ensure",
        json={"path": str(repo), "worktreePath": str(worktree), "branch": "session/demo"},
        headers=_auth(sidecar),
        timeout=60,
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["restored"] is True
    assert body["branch"] == "session/demo"
    assert body["repoRoot"] == str(repo)
    assert (worktree / "note.txt").read_text(encoding="utf-8") == "wip\n"

    # Idempotent when the directory already exists.
    again = httpx.post(
        f"{sidecar.url}/api/git/worktree/ensure",
        json={"path": str(repo), "worktreePath": str(worktree), "branch": "session/demo"},
        headers=_auth(sidecar),
        timeout=60,
    )
    assert again.status_code == 200
    assert again.json()["restored"] is False


def test_worktree_merge_brings_branch_back(sidecar: Sidecar, repo: Path):
    worktree = repo.parent / "wt-merge"
    _git(repo, "branch", "session/merge-me")
    _git(repo, "worktree", "add", str(worktree), "session/merge-me")
    (worktree / "change.txt").write_text("merged work\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "session work")

    reply = httpx.post(
        f"{sidecar.url}/api/git/worktree/merge",
        json={"path": str(repo), "worktreePath": str(worktree)},
        headers=_auth(sidecar),
        timeout=60,
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["merged"] is True
    assert body["branch"] == "session/merge-me"
    assert body["into"] == "main"
    assert (repo / "change.txt").read_text(encoding="utf-8") == "merged work\n"


def test_worktree_routes_require_auth(sidecar: Sidecar, repo: Path):
    reply = httpx.post(
        f"{sidecar.url}/api/git/worktree/ensure",
        json={"path": str(repo), "worktreePath": str(repo.parent / "wt-x")},
        timeout=10,
    )
    assert reply.status_code == 401


def test_worktree_routes_reject_bad_paths(sidecar: Sidecar):
    reply = httpx.post(
        f"{sidecar.url}/api/git/worktree/merge",
        json={"path": "", "worktreePath": "relative/path"},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert reply.status_code == 400
    assert "Path" in reply.json()["detail"]
