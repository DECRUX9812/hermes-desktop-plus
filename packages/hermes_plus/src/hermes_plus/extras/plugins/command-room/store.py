"""The command-room model: escalations, the CEO board's inputs, pins, snoozes,
consensus votes, seat budgets with alerts, and moved-thread read marks.

Ported from the fork's hermes-bots command room (group-room-handoffs.ts /
group-room-votes.ts / group-room-board.tsx / group-room-catchup.tsx): the CEO
(the user) sees what needs them, what the team is on, and what settled. Rooms
and threads themselves are NOT duplicated here — the plugin reads them from the
group-rooms plugin's public API when that plugin is registered in-process, and
this store only owns command-room's own state in its own sqlite file.

This module is deliberately config-free: knobs (thresholds, quorum, budgets,
the alert gate) are read at call time by the plugin layer in ``__init__.py``
through the captured plugin ctx and passed in as plain arguments, so the store
stays testable and host-agnostic.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

ESCALATION_KINDS = ("stalled", "loop")
ESCALATION_STATUSES = ("open", "acked")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room TEXT NOT NULL,
    thread_id INTEGER,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    snoozed_until INTEGER,
    ack_note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    acked_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS escalations_source
    ON escalations(source) WHERE source != '';
CREATE TABLE IF NOT EXISTS pins (
    room TEXT NOT NULL,
    thread_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    PRIMARY KEY (room, thread_id)
);
CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room TEXT NOT NULL,
    question TEXT NOT NULL,
    options TEXT NOT NULL,
    quorum INTEGER NOT NULL,
    asked TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '',
    message_id INTEGER,
    thread_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    decided TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS ballots (
    vote_id INTEGER NOT NULL REFERENCES votes(id) ON DELETE CASCADE,
    voter TEXT NOT NULL,
    option TEXT NOT NULL,
    cast_at INTEGER NOT NULL,
    PRIMARY KEY (vote_id, voter)
);
CREATE TABLE IF NOT EXISTS budgets (
    room TEXT NOT NULL,
    seat TEXT NOT NULL,
    budget INTEGER NOT NULL,
    PRIMARY KEY (room, seat)
);
CREATE TABLE IF NOT EXISTS spends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room TEXT NOT NULL,
    seat TEXT NOT NULL,
    amount INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room TEXT NOT NULL,
    kind TEXT NOT NULL,
    seat TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_seen (
    room TEXT NOT NULL,
    thread_id INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    PRIMARY KEY (room, thread_id)
);
"""


# ── the room's decision protocol (ported from the fork's group-room-votes.ts) ───
# A decision is one line anyone writes in the room:
#
#     @everyone decide: ship the rewrite or keep the old one? · options: ship / keep
#
# Members answer in that thread — either the protocol word ("vote: ship") or
# plain words naming an option — and the user records the outcome with a
# "decided: <option>" line (the card's Call it button, or the same words by
# hand). Protocol words are never translated.

DECIDE = re.compile(r"(?:^|\s)@everyone\s+decide\s*:\s*(.+)", re.IGNORECASE)
FIELD_SPLIT = re.compile(r"\s*[·|;]\s*")
OPTIONS_FIELD = re.compile(r"^options?\s*:\s*", re.IGNORECASE)
VOTE = re.compile(r"\b(?:vote|pick|voting)\s*:\s*(.+)$", re.IGNORECASE)
# The user recording the outcome, in their own words or the card's.
DECIDED = re.compile(r"(?:^|\s)(?:@everyone\s+)?decide[sd]?\s*:\s*(.+)", re.IGNORECASE)
PASS_TEXT = re.compile(r"^\(?\s*pass\s*\)?\.?$", re.IGNORECASE)


def split_options(text: str) -> list[str]:
    """Option labels split on " / ", " | " or " · " — the template's own shapes."""
    return [part.strip() for part in re.split(r"\s*(?:/|\||·)\s*", str(text or "")) if part.strip()]


