"""Activity: the fork's Muse-style Activity pane as an add-on plugin — recent
activity rows over what every bot is doing and has done, plus the
Now/Next/Done bot rail.

Ported from the fork's apps/desktop/src/app/activity/ (activity-tab.tsx: recent
sessions + desktop action rows, newest first; the fix commit 3f8c0538 that pins
the timestamp contract) with the mission rail's Now/Next/Done board sections
(plugins/hermes-bots/work-board.tsx) as the rail. Every source is READ-ONLY:

- sessions come from the profile's own ``state.db``, opened through a
  ``mode=ro`` sqlite URI (never written, never locked longer than a read);
- cron jobs come from the profile's ``cron/jobs.json``, the file the scheduler
  already owns (the add-on's adapter/cron.py reads the same way);
- command-room escalations and group-rooms open handoffs come through those
  plugins' public APIs when they are registered in this process, and the
  corresponding rows simply vanish when they are not — the pane degrades to
  what it can see, never erroring.

TIMESTAMP CONTRACT: every row's ``last_active`` is an integer UNIX SECONDS
value. Source stamps arrive in mixed units, so each one is normalized here —
a value past 1e12 is milliseconds and is converted down to seconds; 0, None
and unparseable values are rejected and the row is omitted (never epoch 0).
The UI converts seconds to milliseconds exactly once (``x * 1000``) at the
relative-time formatting boundary; formatting a seconds value as milliseconds
is the fork's "20,728 days ago" bug.

All knobs come from the plugin's config_schema, read ONLY through
``ctx.get_config`` (the langfuse pattern): the ctx is captured at register
time, and ``ctx.get_config`` re-reads config.yaml on every call so edits apply
without a restart. The pane owns no state, so there is no plugin store —
pure reads over the sources above.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes_plus.plugins.activity")

# Must mirror the defaults declared in plugin.yaml's config_schema (the manifest
# test pins the two together). ctx.get_config already resolves schema defaults
# when the host honours them; these keep the plugin correct on hosts that pass
# through unknown keys.
DEFAULTS: dict[str, Any] = {
    "window_hours": 24,
    "max_rows": 20,
    "now_next_done_enabled": True,
    "now_window_minutes": 10,
    "include_cron": True,
    "include_command_room": True,
    "include_group_rooms": True,
    "debug": False,
}

_PLUGIN_CTX: Any = None


def _now() -> int:
    return int(time.time())


def _setting(name: str, default: Any = None) -> Any:
    """``plugins.entries.activity.settings.<name>`` through the ctx captured at
    register time, or ``default`` when register has not run (the API is inert anyway)."""
    if _PLUGIN_CTX is None:
        return default
    try:
        return _PLUGIN_CTX.get_config(name, default)
    except Exception:  # pragma: no cover - a broken host must not kill an activity call
        return default


def _knob(name: str) -> Any:
    value = _setting(name, DEFAULTS[name])
    return DEFAULTS[name] if value is None else value


def _hermes_home() -> Path:
    """The profile home the ctx (or env) names — the only place sources resolve from."""
    home = (
        getattr(_PLUGIN_CTX, "hermes_home", None)
        or os.environ.get("HERMES_HOME")
        or str(Path.home() / ".hermes")
    )
    return Path(home)


# ── timestamps: normalize every source value to integer UNIX SECONDS ─────────────────────

# Epoch milliseconds exceed this until the year ~33k; epoch seconds do not.
_MS_THRESHOLD = 1e12


def _to_seconds(value: Any) -> int | None:
    """One source stamp → integer UNIX SECONDS, or None when there is no honest
    time in it. Millisecond stamps (>1e12) convert down to seconds; 0, negative,
    None and unparseable values are rejected — a row with no real time is
    omitted rather than shown as epoch 0 (the fork's "20,728 days ago" class of
    bug is fixed at the source, not at the formatter)."""
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        try:
            from datetime import datetime, timezone

            ts = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
        except ValueError:
            return None
    if ts > _MS_THRESHOLD:
        ts = ts / 1000.0
    if ts <= 0:
        return None
    return int(ts)


# ── source: sessions (read-only over the profile's state.db) ─────────────────────────────


def _open_sessions_db() -> sqlite3.Connection | None:
    """A read-only connection to the profile's state.db, or None when there is
    nothing to read (no home yet, no db, a store too new to parse). Never
    creates the file and never upgrades the schema; the pane degrades to empty."""
    path = _hermes_home() / "state.db"
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        logger.debug("activity: state.db at %s not readable; session rows degrade to empty", path)
        return None
    return conn


def _session_rows(now: int) -> list[dict[str, Any]]:
    """The profile's live, non-empty sessions as activity rows, each with a
    normalized seconds ``last_active``; sessions with no honest timestamp are
    omitted, not shown as epoch 0. A session still active inside the Now window
    is running; everything else (idle or ended) is a finished receipt."""
    conn = _open_sessions_db()
    if conn is None:
        return []
    now_window = now - int(_knob("now_window_minutes")) * 60
    try:
        try:
            rows = conn.execute(
                "SELECT id, title, started_at, ended_at, message_count, archived, "
                "last_activity_at, last_activity_description "
                "FROM sessions WHERE archived = 0 AND message_count > 0 "
                "ORDER BY COALESCE(last_activity_at, started_at) DESC",
            ).fetchall()
        except sqlite3.Error:
            logger.debug("activity: state.db has no readable sessions table")
            return []
    finally:
        conn.close()
    out = []
    for row in rows:
        last_active = _to_seconds(row["last_activity_at"]) or _to_seconds(row["started_at"])
        if last_active is None:
            continue  # no honest time in either stamp — omit, never epoch 0
        ended = _to_seconds(row["ended_at"])
        status = "success" if ended is not None or last_active < now_window else "running"
        out.append({
            "id": f"session:{row['id']}",
            "kind": "session",
            "label": str(row["title"] or "").strip() or str(row["id"]),
            "detail": str(row["last_activity_description"] or "").strip()
                      or ("Conversation" if status == "running" else "Conversation (ended)"),
            "status": status,
            "session_id": row["id"],
            "last_active": last_active,
            "created_at": _to_seconds(row["started_at"]),
        })
    return out


# ── source: cron jobs (read-only over the scheduler's jobs.json) ─────────────────────────


def _cron_jobs() -> list[dict[str, Any]]:
    path = _hermes_home() / "cron" / "jobs.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("activity: %s unreadable; cron rows degrade to empty", path)
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return [job for job in jobs or [] if isinstance(job, dict)]


def _cron_title(job: dict[str, Any]) -> str:
    return str(job.get("name") or "").strip() or str(job.get("prompt") or "").strip()[:60] \
        or str(job.get("id") or "Cron job")


def _cron_row(job: dict[str, Any], at: int, *, overdue: bool) -> dict[str, Any]:
    schedule = job.get("schedule") or {}
    return {
        "id": f"cron:{job.get('id')}",
        "kind": "cron",
        "label": _cron_title(job),
        "detail": str(job.get("schedule_display") or schedule.get("display") or schedule.get("expr")
                      or job.get("prompt") or ""),
        "status": "running" if overdue else "success",
        "session_id": None,
        "last_active": at,
        "created_at": _to_seconds(job.get("created_at")),
    }


def _cron_rows(now: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cron jobs as (overdue rows, upcoming Next entries) — a read of the
    scheduler's jobs.json. An overdue slot (past the 15-minute dispatch grace,
    hermes_cli/cron.py's _OVERDUE_GRACE_SECONDS) is running-late work: a Now
    row. A slot merely within grace waits quietly. An upcoming run inside the
    window is a Next entry, soonest first — its ``last_active`` carries the
    scheduled time and may lie in the future by design."""
    grace = 15 * 60
    horizon = now + int(_knob("window_hours")) * 3_600
    overdue_rows: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    for job in _cron_jobs():
        state = str(job.get("state") or "").strip()
        enabled = job.get("enabled")
        if enabled is False:
            continue
        if state in ("paused", "completed", "disabled"):
            continue
        at = _to_seconds(job.get("next_run_at"))
        if at is None:
            continue
        if at < now - grace:
            overdue_rows.append(_cron_row(job, at, overdue=True))
        elif at <= horizon:
            upcoming.append(_cron_row(job, at, overdue=False))
    return overdue_rows, upcoming


