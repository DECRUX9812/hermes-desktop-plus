"""Fork-added git routes: session-worktree affordances (fork web_routers/git.py:218-235).

Stock Hermes already serves ``/api/git/*`` (status, branches, worktrees,
review/*, file-diff) and those proxy to it unchanged. Only these two additions
are implemented here, with the fork's exact bodies and result shapes, over an
audited subprocess git in the workspace:

  * ``POST /api/git/worktree/ensure``  -> worktree_ensure (fork web_git.py:911-933)
  * ``POST /api/git/worktree/merge``   -> worktree_merge  (fork web_git.py:936-950)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger(__name__)

_GIT_TIMEOUT = 20
_BRANCH_SAFE = re.compile(r"[\x00-\x20\\^:?*\[\]~]+")


class _GitError(RuntimeError):
    """A failed git mutation; mapped to 400 with its text (fork _git_op)."""


def git_path(raw: str) -> str:
    """Resolve a client path the way the fork's _git_path does: file: URIs and
    ~ paths expand, NUL and empties are rejected."""
    from urllib.parse import urlparse, unquote

    path = str(raw or "").strip()
    if not path:
        raise _GitError("Path is required")
    if "\0" in path:
        raise _GitError("Invalid path")
    if path.lower().startswith("file:"):
        parsed = urlparse(path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            raise _GitError("Invalid path")
        path = unquote(parsed.path)
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise _GitError("Invalid path") from exc


def _run_git(cwd: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise _GitError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise _GitError("git operation timed out") from exc


def _git_out(cwd: str, args: list[str]) -> str:
    result = _run_git(cwd, args)
    if result.returncode != 0:
        raise _GitError(result.stderr.strip() or "git operation failed")
    return result.stdout.strip()


def _main_root(cwd: str) -> str:
    """The main checkout a cwd belongs to (a worktree's git dir names its root)."""
    common = _git_out(cwd, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    root = Path(common).resolve().parent
    if not (root / ".git").exists():
        raise _GitError("not a git repository")
    return str(root)


def _sanitize_branch(branch: str) -> str:
    candidate = _BRANCH_SAFE.sub("-", branch.strip()).strip("-.")
    return candidate if candidate and candidate not in ("HEAD",) else ""


def _slugify(name: str) -> str:
    return _BRANCH_SAFE.sub("-", name.strip()).strip("-.")[:80]


def worktree_ensure(cwd: str, worktree_path: str, branch: str) -> dict[str, Any]:
    """Recreate a session worktree whose directory is gone; idempotent."""
    root = _main_root(cwd)
    if Path(worktree_path).is_dir():
        return {"path": worktree_path, "branch": branch, "repoRoot": root, "restored": False}
    _run_git(root, ["worktree", "prune"])
    name = _sanitize_branch(branch) or _slugify(Path(worktree_path).name)
    if not name:
        raise _GitError("worktree path has no usable branch name")
    if _run_git(root, ["worktree", "add", worktree_path, name]).returncode != 0:
        # The branch ref is gone (deleted after the worktree was) — recreate it
        # from the repo's HEAD so the directory (and the session's cwd) is
        # restored rather than erroring the open.
        result = _run_git(root, ["worktree", "add", "-b", name, worktree_path])
        if result.returncode != 0:
            raise _GitError(result.stderr.strip() or "git worktree add failed")
    return {"path": worktree_path, "branch": name, "repoRoot": root, "restored": True}


def worktree_merge(cwd: str, worktree_path: str) -> dict[str, Any]:
    """Merge a session worktree's live branch back into the main checkout."""
    root = _main_root(cwd)
    branch = _git_out(worktree_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not branch or branch == "HEAD":
        raise _GitError(f"no branch checked out in {worktree_path}")
    into = _git_out(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    result = _run_git(root, ["merge", "--no-edit", branch])
    if result.returncode != 0:
        raise _GitError(result.stderr.strip() or result.stdout.strip() or "git merge failed")
    return {"merged": True, "branch": branch, "into": into, "repoRoot": root}


async def _git_op(fn: Any, *args: Any) -> JSONResponse:
    loop = asyncio.get_running_loop()
    try:
        return JSONResponse(await loop.run_in_executor(None, fn, *args))
    except _GitError as exc:
        return JSONResponse({"detail": str(exc) or "git operation failed"}, status_code=400)


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


async def git_worktree_ensure(request: Request) -> JSONResponse:
    body = await _body(request)
    try:
        return await _git_op(
            worktree_ensure,
            git_path(str(body.get("path") or "")),
            git_path(str(body.get("worktreePath") or "")),
            str(body.get("branch") or ""),
        )
    except _GitError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


async def git_worktree_merge(request: Request) -> JSONResponse:
    body = await _body(request)
    try:
        return await _git_op(
            worktree_merge,
            git_path(str(body.get("path") or "")),
            git_path(str(body.get("worktreePath") or "")),
        )
    except _GitError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


routes = [
    Route("/api/git/worktree/ensure", git_worktree_ensure, methods=["POST"]),
    Route("/api/git/worktree/merge", git_worktree_merge, methods=["POST"]),
]
