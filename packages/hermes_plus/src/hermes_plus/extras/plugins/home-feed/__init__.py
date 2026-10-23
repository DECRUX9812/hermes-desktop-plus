"""Home Feed: the fork's Hark-style home surface as an add-on plugin — flat
action cards over what needs the user right now, plus the "Recent tasks"
one-job-per-screen session cards.

Ported from the fork's apps/desktop/src/store/home-feed.ts (collectHomeFeed:
priority-ranked, dismissible, capped at 5) and lib/today-brief.ts +
components/chat/today-brief.tsx (the tasks row: needs-you → running → finished,
topped up with the most recent chats, capped at 3). Every source is READ-ONLY:

- sessions come from the profile's own ``state.db``, opened through a
  ``mode=ro`` sqlite URI (never written, never locked longer than a read);
- cron jobs come from the profile's ``cron/jobs.json``, the file the scheduler
  already owns (the add-on's adapter/cron.py reads the same way);
- command-room escalations and group-rooms open handoffs come through those
  plugins' public APIs when they are registered in this process, and the
  corresponding cards simply vanish when they are not — the feed degrades to
  what it can see, never erroring.

All knobs come from the plugin's config_schema, read ONLY through
``ctx.get_config`` (the langfuse pattern): the ctx is captured at register
time, and ``ctx.get_config`` re-reads config.yaml on every call so edits apply
without a restart. Dismissed and pinned cards live in a plugin-owned sqlite
file, opened lazily on the first home-feed call.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes_plus.plugins.home-feed")

from .store import CardNotFound, FeedError, FeedStore

# Must mirror the defaults declared in plugin.yaml's config_schema (the manifest
# test pins the two together). ctx.get_config already resolves schema defaults
# when the host honours them; these keep the plugin correct on hosts that pass
# through unknown keys.
DEFAULTS: dict[str, Any] = {
    "max_cards": 5,
    "recent_tasks_limit": 3,
    "feed_window_hours": 24,
    "cron_due_window_minutes": 15,
    "include_command_room": True,
    "include_group_rooms": True,
    "db_path": "",
    "debug": False,
}

_PLUGIN_CTX: Any = None
_STORE: FeedStore | None = None
_STORE_LOCK = threading.Lock()


def _now() -> int:
    return int(time.time())


def _setting(name: str, default: Any = None) -> Any:
    """``plugins.entries.home-feed.settings.<name>`` through the ctx captured at
    register time, or ``default`` when register has not run (the API is inert anyway)."""
    if _PLUGIN_CTX is None:
        return default
    try:
        return _PLUGIN_CTX.get_config(name, default)
    except Exception:  # pragma: no cover - a broken host must not kill a feed call
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


def _db_path() -> Path:
    """Where the plugin's sqlite file lives: explicit db_path setting, then the host's
    data dir when the ctx provides one, then the profile home the ctx (or env) names."""
    override = str(_setting("db_path", "") or "").strip()
    if override:
        return Path(override)
    data_dir = getattr(_PLUGIN_CTX, "data_dir", None)
    if data_dir:
        return Path(data_dir) / "home-feed" / "home-feed.db"
    return _hermes_home() / "home-feed" / "home-feed.db"


def _store() -> FeedStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = FeedStore(_db_path())
        return _STORE


def close() -> None:
    """Close the sqlite store and drop the plugin's RPC methods (tests and host shutdown)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.close()
            _STORE = None
    try:
        from .rpc import unregister_rpc
    except Exception:
        pass
    else:
        unregister_rpc()


# ── source: sessions (read-only over the profile's state.db) ─────────────────────────────


def _open_sessions_db() -> sqlite3.Connection | None:
    """A read-only connection to the profile's state.db, or None when there is
    nothing to read (no home yet, no db, a store too new to parse). Never
    creates the file and never upgrades the schema; the feed degrades to empty."""
    path = _hermes_home() / "state.db"
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        logger.debug("home-feed: state.db at %s not readable; session cards degrade to empty", path)
        return None
    return conn


