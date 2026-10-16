#!/usr/bin/env python3
"""Backend parity proof for Hermes Desktop Plus, against the running sidecar.

The browser proof (`proof/playwright/proof.mjs`) covers the surfaces a user
clicks. This script covers the parity features whose real surface is the
backend: our JSON-RPC namespaces over `/api/ws`, our own HTTP routes, and the
stock Hermes CLI for the cross-checks that must not be taken on trust (a
routine is a stock cron job; the extras plugins are stock directory plugins).

It talks to the sidecar with the same session credential the browser proof
holds (the one-time `?token=` the sidecar printed is the process token, and the
session header is the same credential `hermes-plus serve` handed the browser),
so nothing here is a second, privileged path. Nothing is stubbed: every call
goes to the live sidecar, which talks to a real `hermes dashboard` child and a
real model provider.

    proof/parity-proof.py --base http://127.0.0.1:9230 --token <token> \
        --hermes-bin /path/to/hermes --demo-project /path/to/demo-project \
        --worktree-path /path/to/scratch/worktree [--model-expected yes|no] \
        [--json report.json]

Prints one `PASS|BROKEN|BLOCKED <name> :: <detail>` line per check and exits
non-zero when any check is broken. `BLOCKED` is used only for a stated external
reason (the proof run had no working model), never to hide a failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import websockets

RPC_TIMEOUT = 240.0
HTTP_TIMEOUT = 120.0
CRON_TIMEOUT = 300
TERMINAL_TIMEOUT = 45.0

#: The host-terminal WS opens with one metadata frame carrying this prefix
#: (`http/host_terminal.py`); every later frame is raw terminal output.
META_PREFIX = "\x00HERMES_TERMINAL_META:"

#: The extras skills `hermes-plus install` copies into `$HERMES_HOME/skills`.
EXTRAS_SKILLS = ("ambition", "first-task", "bot-team-builder", "bot-team-retro", "excalidraw", "initiate-setup")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def terminal_text(raw: str) -> str:
    """Terminal output with escape sequences and CRs removed, so a command's
    result can be matched without the shell's cursor traffic in the way."""
    return _ANSI.sub("", raw).replace("\r", "")


class Reporter:
    def __init__(self) -> None:
        self.results: list[dict[str, str]] = []

    def record(self, name: str, status: str, detail: str) -> None:
        self.results.append({"name": name, "status": status, "detail": detail})
        tag = {"pass": "PASS", "blocked": "BLOCKED"}.get(status, "BROKEN")
        print(f"[{tag}] {name}: {detail}", flush=True)

    async def check(self, name: str, fn: Callable[[], Any]) -> Any:
        try:
            detail = await fn() if asyncio.iscoroutinefunction(fn) else fn()
        except Exception as exc:  # noqa: BLE001 - the exception text is the report
            self.record(name, "broken", f"{type(exc).__name__}: {exc}")
            return None
        self.record(name, "pass", str(detail))
        return detail

    @property
    def broken(self) -> int:
        return sum(1 for row in self.results if row["status"] == "broken")


class Rpc:
    """One JSON-RPC connection to the sidecar's `/api/ws`, credentialled the
    same way the browser is (session token in the query string)."""

    def __init__(self, base: str, token: str) -> None:
        scheme = "wss" if base.startswith("https") else "ws"
        host = base.split("://", 1)[1].rstrip("/")
        self.url = f"{scheme}://{host}/api/ws?token={token}"
        self.next_id = 0
        self.socket: Any = None
        self.events: list[dict[str, Any]] = []

    async def connect(self) -> None:
        self.socket = await websockets.connect(self.url, open_timeout=30, max_size=32 * 1024 * 1024)

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        assert self.socket is not None, "connect() first"
        self.next_id += 1
        request_id = self.next_id
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        await self.socket.send(json.dumps(frame))
        while True:
            answer = json.loads(await asyncio.wait_for(self.socket.recv(), timeout=RPC_TIMEOUT))
            if answer.get("id") != request_id:
                # A server-initiated event (`runtime.broadcast`) rides the same
                # socket; keep it so checks can assert on it.
                if answer.get("method") == "event":
                    self.events.append(answer.get("params") or {})
                continue
            if answer.get("error"):
                raise RuntimeError(f"{method} -> {json.dumps(answer['error'])[:300]}")
            return answer.get("result")

    def event_types(self) -> list[str]:
        return [str(event.get("type")) for event in self.events]

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()


def cron_list(hermes_bin: str, hermes_home: str | None = None) -> str:
    """`hermes cron list --all` in the proof's own HERMES_HOME.

    The home is passed explicitly when the runner knows it (`run-proof.sh`
    hands over the temp home it installed into); otherwise the stock CLI
    inherits the ambient env. Without this the cross-check would read the
    operator's real `~/.hermes` and report a false failure."""
    env = None
    if hermes_home:
        env = {**os.environ, "HERMES_HOME": hermes_home}
    result = subprocess.run(
        [hermes_bin, "cron", "list", "--all"],
        capture_output=True, text=True, timeout=CRON_TIMEOUT, check=False, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"`hermes cron list --all` exited {result.returncode}: {result.stderr.strip()[:300]}")
    return result.stdout


def routine_named(routines: list, name: str, job_id: str | None = None) -> dict | None:
    for routine in routines:
        if job_id is not None and routine.get("id") == job_id:
            return routine
        if job_id is None and routine.get("name") == name:
            return routine
    return None