def names_option(text: str, option: str) -> bool:
    """An option label matched as a word."""
    if not option:
        return False
    return re.search(rf"(^|[^\w]){re.escape(option)}([^\w]|$)", str(text or ""), re.IGNORECASE) is not None


def parse_decision_line(text: str) -> dict[str, Any] | None:
    """One "decide:" line → its question and options, or None when it isn't one."""
    for line in str(text or "").split("\n"):
        match = DECIDE.search(line)
        if not match:
            continue
        parts = [part.strip() for part in FIELD_SPLIT.split(match.group(1))]
        options_part = next((part for part in parts if OPTIONS_FIELD.match(part)), None)
        question = " · ".join(part for part in parts if not OPTIONS_FIELD.match(part)).strip()
        options = split_options(OPTIONS_FIELD.sub("", options_part, count=1)) if options_part else []
        if question:
            return {"question": question, "options": options}
    return None


def pick_of(text: str, options: list[str]) -> str:
    """The option a reply picks, or '' when it names none.

    Longest label first: "ship now" must not lose to its own prefix."""
    match = VOTE.search(str(text or ""))
    said = match.group(1).strip() if match else str(text or "").strip()
    for option in sorted((str(option) for option in options), key=len, reverse=True):
        if names_option(said, option):
            return option
    return ""


def is_pass_text(text: str) -> bool:
    '''"(pass)" never counts as a pick; neither does silence.'''
    trimmed = str(text or "").strip()
    return not trimmed or PASS_TEXT.match(trimmed) is not None


def decide_line(question: str, options: list[str]) -> str:
    """A decision line in the exact shape parse_decision_line reads back."""
    return f"@everyone decide: {question} · options: {' / '.join(options)}"


def decided_line(option: str) -> str:
    """Recording the outcome — the card's Call it button, and the words that
    read the same when someone types them by hand."""
    return f"decided: {option}"


def vote_line(option: str) -> str:
    return f"vote: {option}"


class CommandError(Exception):
    """Caller-visible command-room failure (bad kind, unknown vote, overspend, ...)."""


class CommandNotFound(CommandError):
    pass


class BudgetExhausted(CommandError):
    """A spend went past the seat's budget; the spend was NOT recorded."""


def _now() -> int:
    return int(time.time())