def _session_rows(limit: int) -> list[dict[str, Any]]:
    """The profile's live, non-empty sessions, most recent activity first.

    The fork's tasks row ranked needs-you → running → finished, all by recency;
    live dot states are renderer knowledge, so the plugin side supplies the
    recency-ordered candidates and the UI layers the live states on top.
    """
    conn = _open_sessions_db()
    if conn is None:
        return []
    try:
        try:
            rows = conn.execute(
                "SELECT id, title, started_at, ended_at, message_count, archived, "
                "last_activity_at, last_activity_description "
                "FROM sessions WHERE archived = 0 AND message_count > 0 "
                "ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        except sqlite3.Error:
            logger.debug("home-feed: state.db has no readable sessions table")
            return []
    finally:
        conn.close()
    return [
        {
            "id": row["id"],
            "title": str(row["title"] or ""),
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "last_at": row["last_activity_at"] or row["started_at"],
            "message_count": row["message_count"],
            "caption": str(row["last_activity_description"] or ""),
        }
        for row in rows
    ]


def recent_tasks(*, limit: int | None = None) -> dict[str, Any]:
    """The "Recent tasks" cards: one job per screen, always the latest few, so
    the row reads the same on a busy day and a quiet one (the fork's
    deriveTodayBrief().tasks). Needs-you ordering is the UI's job — the plugin
    hands back the recency-ordered candidates."""
    cap = int(limit if limit is not None else _knob("recent_tasks_limit"))
    return {"tasks": _session_rows(max(1, cap))}


# ── source: cron jobs (read-only over the scheduler's jobs.json) ─────────────────────────


def _cron_jobs() -> list[dict[str, Any]]:
    path = _hermes_home() / "cron" / "jobs.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("home-feed: %s unreadable; cron cards degrade to empty", path)
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return [job for job in jobs or [] if isinstance(job, dict)]


def _parse_ts(value: Any) -> float | None:
    """jobs.json stamps next_run_at as an ISO string; accept epoch numbers too."""
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime, timezone

        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _cron_cards(now: float) -> list[dict[str, Any]]:
    """Overdue and due-soon cron jobs as cards (the fork's cron pass, including
    its 15-minute dispatch grace: a slot parked in the past past the grace is
    the only user-visible trace of a dead scheduler, so it shows; a slot merely
    within grace waits quietly)."""
    grace = 15 * 60  # hermes_cli/cron.py's _OVERDUE_GRACE_SECONDS
    due_window = int(_knob("cron_due_window_minutes")) * 60
    cards = []
    for job in _cron_jobs():
        state = str(job.get("state") or "").strip()
        enabled = job.get("enabled")
        if enabled is False:
            continue
        if state in ("paused", "completed", "disabled"):
            continue
        at = _parse_ts(job.get("next_run_at"))
        if at is None:
            continue
        overdue = now - at
        is_overdue = overdue > grace
        if not is_overdue and not (0 <= at - now <= due_window):
            continue
        title = str(job.get("name") or "").strip() or str(job.get("prompt") or "").strip()[:60] \
            or str(job.get("id") or "Cron job")
        schedule = job.get("schedule") or {}
        cards.append({
            "id": f"cron:{job.get('id')}",
            "kind": "cronOverdue" if is_overdue else "cronDue",
            "title": title,
            "caption": str(job.get("schedule_display") or schedule.get("display") or schedule.get("expr")
                           or job.get("prompt") or ""),
            "session_id": None,
            "cron_job_id": job.get("id"),
            "created_at": int(at),
        })
    return cards


# ── source: command-room (read-only, optional) ───────────────────────────────────────────

_CR_MODULE_NAMES = ("command_room_plugin", "command_room_plugin_load")


def _command_room() -> Any | None:
    """The registered command-room plugin module, or None.

    Only a REGISTERED copy counts (its ``_PLUGIN_CTX`` set at register time):
    calling an unregistered copy would open its store at the default profile
    home, which the plugin must never do behind the host's back. Copies the
    host or the tests already loaded are found in sys.modules; this plugin does
    NOT load the sibling from disk (unlike command-room reaching group-rooms):
    a feed is a pure read of someone else's surface, and loading it just to
    read it would open its sqlite file as a side effect."""
    if not _knob("include_command_room"):
        return None
    for name in _CR_MODULE_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and getattr(mod, "_PLUGIN_CTX", None) is not None:
            return mod
    return None


def _escalation_cards(now: float) -> list[dict[str, Any]]:
    """Open, un-snoozed command-room escalations as needs-you cards, inside the
    feed window; the surface is empty when command-room is not registered."""
    plugin = _command_room()
    if plugin is None:
        return []
    window = now - int(_knob("feed_window_hours")) * 3_600
    try:
        escalations = plugin.escalation_list()
    except Exception:
        logger.debug("home-feed: command-room escalations unavailable")
        return []
    cards = []
    for esc in escalations:
        if esc.get("status") != "open":
            continue
        snoozed = esc.get("snoozed_until")
        if snoozed is not None and int(snoozed) > now:
            continue
        created = int(esc.get("created_at") or 0)
        if created < window:
            continue
        cards.append({
            "id": f"escalation:{esc.get('room')}:{esc.get('id')}",
            "kind": "approval",
            "title": str(esc.get("title") or "Escalation"),
            "caption": str(esc.get("detail") or esc.get("room") or ""),
            "session_id": None,
            "room": esc.get("room"),
            "created_at": created,
        })
    return cards


# ── source: group-rooms (read-only, optional) ────────────────────────────────────────────

_GR_MODULE_NAMES = ("group_rooms_plugin", "command_room_group_rooms")
_GR_SIBLING = Path(__file__).resolve().parent.parent / "group-rooms"


def _group_rooms() -> Any | None:
    """The registered group-rooms plugin module, or None. Same registered-copy
    rule as command-room's integration; the sibling directory loads once as a
    fallback, the way the tests load it."""
    if not _knob("include_group_rooms"):
        return None
    for name in _GR_MODULE_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and getattr(mod, "_PLUGIN_CTX", None) is not None:
            return mod
    try:
        spec = importlib.util.spec_from_file_location(
            _GR_MODULE_NAMES[1], _GR_SIBLING / "__init__.py",
            submodule_search_locations=[str(_GR_SIBLING)],
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    except Exception:
        logger.debug("home-feed: group-rooms plugin not loadable; running without room cards")
        return None
    if getattr(mod, "_PLUGIN_CTX", None) is None:
        return None
    return mod


def _handoff_cards(now: float) -> list[dict[str, Any]]:
    """Group-rooms threads carrying an open handoff as cards — work sitting with
    the team while the user is elsewhere. Empty when group-rooms is not registered."""
    plugin = _group_rooms()
    if plugin is None:
        return []
    window = now - int(_knob("feed_window_hours")) * 3_600
    try:
        rooms = plugin.list_rooms()
    except Exception:
        logger.debug("home-feed: group-rooms rooms unavailable")
        return []
    cards = []
    for room_row in rooms:
        room = str(room_row.get("name") or "")
        if not room:
            continue
        try:
            state = plugin.room_state(room)
        except Exception:
            logger.debug("home-feed: group-rooms room %r unavailable", room)
            continue
        open_by_thread: dict[Any, dict[str, Any]] = {}
        for handoff in state.get("handoffs") or []:
            if handoff.get("open"):
                open_by_thread[int(handoff.get("thread_id") or 0)] = handoff
        threads = {int(t.get("id") or 0): t for t in state.get("threads") or []}
        for thread_id, handoff in open_by_thread.items():
            thread = threads.get(thread_id) or {}
            created = int(handoff.get("created_at") or 0)
            if created < window:
                continue
            cards.append({
                "id": f"handoff:{room}:{handoff.get('id')}",
                "kind": "handoff",
                "title": str(thread.get("title") or f"Handoff in {room}"),
                "caption": f"{handoff.get('from_bot')} → {handoff.get('to_bot')}"
                           + (f": {handoff.get('note')}" if handoff.get("note") else ""),
                "session_id": None,
                "room": room,
                "created_at": created,
            })
    return cards


# ── the feed ─────────────────────────────────────────────────────────────────────────────

# Priority rank for feed items, the fork's ladder with the plugin-sourced kinds
# slotted beside the ones they stand in for: an escalation or a prompt ranks
# first (1–2), an error or a blocked handoff next (3–4), then the cron tiers
# (5–6), then everything else. The stable sort keeps insertion order inside a tier.
def priority_rank(kind: str) -> int:
    return {
        "approval": 1,
        "prompt": 2,
        "error": 3,
        "handoff": 4,
        "cronOverdue": 5,
        "cronDue": 6,
        "prReview": 7,
    }.get(kind, 8)


def feed(*, limit: int | None = None) -> dict[str, Any]:
    """The home feed: every source's cards, dismissed ones dropped, pinned ones
    floated (oldest pin first, then the priority ladder), capped at max_cards —
    the fork's collectHomeFeed with the plugin as the collector."""
    now = float(_now())
    cap = int(limit if limit is not None else _knob("max_cards"))
    dismissed = set(_store().dismissed_ids())
    pinned_ids = _store().pinned_ids()

    items = _escalation_cards(now) + _handoff_cards(now) + _cron_cards(now)
    items = [card for card in items if card["id"] not in dismissed]
    for card in items:
        card["pinned"] = card["id"] in pinned_ids
    items.sort(key=lambda card: (not card["pinned"],
                                 pinned_ids.index(card["id"]) if card["pinned"] else 0,
                                 priority_rank(card["kind"]), -int(card.get("created_at") or 0)))
    return {"cards": items[:max(1, cap)], "total": len(items), "dismissed": sorted(dismissed)}


def dismiss(card_id: str) -> dict[str, Any]:
    return _store().dismiss(card_id)


def restore(card_id: str) -> dict[str, Any]:
    if not _store().restore(card_id):
        raise CardNotFound(f"card {card_id!r} is not dismissed")
    return {"id": card_id, "dismissed": False}


def pin_add(card_id: str, *, title: str = "") -> dict[str, Any]:
    return _store().pin_add(card_id, title=title)


def pin_remove(card_id: str) -> dict[str, Any]:
    if not _store().pin_remove(card_id):
        raise CardNotFound(f"card {card_id!r} is not pinned")
    return {"id": card_id, "pinned": False}


def pins() -> list[dict[str, Any]]:
    return _store().pins()


# ── config echo (the UI gate) ────────────────────────────────────────────────────────────


def config() -> dict[str, Any]:
    """The effective UI-relevant knobs."""
    return {
        "max_cards": int(_knob("max_cards")),
        "recent_tasks_limit": int(_knob("recent_tasks_limit")),
        "feed_window_hours": int(_knob("feed_window_hours")),
        "include_command_room": bool(_knob("include_command_room")),
        "include_group_rooms": bool(_knob("include_group_rooms")),
    }


def register(ctx) -> None:
    global _PLUGIN_CTX, _STORE
    _PLUGIN_CTX = ctx
    # A fresh registration re-points the store: the previous ctx's db must not leak
    # into the new home (matters for tests and for host re-registration).
    if _STORE is not None:
        _STORE.close()
        _STORE = None
    # The RPC surface registers through the sidecar registry's sanctioned plugin
    # hook, here where every other feature wires up. Lazy import + guard: a host
    # without the sidecar package still loads the feed API, just with no RPC door.
    try:
        from .rpc import register_rpc
    except Exception:
        logger.debug("home-feed: rpc module unavailable; RPC surface not registered")
    else:
        try:
            register_rpc()
        except Exception:
            logger.exception("home-feed: RPC registration failed; the feed API continues without it")
    if _knob("debug"):
        logger.debug("home-feed registered; db resolves to %s", _db_path())


__all__ = [
    "DEFAULTS", "CardNotFound", "FeedError", "FeedStore",
    "close", "config", "dismiss", "feed", "pin_add", "pin_remove", "pins",
    "priority_rank", "recent_tasks", "register", "restore",
]