# ── source: command-room (read-only, optional) ───────────────────────────────────────────

_CR_MODULE_NAMES = ("command_room_plugin", "command_room_plugin_load")


def _command_room() -> Any | None:
    """The registered command-room plugin module, or None.

    Only a REGISTERED copy counts (its ``_PLUGIN_CTX`` set at register time):
    calling an unregistered copy would open its store at the default profile
    home, which the plugin must never do behind the host's back. Copies the
    host or the tests already loaded are found in sys.modules; this plugin does
    NOT load the sibling from disk (unlike command-room reaching group-rooms):
    activity is a pure read of someone else's surface, and loading it just to
    read it would open its sqlite file as a side effect."""
    if not _knob("include_command_room"):
        return None
    for name in _CR_MODULE_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and getattr(mod, "_PLUGIN_CTX", None) is not None:
            return mod
    return None


def _escalation_rows(now: int) -> list[dict[str, Any]]:
    """Open, un-snoozed command-room escalations as Now rows inside the window;
    the surface is empty when command-room is not registered."""
    plugin = _command_room()
    if plugin is None:
        return []
    window = now - int(_knob("window_hours")) * 3_600
    try:
        escalations = plugin.escalation_list()
    except Exception:
        logger.debug("activity: command-room escalations unavailable")
        return []
    rows = []
    for esc in escalations:
        if esc.get("status") != "open":
            continue
        snoozed = esc.get("snoozed_until")
        if snoozed is not None and int(snoozed) > now:
            continue
        created = _to_seconds(esc.get("created_at"))
        if created is None or created < window:
            continue
        rows.append({
            "id": f"escalation:{esc.get('room')}:{esc.get('id')}",
            "kind": "approval",
            "label": str(esc.get("title") or "Escalation"),
            "detail": str(esc.get("detail") or esc.get("room") or ""),
            "status": "running",
            "session_id": None,
            "last_active": created,
            "created_at": created,
        })
    return rows


