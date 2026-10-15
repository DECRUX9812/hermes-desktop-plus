"""Team persistence: each mutation loads one team, applies a rule, saves it and audits, in one transaction."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Mapping, Optional

from hermes_plus.store import team_rules as rules
from hermes_plus.store.db import Store

AUDIT_TAIL_DEFAULT = 200
AUDIT_TAIL_MAX = 1000
_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class TeamNotFound(KeyError):
    pass


def _now() -> int:
    return int(time.time())


def _json_list(raw: str | None) -> list[str]:
    return json.loads(raw) if raw else []


class TeamStore:
    def __init__(self, store: Store) -> None:
        self._store = store

    # ── loading and saving whole documents ─────────────────────────────────────────────────

    def _load(self, conn: sqlite3.Connection, team_id: str) -> dict[str, Any]:
        if not _ID_RE.match(team_id or ""):
            raise TeamNotFound(team_id)
        row = conn.execute("SELECT * FROM bot_teams WHERE id = ?", (team_id,)).fetchone()
        if row is None:
            raise TeamNotFound(team_id)
        members = [
            {
                "slot": m["slot"], "profile": m["profile"], "role": m["role"], "title": m["title"],
                "skills": _json_list(m["skills_json"]), "plugins": _json_list(m["plugins_json"]),
                "credentials": _json_list(m["credentials_json"]), "status": m["status"],
                "reports_to": m["reports_to"], "lead": bool(m["is_lead"]), "joined_at": m["joined_at"],
                "budget": {
                    "monthly_usd": m["monthly_usd"], "hard_stop": bool(m["hard_stop"]),
                    "spent_usd": m["spent_usd"], "recorded_usd": m["recorded_usd"],
                    "measured_usd": m["measured_usd"], "synced_at": m["synced_at"], "period": m["period"],
                },
            }
            for m in conn.execute("SELECT * FROM team_members WHERE team_id = ? ORDER BY position", (team_id,))
        ]
        task_rows = conn.execute(
            "SELECT goal_id, task_id FROM team_goal_tasks WHERE team_id = ? ORDER BY position", (team_id,)).fetchall()
        tasks: dict[str, list[str]] = {}
        for t in task_rows:
            tasks.setdefault(t["goal_id"], []).append(t["task_id"])
        goals = [
            {
                "id": g["id"], "title": g["title"], "detail": g["detail"], "parent_id": g["parent_id"],
                "owner": g["owner_slot"], "status": g["status"], "task_ids": tasks.get(g["id"], []),
                "created_at": g["created_at"], "updated_at": g["updated_at"],
            }
            for g in conn.execute("SELECT * FROM team_goals WHERE team_id = ? ORDER BY position", (team_id,))
        ]
        approvals = [
            {
                "id": a["id"], "kind": a["kind"], "subject": a["subject"], "detail": a["detail"],
                "payload": json.loads(a["payload_json"]) if a["payload_json"] is not None else None,
                "requested_by": a["requested_by"], "status": a["status"], "decided_by": a["decided_by"],
                "decided_at": a["decided_at"], "note": a["note"], "created_at": a["created_at"],
            }
            for a in conn.execute("SELECT * FROM team_approvals WHERE team_id = ? ORDER BY position", (team_id,))
        ]
        learnings = [
            {
                "id": x["id"], "text": x["text"], "by": x["by_name"], "source": x["source"],
                "count": x["count"], "at": x["first_at"], "last_at": x["last_at"],
            }
            for x in conn.execute("SELECT * FROM team_learnings WHERE team_id = ? ORDER BY position", (team_id,))
        ]
        return {
            "id": row["id"], "name": row["name"], "mission": row["mission"],
            "policy": {"lead_decides": bool(row["lead_decides"])},
            "channels": json.loads(row["channels_json"]),
            "members": members, "goals": goals, "approvals": approvals, "learnings": learnings,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def _load_all(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        ids = [r["id"] for r in conn.execute("SELECT id FROM bot_teams ORDER BY updated_at DESC, id")]
        return [self._load(conn, team_id) for team_id in ids]

    def _save(self, conn: sqlite3.Connection, team: dict[str, Any]) -> None:
        team_id = team["id"]
        conn.execute(
            "INSERT INTO bot_teams (id, name, mission, lead_decides, channels_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET name = excluded.name, "
            "mission = excluded.mission, lead_decides = excluded.lead_decides, "
            "channels_json = excluded.channels_json, updated_at = excluded.updated_at",
            (team_id, team["name"], team["mission"], int(team["policy"]["lead_decides"]),
             json.dumps(team["channels"]), team["created_at"], team["updated_at"]))
        for table in ("team_goal_tasks", "team_goals", "team_members", "team_approvals", "team_learnings"):
            conn.execute(f"DELETE FROM {table} WHERE team_id = ?", (team_id,))
        for position, m in enumerate(team["members"]):
            b = m["budget"]
            conn.execute(
                "INSERT INTO team_members (team_id, slot, position, profile, role, title, skills_json, plugins_json, "
                "credentials_json, status, reports_to, is_lead, monthly_usd, hard_stop, recorded_usd, measured_usd, "
                "spent_usd, synced_at, period, joined_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (team_id, m["slot"], position, m.get("profile"), m.get("role", ""), m.get("title", ""),
                 json.dumps(m.get("skills", [])), json.dumps(m.get("plugins", [])),
                 json.dumps(m.get("credentials", [])), m.get("status", "active"), m.get("reports_to"),
                 int(bool(m.get("lead"))), b.get("monthly_usd"), int(bool(b.get("hard_stop", True))),
                 b.get("recorded_usd", 0.0), b.get("measured_usd", 0.0), b.get("spent_usd", 0.0),
                 b.get("synced_at", 0), b["period"], m.get("joined_at", team["created_at"])))
        for position, g in enumerate(team["goals"]):
            conn.execute(
                "INSERT INTO team_goals (team_id, id, position, title, detail, parent_id, owner_slot, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (team_id, g["id"], position, g["title"], g.get("detail", ""), g.get("parent_id"), g.get("owner"),
                 g["status"], g.get("created_at", team["created_at"]), g.get("updated_at", team["updated_at"])))
            for task_position, task_id in enumerate(g["task_ids"]):
                conn.execute(
                    "INSERT INTO team_goal_tasks (team_id, goal_id, position, task_id) VALUES (?, ?, ?, ?)",
                    (team_id, g["id"], task_position, task_id))
        for position, a in enumerate(team["approvals"]):
            conn.execute(
                "INSERT INTO team_approvals (team_id, id, position, kind, subject, detail, payload_json, requested_by, "
                "status, decided_by, decided_at, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (team_id, a["id"], position, a["kind"], a["subject"], a.get("detail", ""),
                 json.dumps(a["payload"]) if a.get("payload") is not None else None, a["requested_by"],
                 a["status"], a.get("decided_by"), a.get("decided_at"), a.get("note", ""), a["created_at"]))
        for position, x in enumerate(team["learnings"]):
            conn.execute(
                "INSERT INTO team_learnings (team_id, id, position, text, norm_key, by_name, source, count, first_at, "
                "last_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (team_id, x["id"], position, x["text"], rules.norm_key(x["text"]), x["by"], x.get("source", ""),
                 x.get("count", 1), x["at"], x.get("last_at", x["at"])))

    def _audit(self, conn: sqlite3.Connection, team_id: str, actor: str, action: str,
               detail: Optional[Mapping[str, Any]] = None) -> None:
        conn.execute(
            "INSERT INTO team_audit (team_id, at, actor, action, detail_json) VALUES (?, ?, ?, ?, ?)",
            (team_id, _now(), actor or "unknown", action, json.dumps(detail or {}, ensure_ascii=False, default=str)))

    def _mutate(self, team_id: str, actor: str, change) -> Any:
        """Load, apply ``change(team)`` -> (result, action, detail), stamp, save and audit atomically."""
        with self._store.write() as conn:
            team = self._load(conn, team_id)
            result, action, detail = change(team)
            team["updated_at"] = _now()
            self._save(conn, team)
            self._audit(conn, team_id, actor, action, detail)
        return result

    # ── reads ──────────────────────────────────────────────────────────────────────────────

    def get(self, team_id: str) -> dict[str, Any]:
        with self._store.read() as conn:
            return self._load(conn, team_id)

    def list_summaries(self) -> list[dict[str, Any]]:
        out = []
        for team in self._load_all_read():
            members = team["members"]
            out.append({
                "id": team["id"], "name": team["name"], "mission": team["mission"],
                "member_count": len(members),
                "open_seats": sum(1 for m in members if not m.get("profile")),
                "goal_count": len(team["goals"]),
                "pending_approvals": sum(1 for a in team["approvals"] if a["status"] == "pending"),
                "created_at": team["created_at"], "updated_at": team["updated_at"],
            })
        return out

    def _load_all_read(self) -> list[dict[str, Any]]:
        with self._store.read() as conn:
            return self._load_all(conn)

    def audit_list(self, team_id: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
        with self._store.read() as conn:
            self._load(conn, team_id)
            rows = conn.execute(
                "SELECT at, actor, action, detail_json FROM team_audit WHERE team_id = ? ORDER BY id DESC LIMIT ?",
                (team_id, max(1, min(int(limit or AUDIT_TAIL_DEFAULT), AUDIT_TAIL_MAX)))).fetchall()
        return [{"at": r["at"], "actor": r["actor"], "action": r["action"],
                 "detail": json.loads(r["detail_json"])} for r in rows]

    def room_lead(self, profiles: list[str]) -> Optional[dict[str, Any]]:
        return rules.room_lead_for(self._load_all_read(), profiles)

    def view(self, team_id: str) -> dict[str, Any]:
        team = self.get(team_id)
        return {"team": team, "tree": rules.org_tree(team), "rollup": self.rollup_of(team)}

    @staticmethod
    def rollup_of(team: dict[str, Any]) -> dict[str, Any]:
        # Kanban task status is not reachable from plus.db, so every linked task counts as not done.
        statuses: dict[str, Optional[str]] = {}
        for goal in team["goals"]:
            for task_id in goal["task_ids"]:
                statuses[task_id] = None
        return rules.rollup(team, statuses)

    def brief(self, team_id: str, ref: str, goal_id: Optional[str] = None) -> str:
        team = self.get(team_id)
        return rules.build_brief(team, ref, goal_id=goal_id)

    def export_pack(self, team_id: str) -> dict[str, Any]:
        return rules.export_pack(self.get(team_id))

    def check_budget(self, team_id: str, ref: str) -> dict[str, Any]:
        return rules.check_budget(self.get(team_id), ref, _now())

    def spawn_brief(self, team_id: str, goal_id: str, assignee: Optional[str], title: str, body: str,
                    ) -> tuple[str, str, str]:
        return rules.spawn_brief(self.get(team_id), goal_id, assignee, title=title, body=body)

    # ── team lifecycle ─────────────────────────────────────────────────────────────────────

    def create(self, *, name: str, mission: str, actor: str, lead_decides: bool, channels: Any) -> dict[str, Any]:
        now = _now()
        team = rules.new_team(name=name, mission=mission, lead_decides=lead_decides, channels=channels, now=now)
        with self._store.write() as conn:
            self._save(conn, team)
            self._audit(conn, team["id"], actor, "team.create", {"name": team["name"]})
        return team

    def update(self, team_id: str, *, actor: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        def change(team):
            changed = rules.update_team(team, fields)
            return team, "team.update", changed

        return self._mutate(team_id, actor, change)

    def delete(self, team_id: str, *, actor: str) -> None:
        with self._store.write() as conn:
            self._load(conn, team_id)
            self._audit(conn, team_id, actor, "team.delete")
            conn.execute("DELETE FROM bot_teams WHERE id = ?", (team_id,))

    # ── members ────────────────────────────────────────────────────────────────────────────

    def upsert_member(self, team_id: str, *, slot: Optional[str], actor: str,
                      fields: Mapping[str, Any]) -> dict[str, Any]:
        def change(team):
            member, created = rules.upsert_member(team, slot=slot, fields=fields, now=_now())
            detail = {"slot": member["slot"], "profile": member.get("profile"), "role": member.get("role", "")}
            return member, "member.add" if created else "member.update", detail

        return self._mutate(team_id, actor, change)

    def remove_member(self, team_id: str, ref: str, *, actor: str) -> None:
        def change(team):
            member = rules.remove_member(team, ref)
            return None, "member.remove", {"slot": member["slot"], "profile": member.get("profile")}

        self._mutate(team_id, actor, change)

    # ── goals ──────────────────────────────────────────────────────────────────────────────

    def upsert_goal(self, team_id: str, *, goal_id: Optional[str], actor: str,
                    fields: Mapping[str, Any]) -> dict[str, Any]:
        def change(team):
            goal = rules.upsert_goal(team, goal_id=goal_id, fields=fields, now=_now())
            detail = {"goal": goal["id"], "title": goal["title"], "status": goal["status"]}
            return goal, "goal.update" if goal_id else "goal.add", detail

        return self._mutate(team_id, actor, change)

    def link_task(self, team_id: str, goal_id: str, task_id: str, *, unlink: bool, actor: str) -> dict[str, Any]:
        def change(team):
            goal = rules.link_task(team, goal_id, task_id, unlink=unlink)
            action = "goal.unlink_task" if unlink else "goal.link_task"
            return goal, action, {"goal": goal_id, "task": task_id}

        return self._mutate(team_id, actor, change)

    # ── budgets ────────────────────────────────────────────────────────────────────────────

    def record_spend(self, team_id: str, ref: str, usd: Any, *, actor: str) -> dict[str, Any]:
        spender = actor or "system"
        with self._store.write() as conn:
            team = self._load(conn, team_id)
            member = rules.record_spend(team, ref, usd, _now())
            team["updated_at"] = _now()
            self._save(conn, team)
            conn.execute(
                "INSERT INTO team_budget_ledger (team_id, slot, usd, actor, at) VALUES (?, ?, ?, ?, ?)",
                (team_id, member["slot"], float(usd), spender, _now()))
            self._audit(conn, team_id, spender, "budget.spend", {"slot": member["slot"], "usd": float(usd)})
            return rules.check_budget(team, ref, _now())

    # ── governance ─────────────────────────────────────────────────────────────────────────

    def request_approval(self, team_id: str, *, kind: Any, subject: Any, requested_by: Any, detail: Any,
                         payload: Any) -> dict[str, Any]:
        def change(team):
            req = rules.request_approval(team, kind=kind, subject=subject, requested_by=requested_by,
                                         detail=detail, payload=payload, now=_now())
            return req, "approval.request", {"id": req["id"], "kind": req["kind"], "subject": req["subject"]}

        return self._mutate(team_id, str(requested_by or "unknown"), change)

    def decide_approval(self, team_id: str, approval_id: str, *, approve: bool, actor: str, note: str,
                        ) -> dict[str, Any]:
        def change(team):
            req = rules.decide_approval(team, approval_id, approve=approve, actor=actor, note=note, now=_now())
            return req, "approval.decide", {"id": approval_id, "status": req["status"]}

        return self._mutate(team_id, actor, change)

    # ── learning ───────────────────────────────────────────────────────────────────────────

    def add_learning(self, team_id: str, *, text: Any, by: Any, source: Any) -> dict[str, Any]:
        with self._store.write() as conn:
            team = self._load(conn, team_id)
            item, created = rules.add_learning(team, text=text, by=by, source=source, now=_now())
            team["updated_at"] = _now()
            self._save(conn, team)
            if created:
                self._audit(conn, team_id, item["by"], "learning.add", {"id": item["id"]})
        return item

    def remove_learning(self, team_id: str, learning_id: str, *, actor: str) -> None:
        def change(team):
            rules.remove_learning(team, learning_id)
            return None, "learning.remove", {"id": learning_id}

        self._mutate(team_id, actor, change)

    # ── packs ──────────────────────────────────────────────────────────────────────────────

    def import_pack(self, pack: Any, *, name: Optional[str], actor: str) -> dict[str, Any]:
        pack_name, mission, lead_decides, seats = rules.pack_seats(pack)
        now = _now()
        team = rules.new_team(name=name or pack_name, mission=mission, lead_decides=lead_decides,
                              channels={}, now=now)
        created: dict[str, str] = {}
        for seat in seats:
            fields: dict[str, Any] = {
                "role": seat.get("role"), "title": seat.get("title"), "skills": seat.get("skills"),
                "plugins": seat.get("plugins"), "credentials": seat.get("credentials"),
                "hard_stop": seat.get("hard_stop", True),
            }
            if seat.get("monthly_usd") is not None:
                fields["monthly_usd"] = seat["monthly_usd"]
            member, _ = rules.upsert_member(team, slot=None, fields=fields, now=now)
            created[str(seat.get("slot"))] = member["slot"]
        for seat in seats:
            me, boss = created.get(str(seat.get("slot"))), created.get(str(seat.get("reports_to_slot")))
            if me and boss:
                rules.upsert_member(team, slot=me, fields={"reports_to": boss}, now=now)
            if me and seat.get("lead"):
                rules.upsert_member(team, slot=me, fields={"lead": True}, now=now)
        with self._store.write() as conn:
            self._save(conn, team)
            self._audit(conn, team["id"], actor, "team.create", {"name": team["name"]})
        return self.get(team["id"])

    def import_document(self, team: dict[str, Any], audit_rows: list[dict[str, Any]]) -> bool:
        """Insert a team read from the fork's JSON files. Returns False when the id already exists."""
        with self._store.write() as conn:
            exists = conn.execute("SELECT 1 FROM bot_teams WHERE id = ?", (team["id"],)).fetchone()
            if exists is not None:
                return False
            self._save(conn, team)
            for row in audit_rows:
                conn.execute(
                    "INSERT INTO team_audit (team_id, at, actor, action, detail_json) VALUES (?, ?, ?, ?, ?)",
                    (team["id"], int(row.get("at") or 0), str(row.get("actor") or "unknown"),
                     str(row.get("action") or ""), json.dumps(row.get("detail") or {}, ensure_ascii=False, default=str)))
        return True