class CommandStore:
    """One sqlite file of command-room state; safe to share across threads, not processes."""

    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        # Databases written before the asked roster / room anchor existed carry on:
        # the new columns are additive, defaults match a vote with no roster.
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(votes)")}
        for column, decl in (
            ("asked", "TEXT NOT NULL DEFAULT '[]'"),
            ("source", "TEXT NOT NULL DEFAULT ''"),
            ("message_id", "INTEGER"),
            ("thread_id", "INTEGER"),
        ):
            if column not in columns:
                self._conn.execute(f"ALTER TABLE votes ADD COLUMN {column} {decl}")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── escalations ──────────────────────────────────────────────────────────────────────

    def escalation_create(
        self, room: str, kind: str, title: str, *, detail: str = "", thread_id: int | None = None,
        source: str = "",
    ) -> dict[str, Any]:
        if kind not in ESCALATION_KINDS:
            raise CommandError(f"unknown escalation kind {kind!r}; expected one of {', '.join(ESCALATION_KINDS)}")
        title = str(title or "").strip()
        if not title:
            raise CommandError("escalation title must not be empty")
        with self._lock, self._conn:
            if source:
                existing = self._conn.execute(
                    "SELECT * FROM escalations WHERE source = ?", (source,)).fetchone()
                if existing is not None:
                    return self._escalation_dict(existing)
            cursor = self._conn.execute(
                "INSERT INTO escalations (room, thread_id, kind, title, detail, source, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(room), thread_id, kind, title, str(detail or ""), source, _now()),
            )
            escalation_id = cursor.lastrowid
        return self.escalation(room, escalation_id)

    def escalation(self, room: str, escalation_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._escalation_row(self._conn, room, int(escalation_id))
        return self._escalation_dict(row)

    def _escalation_row(self, conn: sqlite3.Connection, room: str, escalation_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM escalations WHERE id = ? AND room = ?", (escalation_id, str(room))).fetchone()
        if row is None:
            raise CommandNotFound(f"no escalation {escalation_id} in {room!r}")
        return row

    def escalation_ack(self, room: str, escalation_id: int, *, note: str = "") -> dict[str, Any]:
        with self._lock, self._conn:
            self._escalation_row(self._conn, room, int(escalation_id))
            self._conn.execute(
                "UPDATE escalations SET status = 'acked', acked_at = ?, ack_note = ? WHERE id = ?",
                (_now(), str(note or ""), int(escalation_id)),
            )
        return self.escalation(room, escalation_id)

    def escalation_snooze(self, room: str, escalation_id: int, minutes: int) -> dict[str, Any]:
        minutes = int(minutes)
        if minutes <= 0:
            raise CommandError("snooze minutes must be positive")
        with self._lock, self._conn:
            self._escalation_row(self._conn, room, int(escalation_id))
            self._conn.execute(
                "UPDATE escalations SET snoozed_until = ? WHERE id = ?",
                (_now() + minutes * 60, int(escalation_id)),
            )
        return self.escalation(room, escalation_id)

    def escalations(self, room: str | None = None, *, include_acked: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM escalations WHERE (? IS NULL OR room = ?) ORDER BY id DESC",
                (room, room),
            ).fetchall()
        out = [self._escalation_dict(row) for row in rows]
        return [row for row in out if include_acked or row["status"] == "open"]

    def sync_handoffs(
        self, room: str, handoffs: list[dict[str, Any]], *, stalled_after_seconds: int, loop_count: int
    ) -> list[dict[str, Any]]:
        """Derive escalations from group-rooms handoff rows (the fork's
        roomEscalations): an open handoff older than the threshold is `stalled`,
        and LOOP_COUNT open handoffs between the same seat pair in one thread is
        a `loop`. Synced escalations dedup on their source, so a re-sync never
        duplicates; resolving the underlying handoff leaves the record for the
        CEO to ack (a disappeared alarm is how problems get missed)."""
        now = _now()
        out: list[dict[str, Any]] = []
        for handoff in handoffs:
            if not handoff.get("open"):
                continue
            age = now - int(handoff.get("created_at") or 0)
            if age >= stalled_after_seconds:
                esc = self.escalation_create(
                    str(room), "stalled",
                    handoff.get("note") or f"handoff to {handoff.get('to_bot')} stalled",
                    detail=f"{handoff.get('from_bot')} → {handoff.get('to_bot')} open {age // 60}m",
                    thread_id=handoff.get("thread_id"),
                    source=f"group-rooms:handoff:{handoff.get('id')}",
                )
                out.append(esc)
        loops: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for handoff in handoffs:
            if not handoff.get("open"):
                continue
            pair = "↔".join(sorted([str(handoff.get("from_bot")), str(handoff.get("to_bot"))]))
            loops.setdefault((int(handoff.get("thread_id") or 0), pair), []).append(handoff)
        for (thread_id, pair), run in loops.items():
            if len(run) < loop_count:
                continue
            esc = self.escalation_create(
                str(room), "loop", f"handoff loop {pair} in thread {thread_id}",
                detail=f"{len(run)} open handoffs between {pair}, none delivered",
                thread_id=thread_id,
                source=f"group-rooms:loop:{thread_id}:{pair}",
            )
            out.append(esc)
        return out

    # ── pins ─────────────────────────────────────────────────────────────────────────────

    def pin_add(self, room: str, thread_id: int, *, title: str = "") -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO pins (room, thread_id, title, created_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(room, thread_id) DO UPDATE SET title = excluded.title",
                (str(room), int(thread_id), str(title or ""), _now()),
            )
        return {"room": str(room), "thread_id": int(thread_id), "pinned": True, "title": str(title or "")}

    def pin_remove(self, room: str, thread_id: int) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM pins WHERE room = ? AND thread_id = ?", (str(room), int(thread_id)))
            return cursor.rowcount > 0

    def pins(self, room: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pins WHERE room = ? ORDER BY created_at DESC, thread_id DESC", (str(room),)
            ).fetchall()
        return [dict(row) for row in rows]

    # ── consensus votes ──────────────────────────────────────────────────────────────────

    def vote_create(
        self, room: str, question: str, options: list[str], *, quorum: int, asked: list[str] | None = None,
        source: str = "", message_id: int | None = None, thread_id: int | None = None,
    ) -> dict[str, Any]:
        """Open a vote. ``asked`` is the roster the consensus is measured
        against (the room's seats, or whoever was explicitly asked); ``source``
        dedups a vote derived from a room-log line, and ``message_id`` anchors
        the card under the message that asked."""
        question = str(question or "").strip()
        if not question:
            raise CommandError("vote question must not be empty")
        cleaned = [str(option or "").strip() for option in options]
        options = [option for option in cleaned if option]
        if len(set(options)) != len(options):
            raise CommandError("vote options must be distinct")
        if len(options) < 2:
            raise CommandError("a vote needs at least two options")
        quorum = int(quorum)
        if quorum < 1:
            raise CommandError("quorum must be at least 1")
        roster = [str(name) for name in (asked or []) if str(name)]
        deduped = False
        with self._lock, self._conn:
            if source:
                existing = self._conn.execute(
                    "SELECT * FROM votes WHERE source = ?", (str(source),)).fetchone()
                if existing is not None:
                    vote_id = int(existing["id"])
                    deduped = True
            if not deduped:
                cursor = self._conn.execute(
                    "INSERT INTO votes (room, question, options, quorum, asked, source, message_id,"
                    " thread_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(room), question, json.dumps(options), quorum, json.dumps(roster), str(source or ""),
                     message_id, thread_id, _now()),
                )
                vote_id = cursor.lastrowid
        return self.vote_tally(vote_id)

    def _vote_row(self, conn: sqlite3.Connection, room: str, vote_id: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM votes WHERE id = ?", (int(vote_id),)).fetchone()
        if row is None or (room is not None and row["room"] != str(room)):
            raise CommandNotFound(f"no vote {vote_id} in {room!r}")
        return row

    def vote_cast(self, room: str, vote_id: int, voter: str, option: str) -> dict[str, Any]:
        voter = str(voter or "").strip()
        option = str(option or "").strip()
        if not voter:
            raise CommandError("voter must not be empty")
        with self._lock, self._conn:
            row = self._vote_row(self._conn, room, vote_id)
            if row["status"] != "open":
                raise CommandError(f"vote {vote_id} is already decided")
            options = json.loads(row["options"])
            if option not in options:
                raise CommandError(f"option {option!r} is not on this ballot; expected one of {', '.join(options)}")
            # Last pick wins, like the fork's room tally: a member may change
            # their mind until the vote settles.
            self._conn.execute(
                "INSERT INTO ballots (vote_id, voter, option, cast_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(vote_id, voter) DO UPDATE SET option = excluded.option, cast_at = excluded.cast_at",
                (int(vote_id), voter, option, _now()),
            )
        return self.vote_tally(vote_id, room=room)

    def vote_call(self, room: str, vote_id: int, option: str) -> dict[str, Any]:
        """The owner's call — the ONLY way a vote settles. Unanimity lights the
        chip; it never decides. (The room-log write-back of the `decided:` line
        lives in the plugin layer, so a hand-typed line doesn't echo twice.)"""
        option = str(option or "").strip()
        with self._lock, self._conn:
            row = self._vote_row(self._conn, room, vote_id)
            if row["status"] != "open":
                raise CommandError(f"vote {vote_id} is already decided")
            options = json.loads(row["options"])
            if option not in options:
                raise CommandError(f"option {option!r} is not on this ballot; expected one of {', '.join(options)}")
            self._conn.execute(
                "UPDATE votes SET status = 'decided', decided = ?, decided_at = ? WHERE id = ?",
                (option, _now(), int(vote_id)),
            )
        return self.vote_tally(vote_id, room=room)

    def vote_tally(self, vote_id: int, *, room: str | None = None) -> dict[str, Any]:
        """Where the team stands: per-option counts, voter ballots, and the
        fork's consensus word — measured against everyone ASKED, not just the
        ballots cast, so a 2-of-5 lead never reads as a team that has decided.
        Unanimous needs every asked voice; nothing settles here (a vote is
        decided only by the owner's ``vote_call``)."""
        with self._lock:
            row = self._vote_row(self._conn, room, vote_id)
            ballots = [
                dict(ballot) for ballot in self._conn.execute(
                    "SELECT * FROM ballots WHERE vote_id = ? ORDER BY cast_at, voter", (int(vote_id),))
            ]
        counts = {option: 0 for option in json.loads(row["options"])}
        for ballot in ballots:
            counts[ballot["option"]] = counts.get(ballot["option"], 0) + 1
        voters = {ballot["voter"] for ballot in ballots}
        asked = [str(name) for name in json.loads(row["asked"] or "[]")]
        # The card's "waiting on": asked names that have not picked yet.
        missing = [name for name in asked if name not in voters]
        # Without a roster the vote is measured against the quorum count instead
        # (the config knob keeps its meaning when no room roster is known).
        asked_n = len(asked) if asked else max(int(row["quorum"]), 1)
        picks = [ballot["option"] for ballot in ballots]
        top = max(counts.values()) if picks else 0
        leaders = sum(1 for n in counts.values() if n == top and n > 0)
        if not picks:
            consensus = "waiting"
        else:
            # Nobody left to ask and one option carries every pick.
            if not missing and len(set(picks)) == 1 and len(voters) >= asked_n:
                consensus = "unanimous"
            elif top * 2 > asked_n:
                consensus = "majority"
            # A tie for the lead, or no option past a quarter of the room.
            elif len(set(picks)) > 1 and leaders > 1:
                consensus = "split"
            else:
                consensus = "majority"

        out = self._vote_dict(row)
        out["asked"] = asked
        out.update({
            "counts": counts,
            "voters": len(ballots),
            "quorum_met": len(ballots) >= int(row["quorum"]),
            "missing": missing,
            "consensus": consensus,
            "ballots": [{"voter": b["voter"], "option": b["option"]} for b in ballots],
        })
        return out

    def votes(self, room: str) -> list[dict[str, Any]]:
        with self._lock:
            ids = [row["id"] for row in self._conn.execute(
                "SELECT id FROM votes WHERE room = ? ORDER BY id DESC", (str(room),)).fetchall()]
        return [self.vote_tally(vote_id, room=room) for vote_id in ids]

    def vote_rooms(self) -> list[str]:
        """Rooms that hold at least one vote (the brief walks every room)."""
        with self._lock:
            return [row["room"] for row in self._conn.execute("SELECT DISTINCT room FROM votes").fetchall()]

    # ── seat budgets ─────────────────────────────────────────────────────────────────────

    def budget_set(self, room: str, seat: str, budget: int) -> dict[str, Any]:
        budget = int(budget)
        if budget < 0:
            raise CommandError("budget must not be negative")
        seat = str(seat or "").strip()
        if not seat:
            raise CommandError("seat must not be empty")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO budgets (room, seat, budget) VALUES (?, ?, ?)"
                " ON CONFLICT(room, seat) DO UPDATE SET budget = excluded.budget",
                (str(room), seat, budget),
            )
        return {"room": str(room), "seat": seat, "budget": budget}

    def budget_spend(
        self, room: str, seat: str, amount: int, *, note: str = "", budget: int | None = None,
        os_alerts: bool = True,
    ) -> dict[str, Any]:
        """Record spend against the seat's budget. A spend past the budget is
        blocked: nothing is recorded, and (when OS alerts are on) an alert is
        raised for the CEO before the fault surfaces."""
        amount = int(amount)
        if amount <= 0:
            raise CommandError("spend amount must be positive")
        seat = str(seat or "").strip()
        if not seat:
            raise CommandError("seat must not be empty")
        with self._lock:
            row = self._conn.execute(
                "SELECT budget FROM budgets WHERE room = ? AND seat = ?", (str(room), seat)).fetchone()
            limit = int(row["budget"]) if row is not None else int(budget)
            spent = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM spends WHERE room = ? AND seat = ?",
                (str(room), seat)).fetchone()[0]
        if amount + spent > limit:
            if os_alerts:
                self.alert(
                    str(room), "budget", seat,
                    f"seat {seat} in {room!r} blocked: {amount} would pass its budget"
                    f" ({spent}/{limit} used)",
                )
            raise BudgetExhausted(f"seat {seat!r} budget blocked: {spent} spent of {limit}, {amount} requested")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO spends (room, seat, amount, note, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(room), seat, amount, str(note or ""), _now()),
            )
        status = self.budget_status(str(room), default_budget=int(budget or 0))
        return next(row for row in status["seats"] if row["seat"] == seat)

    def budget_status(self, room: str, *, default_budget: int = 0) -> dict[str, Any]:
        with self._lock:
            budget_rows = {
                row["seat"]: row["budget"] for row in self._conn.execute(
                    "SELECT seat, budget FROM budgets WHERE room = ?", (str(room),)).fetchall()
            }
            spend_rows = {
                row["seat"]: row["spent"] for row in self._conn.execute(
                    "SELECT seat, SUM(amount) AS spent FROM spends WHERE room = ? GROUP BY seat",
                    (str(room),)).fetchall()
            }
        seats = sorted(set(budget_rows) | set(spend_rows))
        out = []
        for seat in seats:
            budget = int(budget_rows.get(seat, default_budget))
            spent = int(spend_rows.get(seat, 0))
            out.append({"seat": seat, "budget": budget, "spent": spent, "remaining": budget - spent})
        return {"room": str(room), "seats": out}

    # ── alerts ───────────────────────────────────────────────────────────────────────────

    def alert(self, room: str, kind: str, seat: str, message: str) -> dict[str, Any]:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO alerts (room, kind, seat, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(room), str(kind), str(seat or ""), str(message or ""), _now()),
            )
            alert_id = cursor.lastrowid
        with self._lock:
            row = self._conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return dict(row)

    def alerts(self, room: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alerts WHERE (? IS NULL OR room = ?) ORDER BY id DESC LIMIT ?",
                (room, room, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    # ── moved threads (read marks) ───────────────────────────────────────────────────────

    def seen_map(self, room: str) -> dict[int, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT thread_id, last_seen_at FROM thread_seen WHERE room = ?", (str(room),)).fetchall()
        return {int(row["thread_id"]): int(row["last_seen_at"]) for row in rows}

    def mark_seen(self, room: str, thread_ids: list[int]) -> None:
        if not thread_ids:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO thread_seen (room, thread_id, last_seen_at) VALUES (?, ?, ?)"
                " ON CONFLICT(room, thread_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
                [(str(room), int(thread_id), _now()) for thread_id in thread_ids],
            )

    # ── row shapes ───────────────────────────────────────────────────────────────────────

    def _escalation_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out.pop("source", None)  # sync bookkeeping, not part of the wire shape
        return out

    def _vote_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["options"] = json.loads(out["options"])
        return out