# ── source: group-rooms (read-only, optional) ────────────────────────────────────────────

_GR_MODULE_NAMES = ("group_rooms_plugin", "command_room_group_rooms")


def _group_rooms() -> Any | None:
    """The registered group-rooms plugin module, or None. Same registered-copy
    rule as command-room's integration; the sibling directory is NOT loaded
    from disk — a pane is a pure read, and loading a store owner just to read
    it would open its sqlite file behind the host's back."""
    if not _knob("include_group_rooms"):
        return None
    for name in _GR_MODULE_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and getattr(mod, "_PLUGIN_CTX", None) is not None:
            return mod
    return None


def _handoff_rows(now: int) -> list[dict[str, Any]]:
    """Group-rooms threads carrying an open handoff as Now rows — work sitting
    with the team while the user is elsewhere. Empty when group-rooms is not
    registered."""
    plugin = _group_rooms()
    if plugin is None:
        return []
    window = now - int(_knob("window_hours")) * 3_600
    try:
        rooms = plugin.list_rooms()
    except Exception:
        logger.debug("activity: group-rooms rooms unavailable")
        return []
    rows = []
    for room_row in rooms:
        room = str(room_row.get("name") or "")
        if not room:
            continue
        try:
            state = plugin.room_state(room)
        except Exception:
            logger.debug("activity: group-rooms room %r unavailable", room)
            continue
        open_by_thread: dict[Any, dict[str, Any]] = {}
        for handoff in state.get("handoffs") or []:
            if handoff.get("open"):
                open_by_thread[int(handoff.get("thread_id") or 0)] = handoff
        threads = {int(t.get("id") or 0): t for t in state.get("threads") or []}
        for thread_id, handoff in open_by_thread.items():
            thread = threads.get(thread_id) or {}
            created = _to_seconds(handoff.get("created_at"))
            if created is None or created < window:
                continue
            rows.append({
                "id": f"handoff:{room}:{handoff.get('id')}",
                "kind": "handoff",
                "label": str(thread.get("title") or f"Handoff in {room}"),
                "detail": f"{handoff.get('from_bot')} → {handoff.get('to_bot')}"
                          + (f": {handoff.get('note')}" if handoff.get("note") else ""),
                "status": "running",
                "session_id": None,
                "last_active": created,
                "created_at": created,
            })
    return rows


