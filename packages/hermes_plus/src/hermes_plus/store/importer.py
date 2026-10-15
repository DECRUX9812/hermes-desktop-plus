"""One-time import of the fork's approval journal and team files into plus.db. Read-only on the source."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from hermes_plus.store.db import Store
from hermes_plus.store.teams import TeamStore

_TEAM_ID = re.compile(r"^[a-z0-9_]{1,64}$")
_APPROVAL_COLUMNS = ("request_id", "session_id", "tool_call_id", "tool_name", "command", "description",
                     "created_at", "status", "decided_at", "choice", "ran_at", "reason")


def import_fork_data(hermes_home: Path, store: Store) -> dict[str, int]:
    counts = {
        "approvals_imported": 0,
        "approvals_existing": 0,
        "approvals_bad": 0,
        "teams_imported": 0,
        "teams_existing": 0,
        "team_files_bad": 0,
        "audit_rows_imported": 0,
    }
    _import_approvals(hermes_home / "state.db", store, counts)
    _import_teams(hermes_home / "bot-teams", store, counts)
    return counts


def _import_approvals(db_path: Path, store: Store, counts: dict[str, int]) -> None:
    if not db_path.is_file():
        return
    try:
        source = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return
    try:
        source.row_factory = sqlite3.Row
        rows = source.execute(f"SELECT {', '.join(_APPROVAL_COLUMNS)} FROM fork_approvals").fetchall()
    except sqlite3.Error:
        return
    finally:
        source.close()
    placeholders = ", ".join("?" for _ in _APPROVAL_COLUMNS)
    with store.write() as conn:
        for row in rows:
            try:
                cursor = conn.execute(
                    f"INSERT INTO approvals ({', '.join(_APPROVAL_COLUMNS)}) VALUES ({placeholders}) "
                    "ON CONFLICT(request_id) DO NOTHING",
                    tuple(row[column] for column in _APPROVAL_COLUMNS))
            except sqlite3.IntegrityError:
                counts["approvals_bad"] += 1
                continue
            counts["approvals_imported" if cursor.rowcount else "approvals_existing"] += 1


def _import_teams(root: Path, store: Store, counts: dict[str, int]) -> None:
    teams_dir = root / "teams"
    if not teams_dir.is_dir():
        return
    teams = TeamStore(store)
    for path in sorted(teams_dir.glob("*.json")):
        try:
            team = _team_from_doc(json.loads(path.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            counts["team_files_bad"] += 1
            continue
        audit_rows = _read_audit(root / "audit" / f"{team['id']}.jsonl")
        try:
            imported = teams.import_document(team, audit_rows)
        except sqlite3.Error:
            counts["team_files_bad"] += 1
            continue
        if imported:
            counts["teams_imported"] += 1
            counts["audit_rows_imported"] += len(audit_rows)
        else:
            counts["teams_existing"] += 1


def _read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _team_from_doc(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise TypeError("team document is not an object")
    team_id = str(doc["id"])
    if not _TEAM_ID.match(team_id):
        raise ValueError(f"unsafe team id {team_id!r}")
    policy = doc.get("policy") or {}
    return {
        "id": team_id,
        "name": str(doc["name"]),
        "mission": str(doc.get("mission") or ""),
        "policy": {"lead_decides": bool(policy.get("lead_decides"))},
        "channels": {str(k): str(v) for k, v in (doc.get("channels") or {}).items()},
        "members": [_member(m) for m in doc.get("members") or []],
        "goals": [_goal(g) for g in doc.get("goals") or []],
        "approvals": [_approval(a) for a in doc.get("approvals") or []],
        "learnings": [_learning(x) for x in doc.get("learnings") or []],
        "created_at": int(doc.get("created_at") or 0),
        "updated_at": int(doc.get("updated_at") or 0),
    }


def _member(raw: dict[str, Any]) -> dict[str, Any]:
    budget = raw.get("budget") or {}
    return {
        "slot": str(raw["slot"]),
        "profile": raw.get("profile") or None,
        "role": str(raw.get("role") or ""),
        "title": str(raw.get("title") or ""),
        "skills": list(raw.get("skills") or []),
        "plugins": list(raw.get("plugins") or []),
        "credentials": list(raw.get("credentials") or []),
        "status": raw.get("status") if raw.get("status") in ("active", "paused") else "active",
        "reports_to": raw.get("reports_to") or None,
        "lead": bool(raw.get("lead")),
        "joined_at": int(raw.get("joined_at") or 0),
        "budget": {
            "monthly_usd": None if budget.get("monthly_usd") in (None, "") else float(budget["monthly_usd"]),
            "hard_stop": bool(budget.get("hard_stop", True)),
            "spent_usd": float(budget.get("spent_usd") or 0.0),
            "recorded_usd": float(budget.get("recorded_usd", budget.get("spent_usd")) or 0.0),
            "measured_usd": float(budget.get("measured_usd") or 0.0),
            "synced_at": int(budget.get("synced_at") or 0),
            "period": str(budget.get("period") or ""),
        },
    }


def _goal(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw["id"]),
        "title": str(raw["title"]),
        "detail": str(raw.get("detail") or ""),
        "parent_id": raw.get("parent_id") or None,
        "owner": raw.get("owner") or None,
        "status": str(raw["status"]),
        "task_ids": [str(t) for t in raw.get("task_ids") or []],
        "created_at": int(raw.get("created_at") or 0),
        "updated_at": int(raw.get("updated_at") or raw.get("created_at") or 0),
    }


def _approval(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw["id"]),
        "kind": str(raw["kind"]),
        "subject": str(raw["subject"]),
        "detail": str(raw.get("detail") or ""),
        "payload": raw.get("payload") if isinstance(raw.get("payload"), dict) else None,
        "requested_by": str(raw.get("requested_by") or "unknown"),
        "status": str(raw["status"]),
        "decided_by": raw.get("decided_by") or None,
        "decided_at": int(raw["decided_at"]) if raw.get("decided_at") is not None else None,
        "note": str(raw.get("note") or ""),
        "created_at": int(raw.get("created_at") or 0),
    }


def _learning(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw["id"]),
        "text": str(raw["text"]),
        "by": str(raw.get("by") or "unknown"),
        "source": str(raw.get("source") or ""),
        "count": int(raw.get("count") or 1),
        "at": int(raw.get("at") or 0),
        "last_at": int(raw.get("last_at") or raw.get("at") or 0),
    }
