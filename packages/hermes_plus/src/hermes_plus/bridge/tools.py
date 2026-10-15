"""Model-tool ports from the fork, registered through the guarded ``register_tool``.

Pure-logic tools run in-process (stdlib); stateful tools (bot team, mailbox) call the sidecar over
loopback (see sidecar.py) so the plus.db state stays in one place. Tool names, schemas and result
shapes match the fork's `tools/*.py`. Skipped ports (apply_layout, verify_preview, mcp_apps) need
a desktop-renderer or MCP-transport bridge that stock Hermes does not expose to plugins; they are
listed in the report instead of registered half-working.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import paths, sidecar
from hermes_plus.bridge.spool import truncate

log = logging.getLogger(__name__)

_GIT_TIMEOUT = 30
_ALLOWED_SCRIPT_SOURCES = ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com")
_MAX_HTML_CHARS = 200_000
_EXTERNAL_SCRIPT = re.compile(r"<script[^>]+src=[\"'](?!https?://(?:cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)/)([^\"']+)[\"']", re.IGNORECASE)
_BLOCKED_CALLS = re.compile(r"\b(fetch\s*\(|XMLHttpRequest\s*\(|WebSocket\s*\(|EventSource\s*\()", re.IGNORECASE)
_FRAME_BUSTING = re.compile(r"\b(top\.location|parent\.location|window\.top)\b", re.IGNORECASE)


def _dump(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


def _run(argv: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8"})
    proc = subprocess.run(
        argv, cwd=cwd, env=env, timeout=_GIT_TIMEOUT, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def register_tools(register: Any) -> list[str]:
    """Register every portable tool through the guarded ``register_tool``."""
    registered: list[str] = []
    for name, toolset, description, parameters, handler in _TOOLS:
        schema = {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": parameters,
                           "required": [key for key, spec in parameters.items() if spec.get("required")]},
        }
        try:
            register(name=name, toolset=toolset, schema=schema, handler=handler,
                     is_async=False, description=description)
        except Exception:
            log.warning("tool %s could not be registered on this Hermes", name)
        else:
            registered.append(f"tool.{name}")
    for skipped in ("apply_layout", "verify_preview", "mcp_apps"):
        log.warning(
            "hermes-plus: fork tool %s is not portable to stock Hermes (desktop-renderer / "
            "MCP-transport internals); it is not registered", skipped)
    return registered


# --- sidecar-backed tools -----------------------------------------------------------

def _tool_args(args: dict[str, Any]) -> dict[str, Any]:
    extra = args.get("params")
    merged = {key: value for key, value in args.items() if key != "params"}
    if isinstance(extra, dict):
        merged.update(extra)
    return merged


def _sidecar_tool(method_prefix: str, actions: tuple[str, ...]) -> Any:
    def handler(args: dict[str, Any], **_kw: Any) -> str:
        action = str(args.get("action") or "")
        if action not in actions:
            return _dump({"error": f"action must be one of {', '.join(actions)}"})
        return _dump(sidecar.call(paths.plus_home(None), f"{method_prefix}.{action}", _tool_args(args)))
    return handler


_BOT_TEAM_ACTIONS = ("create", "get", "list", "update", "delete", "brief", "room_lead",
                     "member.upsert", "member.remove", "goal.upsert", "goal.link_task")
_BOT_MAILBOX_ACTIONS = ("send", "list", "update")

_BOT_TEAM_PARAMS: dict[str, Any] = {
    "action": {"type": "string", "enum": list(_BOT_TEAM_ACTIONS),
               "description": "Team operation to run (bots_team.<action> on the sidecar).",
               "required": True},
    "params": {"type": "object",
               "description": "Operation fields: team_id, name, mission, member/goal fields, actor, …"},
}
_BOT_MAILBOX_PARAMS: dict[str, Any] = {
    "action": {"type": "string", "enum": list(_BOT_MAILBOX_ACTIONS),
               "description": "Mailbox operation: send, list or update.", "required": True},
    "params": {"type": "object",
               "description": "Operation fields: to, title, body, id, status, reply, handle, …"},
}
_UPDATE_TASK_PARAMS: dict[str, Any] = {
    "note": {"type": "string", "description": "The note id from the task marker (mbx_…).",
             "required": True},
    "status": {"type": "string", "enum": ["accepted", "declined", "done"],
               "description": "New status of the task note.", "required": True},
    "reply": {"type": "string", "description": "Short outcome/reason."},
}


def _update_task(args: dict[str, Any], **_kw: Any) -> str:
    if not str(args.get("note") or ""):
        return _dump({"error": "note is required — the mbx_… id from the task marker."})
    params = {"id": args.get("note"), "status": args.get("status"), "reply": args.get("reply") or ""}
    return _dump(sidecar.call(paths.plus_home(None), "bots_mailbox.update", params))


# --- in-process tools ---------------------------------------------------------------

_CREATE_VISUALIZATION_PARAMS: dict[str, Any] = {
    "html": {"type": "string", "description": "Complete self-contained HTML document for the visualization.",
             "required": True},
    "title": {"type": "string", "description": "Short title shown in the card header."},
}


def _create_visualization(args: dict[str, Any], **_kw: Any) -> str:
    """The fork's visualization_tool.py: validate a sandboxed-iframe HTML document and return it."""
    html = args.get("html")
    if not isinstance(html, str) or not html.strip():
        return _dump({"error": "Invalid visualization: html is required"})
    if len(html) > _MAX_HTML_CHARS:
        return _dump({"error": f"Invalid visualization: html exceeds {_MAX_HTML_CHARS} characters"})
    if _EXTERNAL_SCRIPT.search(html):
        return _dump({"error": "Invalid visualization: external <script src> outside the allowed CDNs"})
    if _BLOCKED_CALLS.search(html) or _FRAME_BUSTING.search(html):
        return _dump({"error": "Invalid visualization: network calls and frame busting are not allowed"})
    title = truncate(str(args.get("title") or ""), 120)
    return _dump({"success": True, "visualization": {"title": title, "html": html}})