# ── the activity surface ─────────────────────────────────────────────────────────────────


def _collect(now: int) -> list[dict[str, Any]]:
    """Every source's rows (cron overdue included when include_cron), each with
    a normalized seconds ``last_active``."""
    window = now - int(_knob("window_hours")) * 3_600
    rows = list(_session_rows(now)) + _escalation_rows(now) + _handoff_rows(now)
    if _knob("include_cron"):
        overdue, _upcoming = _cron_rows(now)
        rows += overdue
    # The window filter runs AFTER normalization: a row with no honest
    # timestamp was already dropped, and a stale one ages out here.
    return [row for row in rows if row["last_active"] >= window]


def rows(*, limit: int | None = None) -> dict[str, Any]:
    """The activity rows, newest first, capped at max_rows — the fork's
    activity-tab list (no invented activity, ever)."""
    cap = int(limit if limit is not None else _knob("max_rows"))
    items = sorted(_collect(_now()), key=lambda row: -int(row["last_active"]))
    return {"rows": items[:max(1, cap)], "total": len(items)}


def rail() -> dict[str, Any]:
    """The Now/Next/Done bot rail. Now: running work and things waiting on you
    (running sessions, escalations, open handoffs, overdue cron). Next: the
    scheduler's upcoming runs inside the window, soonest first. Done: finished
    conversations as receipts. Buckets are each capped at max_rows; when the
    rail is switched off the buckets come back empty and the UI hides it."""
    now = _now()
    if not _knob("now_next_done_enabled"):
        return {"enabled": False, "now": [], "next": [], "done": []}
    cap = int(_knob("max_rows"))
    items = _collect(now)
    now_rows = sorted((row for row in items if row["status"] == "running"),
                      key=lambda row: -int(row["last_active"]))
    done_rows = sorted((row for row in items if row["status"] == "success"),
                       key=lambda row: -int(row["last_active"]))
    next_rows: list[dict[str, Any]] = []
    if _knob("include_cron"):
        _overdue, upcoming = _cron_rows(now)
        next_rows = sorted(upcoming, key=lambda row: int(row["last_active"]))
    return {
        "enabled": True,
        "now": now_rows[:cap],
        "next": next_rows[:cap],
        "done": done_rows[:cap],
    }


# ── config echo (the UI gate) ────────────────────────────────────────────────────────────


def config() -> dict[str, Any]:
    """The effective UI-relevant knobs."""
    return {
        "window_hours": int(_knob("window_hours")),
        "max_rows": int(_knob("max_rows")),
        "now_next_done_enabled": bool(_knob("now_next_done_enabled")),
        "now_window_minutes": int(_knob("now_window_minutes")),
        "include_cron": bool(_knob("include_cron")),
        "include_command_room": bool(_knob("include_command_room")),
        "include_group_rooms": bool(_knob("include_group_rooms")),
    }


def register(ctx) -> None:
    global _PLUGIN_CTX
    _PLUGIN_CTX = ctx
    # The RPC surface registers through the sidecar registry's sanctioned plugin
    # hook, here where every other feature wires up. Lazy import + guard: a host
    # without the sidecar package still loads the activity API, just with no RPC door.
    try:
        from .rpc import register_rpc
    except Exception:
        logger.debug("activity: rpc module unavailable; RPC surface not registered")
    else:
        try:
            register_rpc()
        except Exception:
            logger.exception("activity: RPC registration failed; the pane API continues without it")
    if _knob("debug"):
        logger.debug("activity registered; sources resolve under %s", _hermes_home())


def close() -> None:
    """Drop the plugin's RPC methods (tests and host shutdown). No store to close:
    the pane owns no state."""
    try:
        from .rpc import unregister_rpc
    except Exception:
        pass
    else:
        unregister_rpc()


__all__ = [
    "DEFAULTS", "_to_seconds",
    "close", "config", "rail", "register", "rows",
]