def _session_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("session_id") or "")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--hermes-bin", required=True)
    parser.add_argument(
        "--hermes-home",
        help="the HERMES_HOME the sidecar serves; the stock CLI cross-checks run against it",
    )
    parser.add_argument(
        "--desktop-plugin",
        default="",
        help="a plugin id the runner installed under <hermes home>/desktop-plugins; its entry is asserted",
    )
    parser.add_argument("--demo-project", required=True, help="the throwaway git repo serve runs in")
    parser.add_argument("--worktree-path", required=True, help="scratch path for the git worktree ensure check")
    parser.add_argument("--profile", default="default")
    parser.add_argument(
        "--model-expected", default="yes",
        help="'no' when the run had no working model provider: session.ask is then BLOCKED, not BROKEN",
    )
    parser.add_argument("--json")
    args = parser.parse_args()

    report = Reporter()
    profile = args.profile
    model_expected = args.model_expected.strip().lower() in ("yes", "1", "true")
    state: dict[str, Any] = {}

    base = args.base.rstrip("/")
    headers = {"X-Hermes-Session-Token": args.token}
    http = httpx.Client(base_url=base, headers=headers, timeout=HTTP_TIMEOUT, follow_redirects=False)

    rpc = Rpc(base, args.token)
    await rpc.connect()
    try:
        # ── sessions: everything below hangs off a real session ──────────────
        # `session.list` rows carry the STORED session key; the session-scoped
        # RPCs resolve against the gateway's live runtime ids, so the session
        # has to be resumed (the same thing the sidebar does when a row is
        # opened) and the runtime id from that reply is what they take.
        listing = await rpc.call("session.list", {"limit": 50})
        rows = [row for row in (listing or {}).get("sessions", []) if _session_id(row)]
        stored_id = _session_id(rows[0]) if rows else ""
        session_id = ""
        if stored_id:
            state["stored_session_id"] = stored_id
            resumed = await rpc.call("session.resume", {"session_id": stored_id})
            session_id = str((resumed or {}).get("session_id") or "")
        report.record(
            "a live session is available to check against",
            "pass" if session_id else "broken",
            f"resumed stored session {stored_id} as runtime session {session_id}"
            + f" ({len((resumed or {}).get('messages') or [])} message(s))" if session_id
            else f"session.list returned {json.dumps(listing)[:200]}",
        )

        # ── 16/30. Skills: the bundled extras skills are installed and listed ─
        async def extras_skills() -> str:
            response = http.get("/api/skills")
            if response.status_code != 200:
                raise RuntimeError(f"GET /api/skills -> HTTP {response.status_code}: {response.text[:200]}")
            skills = response.json()
            if not isinstance(skills, list):
                raise RuntimeError(f"GET /api/skills returned {type(skills).__name__}: {json.dumps(skills)[:200]}")
            by_name = {str(skill.get("name")): skill for skill in skills if isinstance(skill, dict)}
            missing = [name for name in EXTRAS_SKILLS if name not in by_name]
            if missing:
                raise RuntimeError(f"extras skills missing from /api/skills: {missing}; got {sorted(by_name)}")
            disabled = [name for name in EXTRAS_SKILLS if not by_name[name].get("enabled")]
            if disabled:
                raise RuntimeError(f"extras skills installed but disabled: {disabled}")
            return f"{len(skills)} skills listed, all {len(EXTRAS_SKILLS)} extras skills present and enabled"

        await report.check("extras skills are listed by /api/skills", extras_skills)

        async def bridge_tools() -> str:
            # Row 31: the extra model tools the bridge registers on stock's own
            # registry, read back through stock's toolsets route. `apply_layout`,
            # `verify_preview` and `mcp_apps` are deliberately NOT registered
            # (desktop-renderer / MCP-transport internals — see bridge/tools.py);
            # `mcp_apps` is served as the sidecar's own RPC instead, and
            # `browser_step_thumbnail` as `fork.browser.thumbnail` (row 17), so
            # the check asserts both the ports and the documented non-ports.
            response = http.get("/api/tools/toolsets")
            if response.status_code != 200:
                raise RuntimeError(f"GET /api/tools/toolsets -> HTTP {response.status_code}: {response.text[:200]}")
            toolsets = response.json()
            if not isinstance(toolsets, list):
                raise RuntimeError(f"/api/tools/toolsets returned {type(toolsets).__name__}")
            by_name = {
                str(ts.get("name")): [str(tool) for tool in ts.get("tools") or []]
                for ts in toolsets if isinstance(ts, dict)
            }
            ported = ("git_ship", "open_pr", "create_visualization", "bot_team", "bot_mailbox", "update_task")
            registered = {tool for tools in by_name.values() for tool in tools}
            missing = [tool for tool in ported if tool not in registered]
            if missing:
                raise RuntimeError(
                    f"the bridge's tools are missing from stock's registry: {missing};"
                    f" toolsets present: {sorted(by_name)}")
            non_ports = ("apply_layout", "verify_preview", "mcp_apps")
            leaked = [tool for tool in non_ports if tool in registered]
            if leaked:
                raise RuntimeError(
                    f"{leaked} are registered although bridge/tools.py documents them as not portable")
            homes = {tool: [name for name, tools in by_name.items() if tool in tools] for tool in ported}
            return (f"all {len(ported)} ported bridge tools are in stock's registry"
                    f" ({json.dumps(homes)}); the documented non-ports"
                    f" {list(non_ports)} are absent from {len(by_name)} toolset(s)")

        await report.check("the bridge's extra tools are on stock's registry", bridge_tools)

        # ── 22. File upload + 23. files-pane ticketed Range download ─────────
        payload = b"hp-proof-range-0123456789-abcdefghijklmnopqrstuvwxyz"
        upload_name = f"hp-proof-{random.randrange(16**6):06x}.txt"

        async def upload() -> str:
            response = http.post(
                "/api/chat/file-upload",
                files={"file": (upload_name, payload, "text/plain")},
            )
            if response.status_code != 200:
                raise RuntimeError(f"POST /api/chat/file-upload -> HTTP {response.status_code}: {response.text[:200]}")
            body = response.json()
            if body.get("ok") is not True or not body.get("path"):
                raise RuntimeError(f"upload answered {json.dumps(body)[:250]}")
            staged = body.get("staged_upload") or {}
            if not staged.get("path") or not staged.get("install_id"):
                raise RuntimeError(f"upload staged nothing: {json.dumps(body)[:250]}")
            if body.get("size") != len(payload):
                raise RuntimeError(f"upload stored {body.get('size')} bytes, sent {len(payload)}")
            state["upload_path"] = str(body["path"])
            return f"HTTP 200, {body['size']} bytes staged at {Path(body['path']).name}"

        await report.check("POST /api/chat/file-upload accepts a file", upload)

        async def ranged_download() -> str:
            path = state.get("upload_path")
            if not path:
                raise RuntimeError("no uploaded file: the upload step did not complete")
            ticket_response = http.post("/api/files/ticket", json={"route": "download", "path": path})
            if ticket_response.status_code != 200:
                raise RuntimeError(f"POST /api/files/ticket -> HTTP {ticket_response.status_code}: {ticket_response.text[:200]}")
            ticket = ticket_response.json().get("ticket")
            if not ticket:
                raise RuntimeError(f"no ticket in {ticket_response.text[:200]}")
            ranged = http.get(
                "/api/files/download",
                params={"path": path, "ticket": ticket},
                headers={"Range": "bytes=0-9"},
            )
            if ranged.status_code != 206:
                raise RuntimeError(f"ranged GET -> HTTP {ranged.status_code}: {ranged.text[:200]}")
            content_range = ranged.headers.get("content-range", "")
            expected = f"bytes 0-9/{len(payload)}"
            if content_range.replace(" ", "") != expected.replace(" ", ""):
                raise RuntimeError(f"Content-Range is {content_range!r}, expected {expected!r}")
            if ranged.content != payload[:10]:
                raise RuntimeError(f"body is {ranged.content!r}, expected {payload[:10]!r}")
            return f"HTTP 206, Content-Range {content_range}, first 10 bytes match the uploaded file"

        await report.check("a ticketed download honours a Range header (206 + bytes)", ranged_download)

        async def ticket_scope() -> str:
            path = state.get("upload_path")
            if not path:
                raise RuntimeError("no uploaded file: the upload step did not complete")
            ticket = http.post("/api/files/ticket", json={"route": "download", "path": path}).json()["ticket"]
            # The ticket is bound to one exact path: a different path must be refused.
            other = http.get(
                "/api/files/download",
                params={"path": str(Path(path).parent / "not-the-ticketed-file.txt"), "ticket": ticket},
            )
            if other.status_code == 200:
                raise RuntimeError("the ticket also admitted a different path")
            return f"ticket for one path refused another (HTTP {other.status_code})"

        await report.check("a file ticket admits only its own path", ticket_scope)

        # ── 25. Mobile companion ────────────────────────────────────────────
        async def mobile_pairing() -> str:
            response = http.post("/api/mobile/pairing")
            if response.status_code != 200:
                raise RuntimeError(f"POST /api/mobile/pairing -> HTTP {response.status_code}: {response.text[:200]}")
            body = response.json()
            code = str(body.get("code") or "")
            if not code or not isinstance(body.get("expires_in"), int) or body.get("page") != "/mobile":
                raise RuntimeError(f"pairing answered {json.dumps(body)[:250]}")
            state["pairing_code"] = code
            return f"pairing code of {len(code)} chars, expires in {body['expires_in']}s, page {body['page']}"

        await report.check("POST /api/mobile/pairing mints a pairing code", mobile_pairing)

        async def mobile_overview() -> str:
            response = http.get("/api/mobile/overview")
            if response.status_code != 200:
                raise RuntimeError(f"GET /api/mobile/overview -> HTTP {response.status_code}: {response.text[:200]}")
            body = response.json()
            if not isinstance(body.get("sessions"), list) or "generated_at" not in body:
                raise RuntimeError(f"overview answered {json.dumps(body)[:250]}")
            return f"{len(body['sessions'])} session(s) in the phone overview"

        await report.check("GET /api/mobile/overview answers with the session list", mobile_overview)

        async def mobile_pair_exchange() -> str:
            code = state.get("pairing_code")
            if not code:
                raise RuntimeError("no pairing code: the mint step did not complete")
            response = http.post("/api/mobile/pair", json={"code": code})
            if response.status_code != 200:
                raise RuntimeError(f"POST /api/mobile/pair -> HTTP {response.status_code}: {response.text[:200]}")
            token = str(response.json().get("token") or "")
            if not token:
                raise RuntimeError(f"pair answered {response.text[:200]}")
            return "the public pair route exchanged the code for a session token"

        await report.check("the pairing code exchanges for a session token", mobile_pair_exchange)

        async def mobile_write_routes() -> str:
            # Row 25's two write routes. A real reply would run another model
            # turn, so the check drives the doors themselves: both require the
            # session credential (they are the paired phone's authenticated
            # surface, unlike /api/mobile/pair), both refuse an empty body, and a
            # reply for a session the gateway does not hold maps to 404 instead
            # of leaking.
            anonymous = httpx.Client(base_url=base, timeout=HTTP_TIMEOUT, follow_redirects=False)
            try:
                unauthenticated = anonymous.post("/api/mobile/reply", json={})
            finally:
                anonymous.close()
            empty_reply = http.post("/api/mobile/reply", json={})
            empty_respond = http.post("/api/mobile/respond", json={})
            unknown = http.post(
                "/api/mobile/reply", json={"session_id": "proof-does-not-exist", "text": "hello"})
            if unauthenticated.status_code != 401:
                raise RuntimeError(
                    f"an unauthenticated reply answered HTTP {unauthenticated.status_code}, expected 401")
            if empty_reply.status_code != 400 or empty_respond.status_code != 400:
                raise RuntimeError(
                    f"an empty body answered HTTP {empty_reply.status_code} / {empty_respond.status_code},"
                    " expected 400 from both")
            if unknown.status_code != 404:
                raise RuntimeError(
                    f"a reply for an unknown session answered HTTP {unknown.status_code}: {unknown.text[:200]}")
            return ("POST /api/mobile/reply and /api/mobile/respond require the session credential"
                    f" (no header -> 401) and validate their input"
                    f" (empty body -> 400, unknown session -> {unknown.status_code})")

        await report.check("the mobile reply and respond routes answer", mobile_write_routes)

        # ── 26. Webapp windows ──────────────────────────────────────────────
        async def webapp_window() -> str:
            ticket_response = http.post("/api/webapp/window-ticket")
            if ticket_response.status_code != 200:
                raise RuntimeError(f"POST /api/webapp/window-ticket -> HTTP {ticket_response.status_code}: {ticket_response.text[:200]}")
            ticket = str(ticket_response.json().get("ticket") or "")
            if not ticket:
                raise RuntimeError(f"no ticket in {ticket_response.text[:200]}")
            # The window page hands its ticket to this public route; the ticket
            # is bound to the origin and consumed on redeem.
            session = http.post(
                "/webapp/window-session",
                headers={"X-Hermes-Window-Ticket": ticket, "Origin": base},
            )
            if session.status_code != 200:
                raise RuntimeError(f"POST /webapp/window-session -> HTTP {session.status_code}: {session.text[:200]}")
            token = str(session.json().get("token") or "")
            if not token:
                raise RuntimeError(f"window-session answered {session.text[:200]}")
            replay = http.post(
                "/webapp/window-session",
                headers={"X-Hermes-Window-Ticket": ticket, "Origin": base},
            )
            if replay.status_code == 200:
                raise RuntimeError("the window ticket was accepted twice")
            return f"window ticket minted and exchanged for a token (replay -> HTTP {replay.status_code})"

        await report.check("a webapp window ticket exchanges for a window session", webapp_window)

        # ── 21. Host terminal (WS) ──────────────────────────────────────────
        async def host_terminal() -> str:
            marker = f"hp-proof-{random.randrange(16**8):08x}"
            # The shell evaluates the arithmetic, so the echoed value can only
            # come from a command that really ran in the PTY.
            command = f"echo {marker}-$((6*7))"
            expected = f"{marker}-42"
            scheme = "wss" if base.startswith("https") else "ws"
            host = base.split("://", 1)[1]
            url = f"{scheme}://{host}/api/host-terminal?token={args.token}&cols=100&rows=30"
            collected = ""
            async with websockets.connect(url, open_timeout=30, max_size=None) as socket:
                first = await asyncio.wait_for(socket.recv(), timeout=TERMINAL_TIMEOUT)
                if not isinstance(first, str) or not first.startswith(META_PREFIX):
                    raise RuntimeError(f"the first frame was not terminal metadata: {str(first)[:200]!r}")
                meta = json.loads(first[len(META_PREFIX):])
                if not meta.get("shell") or not meta.get("terminalId"):
                    raise RuntimeError(f"terminal metadata is incomplete: {json.dumps(meta)[:200]}")
                await socket.send(command + "\n")
                deadline = asyncio.get_running_loop().time() + TERMINAL_TIMEOUT
                while expected not in collected:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise RuntimeError(
                            f"the terminal never printed {expected!r}; saw {terminal_text(collected)[-200:]!r}")
                    try:
                        chunk = await asyncio.wait_for(socket.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode("utf-8", "replace")
                    collected += chunk
            if expected not in collected:
                raise RuntimeError(f"the terminal never printed {expected!r}; saw {terminal_text(collected)[-200:]!r}")
            return f"WS /api/host-terminal ran {command!r} in {meta['shell']} and echoed {expected}"

        await report.check("the host terminal runs a command and streams its output", host_terminal)

        # ── 4/5/6/7/9/10/11/17/18/19. Backend surfaces answer well-formed ───
        async def approvals_list() -> str:
            if not session_id:
                raise RuntimeError("no session to query")
            result = await rpc.call("fork.approvals.list", {"session_id": session_id})
            rows = result.get("approvals") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError(f"fork.approvals.list answered {json.dumps(result)[:250]}")
            return (f"{{'approvals': []}} — no approval was requested in this fresh install"
                    if not rows else f"{len(rows)} approval row(s)")

        await report.check("fork.approvals.list answers", approvals_list)

        async def delegation_reports() -> str:
            if not session_id:
                raise RuntimeError("no session to query")
            result = await rpc.call("delegation.reports", {"session_id": session_id})
            rows = result.get("reports") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError(f"delegation.reports answered {json.dumps(result)[:250]}")
            return (f"{{'reports': []}} — no delegation ran in this fresh install"
                    if not rows else f"{len(rows)} delegation report(s)")

        await report.check("delegation.reports answers", delegation_reports)

        async def browser_timeline() -> str:
            tool_call_id = f"proof-{random.randrange(16**8):08x}"
            thumbnail = await rpc.call("fork.browser.thumbnail", {"tool_call_id": tool_call_id})
            snapshot = await rpc.call("fork.browser.snapshot", {"tool_call_id": tool_call_id})
            if thumbnail.get("found") is not False or snapshot.get("found") is not False:
                raise RuntimeError(
                    f"unknown tool_call_id was not 'found: false': {json.dumps(thumbnail)[:150]} / {json.dumps(snapshot)[:150]}")
            return ("{'found': false} for an unknown tool_call_id in both "
                    "fork.browser.thumbnail and fork.browser.snapshot (no browser step ran)")

        await report.check("fork.browser.thumbnail/snapshot answer", browser_timeline)

        async def computer_timeline() -> str:
            if not session_id:
                raise RuntimeError("no session to query")
            result = await rpc.call("fork.computer.timeline", {"profile": profile, "session_id": session_id})
            steps = result.get("steps") if isinstance(result, dict) else None
            todos = result.get("todos") if isinstance(result, dict) else None
            if not isinstance(steps, list) or not isinstance(todos, list):
                raise RuntimeError(f"fork.computer.timeline answered {json.dumps(result)[:250]}")
            return (f"session_id echoed, {len(steps)} step(s), {len(todos)} todo(s)"
                    + (" — empty because the bridge recorded no tool call for this session"
                       if not steps and not todos else ""))

        await report.check("fork.computer.timeline answers", computer_timeline)

        async def mcp_apps_list() -> str:
            result = await rpc.call("mcp_apps.list", {"profile": profile})
            apps = result.get("apps") if isinstance(result, dict) else None
            if not isinstance(apps, list):
                raise RuntimeError(f"mcp_apps.list answered {json.dumps(result)[:250]}")
            return (f"{{'apps': []}} — no MCP server is configured in this profile"
                    if not apps else f"{len(apps)} MCP app(s)")

        await report.check("mcp_apps.list answers", mcp_apps_list)

        async def onboarding_state() -> str:
            result = await rpc.call("fork.onboarding.state", {})
            keys = {"eligible", "profile", "intro", "failed_starts", "completed_at"}
            if not isinstance(result, dict) or not keys.issubset(result):
                raise RuntimeError(f"fork.onboarding.state answered {json.dumps(result)[:250]}")
            return (f"eligible={result['eligible']}, intro={result['intro']}, "
                    f"failed_starts={result['failed_starts']}, completed_at={result['completed_at']}")

        await report.check("fork.onboarding.state answers", onboarding_state)

        async def onboarding_setup_profile() -> str:
            # The setup profile is the real onboarding side effect: a profile the
            # adapter creates through the stock CLI, idempotently. The reset call
            # first is the onboarding page's own "start over" action, and it makes
            # this check independent of whether the home already had one.
            await rpc.call("fork.onboarding.reset_setup_profile", {})
            first = await rpc.call("fork.onboarding.ensure_setup_profile", {})
            if not first.get("name") or not first.get("path") or first.get("created") is not True:
                raise RuntimeError(f"ensure_setup_profile answered {json.dumps(first)[:250]}")
            if not Path(str(first["path"])).is_dir():
                raise RuntimeError(f"the setup profile directory does not exist: {first['path']}")
            second = await rpc.call("fork.onboarding.ensure_setup_profile", {})
            if second.get("created") is not False or second.get("path") != first["path"]:
                raise RuntimeError(f"ensure_setup_profile is not idempotent: {json.dumps(second)[:250]}")
            state["setup_profile"] = str(first["name"])
            return f"profile {first['name']} created then reused (created true -> false)"

        await report.check("fork.onboarding.ensure_setup_profile creates a real profile", onboarding_setup_profile)

        async def profiles_list() -> str:
            result = await rpc.call("profiles.list", {})
            rows = result.get("profiles") if isinstance(result, dict) else None
            if not isinstance(rows, list) or not rows:
                raise RuntimeError(f"profiles.list answered {json.dumps(result)[:250]}")
            names = [str(row.get("name")) for row in rows if isinstance(row, dict)]
            if profile not in names:
                raise RuntimeError(f"profiles.list has no {profile}: {names}")
            return f"profiles.list returned {names}"

        await report.check("profiles.list answers with the profile rows", profiles_list)

        async def memory_list() -> str:
            result = await rpc.call("fork.memory.list", {"profile": profile})
            if not isinstance(result, dict) or not isinstance(result.get("entries"), list):
                raise RuntimeError(f"fork.memory.list answered {json.dumps(result)[:250]}")
            usage = result.get("usage") or {}
            return f"{len(result['entries'])} memory entr(ies), usage keys {sorted(usage)}"

        await report.check("fork.memory.list answers", memory_list)

        async def mailbox_round_trip() -> str:
            title = f"S39 proof note {random.randrange(16**6):06x}"
            sent = await rpc.call("bots_mailbox.send", {
                "to": "proof-bot", "title": title, "body": "Backend parity proof note.",
                "profile": profile,
            })
            note = sent.get("note") if isinstance(sent, dict) else None
            if not isinstance(note, dict) or not note.get("id"):
                raise RuntimeError(f"bots_mailbox.send answered {json.dumps(sent)[:250]}")
            listed = await rpc.call("bots_mailbox.list", {})
            notes = listed.get("notes") if isinstance(listed, dict) else None
            if not isinstance(notes, list) or not any(row.get("id") == note["id"] for row in notes):
                raise RuntimeError(f"bots_mailbox.list does not hold the note: {json.dumps(listed)[:250]}")
            updated = await rpc.call("bots_mailbox.update", {"id": note["id"], "status": "done", "reply": "noted"})
            status = (updated.get("note") or {}).get("status") if isinstance(updated, dict) else None
            if status != "done":
                raise RuntimeError(f"bots_mailbox.update answered {json.dumps(updated)[:250]}")
            # Row 11: plus emits the fork-only relay event on every mailbox write.
            relay_events = [name for name in rpc.event_types() if name == "bot_relay.outbox.pending"]
            if not relay_events:
                raise RuntimeError(f"no bot_relay.outbox.pending event; saw {rpc.event_types()[-8:]}")
            return (f"note {note['id']} sent, listed and moved to done; "
                    f"{len(relay_events)} bot_relay.outbox.pending event(s) emitted")

        await report.check("bots_mailbox send/list/update round-trips (and emits the relay event)", mailbox_round_trip)

        async def relay_extensions() -> str:
            # Rows 3 and 11: the relay extensions the bots plugin drives. The
            # "retry now" door on a run card is exactly `bot_relay.outbox.drain`,
            # and the roster panel reconciles through `bot_relay.roster.sync`;
            # both are stock methods the sidecar proxies, so answering them
            # proves the door exists on this install (the pending EVENT is
            # proved by the mailbox check above).
            drain = await rpc.call("bot_relay.outbox.drain", {})
            if not isinstance(drain, dict):
                raise RuntimeError(f"bot_relay.outbox.drain answered {json.dumps(drain)[:250]}")
            sync = await rpc.call("bot_relay.roster.sync", {})
            if not isinstance(sync, dict):
                raise RuntimeError(f"bot_relay.roster.sync answered {json.dumps(sync)[:250]}")
            return (f"bot_relay.outbox.drain -> {json.dumps(drain)[:120]};"
                    f" bot_relay.roster.sync -> {json.dumps(sync)[:120]}")

        await report.check("the relay outbox drain and roster sync answer", relay_extensions)

        async def team_mode() -> str:
            # Rows 8 and 13: the team-mode verbs the bot-room UI drives — a goal,
            # the task it spawns onto the kanban board, the budget ledger, the
            # audit trail and the exportable pack.
            name = f"S39 team mode {random.randrange(16**6):06x}"
            created = await rpc.call("bots_team.create", {
                "name": name, "mission": "Prove the team-mode surfaces.", "lead_decides": True})
            team = (created or {}).get("team") or {}
            team_id = str(team.get("id") or "")
            if not team_id:
                raise RuntimeError(f"bots_team.create answered {json.dumps(created)[:250]}")
            try:
                seated = await rpc.call("bots_team.member.upsert", {
                    "team_id": team_id, "profile": "proof-bot", "role": "prover", "monthly_usd": 10})
                slot = str(((seated or {}).get("member") or {}).get("slot") or "you")
                goal_title = f"Ship goal {random.randrange(16**4):04x}"
                upserted = await rpc.call("bots_team.goal.upsert", {
                    "team_id": team_id, "title": goal_title, "status": "active", "owner": slot})
                goal_id = str(((upserted or {}).get("goal") or {}).get("id") or "")
                if not goal_id:
                    raise RuntimeError(f"bots_team.goal.upsert answered {json.dumps(upserted)[:250]}")
                spawned = await rpc.call("bots_team.goal.spawn_task", {
                    "team_id": team_id, "goal_id": goal_id, "assignee": slot,
                    "title": f"Ship task {random.randrange(16**4):04x}"})
                task_id = str((spawned or {}).get("task_id") or "")
                if not task_id:
                    raise RuntimeError(f"bots_team.goal.spawn_task answered {json.dumps(spawned)[:250]}")
                budget = await rpc.call("bots_team.budget.check", {"team_id": team_id, "member": slot})
                recorded = await rpc.call("bots_team.budget.record", {
                    "team_id": team_id, "member": slot, "usd": 0.25})
                brief = await rpc.call("bots_team.brief", {"team_id": team_id, "member": slot})
                pack = await rpc.call("bots_team.pack.export", {"team_id": team_id})
                audit = await rpc.call("bots_team.audit.list", {"team_id": team_id})
                if not isinstance((pack or {}).get("pack"), dict):
                    raise RuntimeError(f"bots_team.pack.export answered {json.dumps(pack)[:250]}")
                entries = (audit or {}).get("entries")
                if not isinstance(entries, list) or len(entries) < 4:
                    raise RuntimeError(f"bots_team.audit.list answered {json.dumps(audit)[:250]}")
                return (f"team {team_id}: goal {goal_id} spawned kanban task {task_id},"
                        f" budget.check/record answered, brief {len(json.dumps(brief))} bytes,"
                        f" pack exported, {len(entries)} audit entries"
                        f" (budget {json.dumps(budget)[:70]}, spend {json.dumps(recorded)[:70]})")
            finally:
                await rpc.call("bots_team.delete", {"team_id": team_id})

        await report.check("the team-mode goal/budget/audit/pack verbs answer", team_mode)

        async def desktop_plugins() -> str:
            root = http.get("/api/desktop-plugins/root")
            listing = http.get("/api/desktop-plugins/list")
            if root.status_code != 200 or listing.status_code != 200:
                raise RuntimeError(
                    f"/api/desktop-plugins/root -> {root.status_code}, /list -> {listing.status_code}")
            root_body = root.json()
            entries = listing.json().get("entries")
            if not root_body.get("root") or not isinstance(entries, list):
                raise RuntimeError(f"root {root.text[:120]}, list {listing.text[:120]}")
            names = [str(entry.get("name")) for entry in entries if isinstance(entry, dict)]
            detail = f"root {Path(str(root_body['root'])).name}, {len(entries)} desktop-plugin entr(ies)"
            if args.desktop_plugin:
                # The runner installed this one through the disk door; the route
                # must list it AND hand back its plugin.js source.
                if args.desktop_plugin not in names:
                    raise RuntimeError(f"/api/desktop-plugins/list has no {args.desktop_plugin}: {names}")
                source = http.get("/api/desktop-plugins/source", params={"name": args.desktop_plugin})
                if source.status_code != 200:
                    raise RuntimeError(
                        f"/api/desktop-plugins/source?name={args.desktop_plugin} -> HTTP {source.status_code}:"
                        f" {source.text[:200]}")
                body = source.json()
                if body.get("name") != "plugin.js" or not str(body.get("text") or "").strip():
                    raise RuntimeError(f"source answered {json.dumps(body)[:200]}")
                refused = http.get(
                    "/api/desktop-plugins/source",
                    params={"name": args.desktop_plugin, "file": "README.md"},
                )
                if refused.status_code == 200:
                    raise RuntimeError("the source route read a file outside plugin.js / .hermes-package.json")
                detail += (f"; {args.desktop_plugin} is listed and its plugin.js source is readable"
                           f" ({len(str(body['text']))} chars, README.md refused with HTTP {refused.status_code})")
            return detail

        await report.check("the desktop-plugins routes answer", desktop_plugins)

        async def approvals_decide() -> str:
            # Row 4: the journal's write half. Nothing is pending in a fresh
            # install, so the real check is that `decide` is served and validates
            # its input instead of failing as an unknown method.
            try:
                await rpc.call("fork.approvals.decide", {
                    "session_id": session_id or "proof-session",
                    "request_id": "proof-does-not-exist",
                    "choice": "once",
                })
            except RuntimeError as exc:
                text = str(exc)
                if "method" in text.lower() and "not found" in text.lower():
                    raise RuntimeError(f"fork.approvals.decide is not served: {text[:200]}") from exc
                return f"fork.approvals.decide is served and refused an unknown approval: {text[:180]}"
            raise RuntimeError("fork.approvals.decide accepted an approval id that does not exist")

        await report.check("fork.approvals.decide answers", approvals_decide)

        async def memory_round_trip() -> str:
            # Row 10: the write half of the memory store, against the real
            # MEMORY.md every mutation rewrites. An entry's id is a hash of its
            # text, so editing it mints a new id — the check follows the id the
            # store hands back rather than assuming the old one survives.
            text = f"S39 proof memory {random.randrange(16**6):06x}"
            added = await rpc.call("fork.memory.add", {"profile": profile, "target": "memory", "text": text})
            entry = next((row for row in (added or {}).get("entries", []) if row.get("text") == text), None)
            if not entry:
                raise RuntimeError(f"fork.memory.add did not list the new entry: {json.dumps(added)[:250]}")
            first_id = str(entry["id"])
            edited = text + " (edited)"
            updated = await rpc.call(
                "fork.memory.update", {"profile": profile, "id": first_id, "text": edited})
            entry = next((row for row in (updated or {}).get("entries", []) if row.get("text") == edited), None)
            if not entry:
                raise RuntimeError(f"fork.memory.update did not rewrite the entry: {json.dumps(updated)[:250]}")
            entry_id = str(entry["id"])
            listed = await rpc.call("fork.memory.list", {"profile": profile})
            if not any(str(row.get("id")) == entry_id for row in listed.get("entries", [])):
                raise RuntimeError(f"fork.memory.list lost {entry_id}: {json.dumps(listed)[:250]}")
            removed = await rpc.call("fork.memory.remove", {"profile": profile, "id": entry_id})
            if any(str(row.get("id")) == entry_id for row in (removed or {}).get("entries", [])):
                raise RuntimeError(f"fork.memory.remove left {entry_id}: {json.dumps(removed)[:250]}")
            return (f"memory entry added ({first_id}), edited to {entry_id}, listed and removed;"
                    f" usage {json.dumps((removed or {}).get('usage'))[:80]}")

        await report.check("fork.memory add/update/list/remove round-trips", memory_round_trip)

        async def onboarding_flow() -> str:
            # Row 15: the rest of the onboarding surface around the state call.
            marked = await rpc.call("fork.onboarding.mark_seen", {})
            if not isinstance(marked, dict) or marked.get("intro") != "seen":
                raise RuntimeError(f"fork.onboarding.mark_seen answered {json.dumps(marked)[:250]}")
            failed = await rpc.call("fork.onboarding.record_failed_start", {})
            if not isinstance(failed, dict) or not isinstance(failed.get("failed_starts"), int):
                raise RuntimeError(f"fork.onboarding.record_failed_start answered {json.dumps(failed)[:250]}")
            reset = await rpc.call("fork.onboarding.reset_setup_profile", {})
            if not isinstance(reset, dict) or reset.get("reset") is not True or not reset.get("name"):
                raise RuntimeError(f"fork.onboarding.reset_setup_profile answered {json.dumps(reset)[:250]}")
            setup = await rpc.call("fork.onboarding.ensure_setup_session", {})
            if not isinstance(setup, dict) or not setup.get("session_id"):
                raise RuntimeError(f"fork.onboarding.ensure_setup_session answered {json.dumps(setup)[:250]}")
            return (f"mark_seen -> intro {marked['intro']}, record_failed_start -> {failed['failed_starts']},"
                    f" reset_setup_profile -> {reset['name']},"
                    f" ensure_setup_session -> session {setup['session_id']} (empty {setup.get('empty')})")

        await report.check("the onboarding flow answers (mark_seen / failed start / reset / setup session)",
                           onboarding_flow)

        async def routines_controls() -> str:
            # Row 14's remaining lifecycle verbs, on a routine of its own.
            name = f"S39 control routine {random.randrange(16**6):06x}"
            created = await rpc.call("fork.routines.create", {
                "profile": profile, "name": name, "schedule": "every 6h", "kind": "schedule",
                "prompt": "Reply with the single word: routine-proof.",
            })
            routine = routine_named(created.get("routines", []), name)
            if routine is None:
                raise RuntimeError(f"fork.routines.create listed {json.dumps(created)[:250]}")
            job_id = str(routine["id"])
            try:
                updated = await rpc.call("fork.routines.update", {
                    "profile": profile, "id": job_id, "schedule": "every 12h"})
                paused = await rpc.call("fork.routines.pause", {"profile": profile, "id": job_id})
                resumed = await rpc.call("fork.routines.resume", {"profile": profile, "id": job_id})
                ran = await rpc.call("fork.routines.run_now", {"profile": profile, "id": job_id})
                row = routine_named((resumed or {}).get("routines", []), name, job_id)
                if row is None or row.get("enabled") is not True:
                    raise RuntimeError(f"fork.routines.resume answered {json.dumps(resumed)[:250]}")
                return (f"routine {job_id} updated to every 12h"
                        f" ({json.dumps(updated)[:60]}), paused, resumed, run_now -> {json.dumps(ran)[:80]}")
            finally:
                await rpc.call("fork.routines.remove", {"profile": profile, "id": job_id})

        await report.check("fork.routines update/pause/resume/run_now answer", routines_controls)

        async def mcp_apps_calls() -> str:
            # Row 19's other two verbs: `call` / `read_ui` must be served and
            # must refuse an unknown server rather than answer as unknown methods.
            attempts = (
                ("mcp_apps.call", {"server": "proof-does-not-exist", "tool": "x", "arguments": {}}),
                ("mcp_apps.read_ui", {"server": "proof-does-not-exist", "uri": "ui://proof/x"}),
            )
            answers = []
            for method, params in attempts:
                try:
                    await rpc.call(method, params)
                except RuntimeError as exc:
                    text = str(exc)
                    if "not found" in text.lower() and "method" in text.lower():
                        raise RuntimeError(f"{method} is not served: {text[:200]}") from exc
                    answers.append(f"{method} refused an unknown server ({text[:80]})")
                else:
                    raise RuntimeError(f"{method} accepted an MCP server that does not exist")
            return "; ".join(answers)

        await report.check("mcp_apps.call and mcp_apps.read_ui answer", mcp_apps_calls)

        async def share_lifecycle() -> str:
            # Row 24's other two routes, on a grant of their own.
            stored = str(state.get("stored_session_id") or "")
            if not stored:
                raise RuntimeError("no stored session id: the resume step did not complete")
            created = http.post("/api/share/create", json={"session_id": stored})
            if created.status_code != 200:
                raise RuntimeError(f"POST /api/share/create -> HTTP {created.status_code}: {created.text[:200]}")
            grant = created.json()
            token = str(grant.get("token") or "")
            listed = http.get("/api/share/list", params={"session_id": stored})
            if listed.status_code != 200 or not any(
                    # The list route is a management view: it returns a masked
                    # `token_preview`, never the token itself.
                    str(row.get("token_preview")) == token[:6] for row in listed.json().get("shares", [])):
                raise RuntimeError(f"GET /api/share/list does not hold the grant: {listed.text[:250]}")
            revoked = http.post("/api/share/revoke", json={"token": token})
            if revoked.status_code != 200 or revoked.json().get("revoked") is not True:
                raise RuntimeError(f"POST /api/share/revoke -> HTTP {revoked.status_code}: {revoked.text[:200]}")
            view = http.get("/api/share/view", params={"t": token})
            if view.status_code == 200:
                raise RuntimeError("a revoked share token still renders the projection")
            return (f"grant {token[:8]}… created, listed, revoked (revoked=true),"
                    f" and the revoked token now answers HTTP {view.status_code}")

        await report.check("share create/list/revoke round-trips", share_lifecycle)

        async def webapp_pages() -> str:
            # Row 26's two HTML doors: the launcher and the child window.
            launch = http.get("/webapp/launch")
            window = http.get("/webapp/window")
            if launch.status_code != 200 or window.status_code != 200:
                raise RuntimeError(f"/webapp/launch -> {launch.status_code}, /webapp/window -> {window.status_code}")
            if "text/html" not in launch.headers.get("content-type", ""):
                raise RuntimeError(f"/webapp/launch served {launch.headers.get('content-type')!r}")
            return (f"GET /webapp/launch and /webapp/window -> 200 HTML"
                    f" ({len(launch.text)} and {len(window.text)} bytes)")

        await report.check("the webapp launcher and child-window pages render", webapp_pages)

        async def worktree_merge() -> str:
            # Row 28's other half: merging a session worktree back.
            worktree_path = Path(args.worktree_path)
            if not worktree_path.is_dir():
                raise RuntimeError("no worktree: the ensure step did not complete")
            response = http.post("/api/git/worktree/merge", json={
                "path": args.demo_project, "worktreePath": str(worktree_path)})
            if response.status_code not in (200, 409):
                raise RuntimeError(f"POST /api/git/worktree/merge -> HTTP {response.status_code}: {response.text[:200]}")
            return (f"POST /api/git/worktree/merge answered HTTP {response.status_code}"
                    f" ({json.dumps(response.json())[:140]})")

        await report.check("POST /api/git/worktree/merge answers", worktree_merge)

        async def git_worktree() -> str:
            worktree_path = Path(args.worktree_path)
            # `--worktree-path` is the proof's own scratch path (under the run's
            # temp WORK dir). Clear a stale one so the check always measures a
            # real create, and prune the demo repo's matching metadata.
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
                subprocess.run(["git", "-C", args.demo_project, "worktree", "prune"],
                               capture_output=True, text=True, timeout=120, check=False)
            body = {"path": args.demo_project, "worktreePath": str(worktree_path), "branch": "proof-worktree"}
            response = http.post("/api/git/worktree/ensure", json=body)
            if response.status_code != 200:
                raise RuntimeError(f"POST /api/git/worktree/ensure -> HTTP {response.status_code}: {response.text[:200]}")
            result = response.json()
            if result.get("restored") is not True or not worktree_path.is_dir():
                raise RuntimeError(f"ensure did not restore the worktree: {json.dumps(result)[:250]}")
            if result.get("branch") != "proof-worktree" or not result.get("repoRoot"):
                raise RuntimeError(f"ensure answered {json.dumps(result)[:250]}")
            again = http.post("/api/git/worktree/ensure", json=body).json()
            if again.get("restored") is not False:
                raise RuntimeError(f"ensure is not idempotent: {json.dumps(again)[:250]}")
            return (f"worktree {worktree_path.name} created on branch {result['branch']} "
                    f"under {Path(str(result['repoRoot'])).name}, then reported restored:false")

        await report.check("POST /api/git/worktree/ensure restores a session worktree", git_worktree)

        # ── 5/6. session.ask and session.predict_next ───────────────────────
        async def session_ask() -> str:
            if not session_id:
                raise RuntimeError("no session to ask about")
            try:
                result = await rpc.call("session.ask", {
                    "session_id": session_id,
                    "question": "In one short sentence, what did the user ask for in this conversation?",
                })
            except Exception as exc:  # noqa: BLE001 - classified below
                if not model_expected:
                    report.record("session.ask answers about the session", "blocked",
                                  f"no model provider in this run: {exc}")
                    return None
                raise
            answer = result.get("answer") if isinstance(result, dict) else None
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError(f"session.ask answered {json.dumps(result)[:250]}")
            if not isinstance(result.get("messages_considered"), int):
                raise RuntimeError(f"session.ask omitted messages_considered: {json.dumps(result)[:250]}")
            state["ask_answer"] = answer
            return (f"answer from the model about the resumed session "
                    f"({result['messages_considered']} message(s) considered): "
                    f"{answer.strip().replace(chr(10), ' ')[:160]}")

        asked = await report.check("session.ask answers about the session", session_ask)

        async def predict_next() -> str:
            if not session_id:
                raise RuntimeError("no session to predict for")
            # run-proof.sh turns the auxiliary task on in the proof's own temp
            # home, so a suggestion is expected. An empty `{text: ""}` is a
            # legitimate model answer (the instructions say to output nothing
            # when there is no confident next message), so the reply is retried
            # once before it is reported as a shape-only pass.
            last = ""
            for _ in range(2):
                result = await rpc.call("session.predict_next", {"session_id": session_id})
                text = result.get("text") if isinstance(result, dict) else None
                if not isinstance(text, str):
                    raise RuntimeError(f"session.predict_next answered {json.dumps(result)[:250]}")
                if text.strip():
                    return f"a suggestion came back: {text.strip()[:120]}"
                last = text
            return ("{'text': ''} on both attempts — the RPC answers its documented shape,"
                    " but the model declined to predict a next message"
                    f" (auxiliary.composer_prediction.enabled is on; last reply {last!r})")

        await report.check("session.predict_next answers with {text}", predict_next)

        # ── 14. Routines are real stock cron jobs ───────────────────────────
        routine_name = f"S39 proof routine {random.randrange(16**6):06x}"

        async def routine_create() -> str:
            listing_result = await rpc.call("fork.routines.create", {
                "profile": profile,
                "name": routine_name,
                "schedule": "every 3h",
                "kind": "schedule",
                "prompt": "Reply with the single word: routine-proof.",
            })
            routine = routine_named(listing_result.get("routines", []), routine_name)
            if routine is None:
                raise RuntimeError(f"fork.routines.create listed {json.dumps(listing_result)[:300]}")
            state["job_id"] = routine["id"]
            if routine.get("enabled") is not True or routine.get("deliver") != f"bot-chat:{profile}":
                raise RuntimeError(f"the created routine is malformed: {json.dumps(routine)[:250]}")
            return f"fork.routines.create made cron job {routine['id']} ({routine['schedule']})"

        await report.check("fork.routines.create makes a routine", routine_create)

        async def routine_listed() -> str:
            job_id = str(state.get("job_id") or "")
            listing_result = await rpc.call("fork.routines.list", {"profile": profile})
            routine = routine_named(listing_result.get("routines", []), routine_name, job_id or None)
            if routine is None:
                raise RuntimeError(f"fork.routines.list has no {routine_name}: {json.dumps(listing_result)[:300]}")
            return f"fork.routines.list returned {len(listing_result['routines'])} routine(s) including {routine_name}"

        await report.check("fork.routines.list returns the routine", routine_listed)

        async def routine_in_stock_cron() -> str:
            job_id = str(state.get("job_id") or "")
            if not job_id:
                raise RuntimeError("no routine id: the create step did not complete")
            output = cron_list(args.hermes_bin, args.hermes_home)
            if job_id not in output:
                raise RuntimeError(f"`hermes cron list --all` has no job {job_id}: {output.strip()[:300]}")
            return f"`hermes cron list --all` shows job {job_id} in the proof's HERMES_HOME"

        await report.check("stock Hermes cron holds the routine as a job", routine_in_stock_cron)

        async def routine_removed() -> str:
            job_id = str(state.get("job_id") or "")
            if not job_id:
                raise RuntimeError("no routine id: the create step did not complete")
            listing_result = await rpc.call("fork.routines.remove", {"profile": profile, "id": job_id})
            if routine_named(listing_result.get("routines", []), routine_name, job_id) is not None:
                raise RuntimeError(f"fork.routines.remove left the routine behind: {json.dumps(listing_result)[:300]}")
            return f"fork.routines.remove dropped job {job_id} from the routine list"

        await report.check("fork.routines.remove deletes the routine", routine_removed)

        async def routine_gone_from_cron() -> str:
            job_id = str(state.get("job_id") or "")
            if not job_id:
                raise RuntimeError("no routine id: the create step did not complete")
            output = cron_list(args.hermes_bin, args.hermes_home)
            if job_id in output:
                raise RuntimeError(f"`hermes cron list --all` still shows {job_id}: {output.strip()[:300]}")
            return f"job {job_id} is gone from `hermes cron list --all` too"

        await report.check("stock Hermes cron dropped the routine", routine_gone_from_cron)
    finally:
        await rpc.close()
        http.close()

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"base": base, "profile": profile, "results": report.results}, indent=2), encoding="utf-8")
    broken = report.broken
    blocked = sum(1 for row in report.results if row["status"] == "blocked")
    print(f"{len(report.results) - broken - blocked} checks passed, {blocked} blocked, {broken} broken", flush=True)
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