_GIT_SHIP_PARAMS: dict[str, Any] = {
    "path": {"type": "string", "description": "Repo root. Defaults to the session's working directory."},
    "branch": {"type": "string", "description": "Create and switch to this new branch before committing. "
                                                "Refused if it already exists."},
    "message": {"type": "string", "description": "Commit message (conventional style recommended). "
                                                 "Generated when omitted."},
    "files": {"type": "array", "items": {"type": "string"},
              "description": "Stage only these paths instead of the whole tree."},
    "push": {"type": "boolean", "description": "Push the branch after committing."},
}
_OPEN_PR_PARAMS: dict[str, Any] = {
    "path": {"type": "string", "description": "Repo root. Defaults to the session's working directory."},
    "title": {"type": "string", "description": "Pull request title."},
    "body": {"type": "string", "description": "Pull request body."},
    "base": {"type": "string", "description": "Base branch to open the PR against."},
    "draft": {"type": "boolean", "description": "Open as a draft PR."},
}


def _repo_root(path: str | None) -> tuple[Path, str] | str:
    cwd = Path(path) if path else Path(os.getcwd())
    if not cwd.is_dir():
        return f"not a directory: {cwd}"
    code, out, err = _run(["git", "rev-parse", "--show-toplevel", "--path-format=absolute"], cwd=cwd)
    if code != 0:
        return f"not a git repository: {err.strip() or out.strip()}"
    return cwd, out.strip().splitlines()[0]


