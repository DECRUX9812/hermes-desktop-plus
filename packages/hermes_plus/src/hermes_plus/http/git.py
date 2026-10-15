"""Git operations via subprocess (workspace-scoped, argument-guarded)."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger(__name__)

# Whitelist of git operations the UI can call
_ALLOWED_GIT_OPERATIONS = frozenset({
    "status",
    "log",
    "diff",
    "branch",
    "worktree",
    "remote",
})

# Argument whitelisting: each operation defines allowed flags
_GIT_ARG_PATTERNS = {
    "status": [r"^--porcelain$", r"^--short$", r"^--long$"],
    "log": [r"^--oneline$", r"^--all$", r"^-n$", r"^\d+$", r"^--.*$"],
    "diff": [r"^--stat$", r"^--.*$"],
    "branch": [r"^-a$", r"^-v$", r"^--list$", r"^.*$"],
    "worktree": [r"^list$", r"^add$", r"^remove$", r"^--.*$"],
    "remote": [r"^-v$", r"^add$", r"^remove$", r"^.*$"],
}


def _validate_git_path(workspace: str | None) -> Path:
    """Validate and resolve git workspace path."""
    if not workspace:
        raise ValueError("workspace required")
    
    try:
        path = Path(workspace).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workspace is not a directory")
        git_dir = path / ".git"
        if not (git_dir.is_dir() or git_dir.is_file()):
            raise ValueError("not a git repository")
        return path
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"Invalid workspace: {exc}")


def _validate_args(operation: str, args: list[str]) -> None:
    """Validate git command arguments for safety."""
    if operation not in _ALLOWED_GIT_OPERATIONS:
        raise ValueError(f"Operation not allowed: {operation}")

    patterns = _GIT_ARG_PATTERNS.get(operation, [])
    for arg in args:
        if not any(__import__("re").match(p, arg) for p in patterns):
            raise ValueError(f"Argument not allowed: {arg}")


async def git_status(request: Request) -> Any:
    """Get git status of a workspace."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)

    workspace = body.get("workspace", "").strip()
    porcelain = body.get("porcelain", False)

    try:
        workspace_path = _validate_git_path(workspace)
        args = ["--porcelain"] if porcelain else []
        _validate_args("status", args)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    try:
        result = subprocess.run(
            ["git", "-C", str(workspace_path), "status"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return JSONResponse(
                {"detail": result.stderr or "git status failed"},
                status_code=400,
            )
        return JSONResponse({
            "status": result.stdout,
            "porcelain": porcelain,
        })
    except subprocess.TimeoutExpired:
        return JSONResponse({"detail": "git command timed out"}, status_code=504)
    except Exception as exc:
        log.exception("git_status error")
        return JSONResponse({"detail": str(exc)}, status_code=500)


async def git_log(request: Request) -> Any:
    """Get git commit log."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)

    workspace = body.get("workspace", "").strip()
    oneline = body.get("oneline", False)
    count = body.get("count", 20)

    try:
        workspace_path = _validate_git_path(workspace)
        args = []
        if oneline:
            args.append("--oneline")
        if count > 0:
            args.extend(["-n", str(min(count, 100))])
        _validate_args("log", args)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    try:
        result = subprocess.run(
            ["git", "-C", str(workspace_path), "log"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return JSONResponse(
                {"detail": result.stderr or "git log failed"},
                status_code=400,
            )
        return JSONResponse({"log": result.stdout})
    except subprocess.TimeoutExpired:
        return JSONResponse({"detail": "git command timed out"}, status_code=504)
    except Exception as exc:
        log.exception("git_log error")
        return JSONResponse({"detail": str(exc)}, status_code=500)


async def git_branch_list(request: Request) -> Any:
    """List git branches."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)

    workspace = body.get("workspace", "").strip()
    all_branches = body.get("all", False)

    try:
        workspace_path = _validate_git_path(workspace)
        args = ["-v"]
        if all_branches:
            args.append("-a")
        _validate_args("branch", args)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    try:
        result = subprocess.run(
            ["git", "-C", str(workspace_path), "branch"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return JSONResponse(
                {"detail": result.stderr or "git branch failed"},
                status_code=400,
            )
        return JSONResponse({"branches": result.stdout})
    except subprocess.TimeoutExpired:
        return JSONResponse({"detail": "git command timed out"}, status_code=504)
    except Exception as exc:
        log.exception("git_branch_list error")
        return JSONResponse({"detail": str(exc)}, status_code=500)


routes = [
    Route("/api/git/status", git_status, methods=["POST"]),
    Route("/api/git/log", git_log, methods=["POST"]),
    Route("/api/git/branch-list", git_branch_list, methods=["POST"]),
]