def _git_ship(args: dict[str, Any], **_kw: Any) -> str:
    """The fork's git_ship_tool: stage, commit, (optionally branch and push) in one call."""
    resolved = _repo_root(args.get("path"))
    if isinstance(resolved, str):
        return _dump({"error": resolved})
    cwd, root = resolved

    branch = str(args.get("branch") or "")
    if branch:
        code, _out, _err = _run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"], cwd=cwd)
        if code == 0:
            return _dump({"error": f"branch '{branch}' already exists"})
        code, _out, err = _run(["git", "checkout", "-b", branch], cwd=cwd)
        if code != 0:
            return _dump({"error": f"could not create branch '{branch}': {err.strip()}"})

    files = args.get("files")
    if isinstance(files, list) and files:
        code, _out, err = _run(["git", "add", "--", *[str(f) for f in files]], cwd=cwd)
    else:
        code, _out, err = _run(["git", "add", "-A"], cwd=cwd)
    if code != 0:
        return _dump({"error": f"staging failed: {err.strip()}"})

    code, staged, _err = _run(["git", "diff", "--cached", "--numstat"], cwd=cwd)
    if code != 0 or not staged.strip():
        return _dump({"error": "nothing to commit"})
    insertions = sum(int(parts[0]) for line in staged.splitlines() if (parts := line.split("\t"))[0].isdigit())
    deletions = sum(int(parts[1]) for line in staged.splitlines() if len(parts := line.split("\t")) > 1 and parts[1].isdigit())
    file_count = len([line for line in staged.splitlines() if line.strip()])

    message = str(args.get("message") or "")
    if not message:
        message = (f"chore: update {file_count} files" if file_count > 1
                   else f"chore: update {Path(staged.splitlines()[0].split(chr(9))[2]).name}")
    code, _out, err = _run(["git", "commit", "-m", message], cwd=cwd)
    if code != 0:
        return _dump({"error": f"commit failed: {err.strip()}"})

    code, sha, _err = _run(["git", "rev-parse", "HEAD"], cwd=cwd)
    code2, branch_name, _err = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    code3, subject, _err = _run(["git", "log", "-1", "--format=%s"], cwd=cwd)
    pushed, upstream = False, None
    if args.get("push"):
        code4, push_out, push_err = _run(["git", "push", "-u", "origin", branch_name.strip()], cwd=cwd)
        if code4 == 0:
            pushed = True
            upstream = f"origin/{branch_name.strip()}"
        else:
            return _dump({"error": f"commit landed but push failed: {push_err.strip() or push_out.strip()}"})
    return _dump({
        "ok": True, "sha": sha.strip(), "branch": branch_name.strip(), "files": file_count,
        "insertions": insertions, "deletions": deletions, "pushed": pushed,
        "upstream": upstream, "subject": subject.strip() or message.splitlines()[0],
    })


def _open_pr(args: dict[str, Any], **_kw: Any) -> str:
    """The fork's open_pr: push, then `gh pr create`. Needs the gh binary."""
    if not any(Path(entry) / "gh" for entry in os.environ.get("PATH", "").split(os.pathsep)) \
            and subprocess.which("gh") is None:
        return _dump({"error": "the `gh` CLI is required to open a pull request"})
    resolved = _repo_root(args.get("path"))
    if isinstance(resolved, str):
        return _dump({"error": resolved})
    cwd, _root = resolved

    code, _out, err = _run(["git", "push"], cwd=cwd)
    if code != 0:
        return _dump({"error": f"push failed: {err.strip()}"})

    argv = ["gh", "pr", "create"]
    if args.get("title"):
        argv += ["--title", str(args["title"])]
    if args.get("body"):
        argv += ["--body", str(args["body"])]
    if args.get("base"):
        argv += ["--base", str(args["base"])]
    if args.get("draft"):
        argv.append("--draft")
    if not args.get("title") and not args.get("body"):
        argv.append("--fill")
    try:
        code, out, err = _run(argv, cwd=cwd)
    except FileNotFoundError:
        return _dump({"error": "the `gh` CLI is required to open a pull request"})
    if code != 0:
        return _dump({"error": f"gh pr create failed: {err.strip() or out.strip()}"})
    match = re.search(r"https://\S+/pull/(\d+)", out)
    if match is None:
        return _dump({"error": f"could not parse the pull request URL from: {out.strip()[:200]}"})
    return _dump({"ok": True, "url": match.group(0), "number": int(match.group(1)),
                  "draft": bool(args.get("draft"))})


_TOOLS: tuple[tuple[str, str, str, dict[str, Any], Any], ...] = (
    ("bot_team", "bot_room",
     "Manage a bot team (create, inspect, update, brief) through the Hermes Plus sidecar.",
     _BOT_TEAM_PARAMS, _sidecar_tool("bots_team", _BOT_TEAM_ACTIONS)),
    ("bot_mailbox", "bot_room",
     "File or read install-wide task notes between bots and the user through the Hermes Plus sidecar.",
     _BOT_MAILBOX_PARAMS, _sidecar_tool("bots_mailbox", _BOT_MAILBOX_ACTIONS)),
    ("update_task", "bot_room",
     "Update a task note received in a mailbox task marker (accepted/declined/done).",
     _UPDATE_TASK_PARAMS, _update_task),
    ("create_visualization", "visualization",
     "Validate a self-contained HTML visualization document for a sandboxed card in the Plus UI.",
     _CREATE_VISUALIZATION_PARAMS, _create_visualization),
    ("git_ship", "git",
     "Stage, commit and optionally push the working tree in one step; returns the commit summary.",
     _GIT_SHIP_PARAMS, _git_ship),
    ("open_pr", "git",
     "Push the current branch and open a GitHub pull request with the gh CLI.",
     _OPEN_PR_PARAMS, _open_pr),
)
