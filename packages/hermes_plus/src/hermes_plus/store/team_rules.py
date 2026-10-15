"""Team domain rules: org chart, goals, budgets, governance, shared learning, briefs and packs.

Pure functions over the in-memory team document (same shape as the fork's JSON team file).
Nothing here touches storage; TeamStore loads a document, applies one rule, saves it, and
writes the audit line in one transaction.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Iterable, Mapping, Optional

TEAM_ID_PREFIX = "team_"
GOAL_ID_PREFIX = "goal_"
APPROVAL_ID_PREFIX = "apr_"
LEARNING_ID_PREFIX = "lrn_"
SEAT_ID_PREFIX = "seat_"

NAME_MAX = 80
TEXT_MAX = 2000
MISSION_MAX = 4000
LIST_MAX = 64
LEARNINGS_MAX = 200
GOAL_STATUSES = ("open", "active", "blocked", "done", "cancelled")
GOAL_SETTLED = frozenset({"done", "cancelled"})
APPROVAL_KINDS = ("hire", "spend", "credential", "action")
MEMBER_STATUSES = ("active", "paused")
BOARD_ACTOR = "you"
PACK_VERSION = 1
PACK_MAX_SEATS = 64
PACK_MEMBER_KEYS = ("slot", "role", "title", "reports_to_slot", "lead", "skills", "plugins",
                    "credentials", "monthly_usd", "hard_stop")

_NORM_RE = re.compile(r"\W+")


class TeamError(ValueError):
    """A domain refusal (bad input, cycle, governance)."""


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def current_period(now: int) -> str:
    return time.strftime("%Y-%m", time.gmtime(now))


def clean_text(raw: Any, cap: int) -> str:
    return str(raw or "").strip()[:cap]


def norm_key(text: str) -> str:
    return _NORM_RE.sub(" ", text.lower()).strip()


def _names(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        name = clean_text(item, 80)
        if name and name not in out:
            out.append(name)
        if len(out) >= LIST_MAX:
            break
    return out


def _channels(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {clean_text(k, 32): clean_text(v, 120) for k, v in raw.items() if clean_text(k, 32) and clean_text(v, 120)}


def _budget(raw: Mapping[str, Any], now: int, prev: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    prev = prev or {}
    limit = raw.get("monthly_usd", prev.get("monthly_usd"))
    try:
        limit = None if limit in (None, "") else max(0.0, float(limit))
    except (TypeError, ValueError):
        raise TeamError("monthly_usd must be a number or null") from None
    return {
        "monthly_usd": limit,
        "hard_stop": bool(raw.get("hard_stop", prev.get("hard_stop", True))),
        "spent_usd": float(prev.get("spent_usd", 0.0)),
        "recorded_usd": float(prev.get("recorded_usd", prev.get("spent_usd", 0.0))),
        "measured_usd": float(prev.get("measured_usd", 0.0)),
        "synced_at": int(prev.get("synced_at", 0)),
        "period": prev.get("period") or current_period(now),
    }


def roll_period(budget: dict[str, Any], now: int) -> dict[str, Any]:
    """Spend is per calendar month (UTC): a new month zeroes the counters."""
    period = current_period(now)
    if budget.get("period") != period:
        budget.update(period=period, spent_usd=0.0, recorded_usd=0.0, measured_usd=0.0, synced_at=0)
    return budget


def new_team(*, name: str, mission: str, lead_decides: bool, channels: Any, now: int) -> dict[str, Any]:
    clean_name = clean_text(name, NAME_MAX)
    if not clean_name:
        raise TeamError("team name required")
    return {
        "id": new_id(TEAM_ID_PREFIX),
        "name": clean_name,
        "mission": clean_text(mission, MISSION_MAX),
        "policy": {"lead_decides": bool(lead_decides)},
        "channels": _channels(channels),
        "members": [],
        "goals": [],
        "approvals": [],
        "learnings": [],
        "created_at": now,
        "updated_at": now,
    }


def update_team(team: dict[str, Any], fields: Mapping[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    if "name" in fields:
        name = clean_text(fields["name"], NAME_MAX)
        if not name:
            raise TeamError("team name required")
        team["name"], changed["name"] = name, name
    if "mission" in fields:
        team["mission"] = changed["mission"] = clean_text(fields["mission"], MISSION_MAX)
    if "lead_decides" in fields:
        team["policy"]["lead_decides"] = changed["lead_decides"] = bool(fields["lead_decides"])
    if "channels" in fields:
        team["channels"] = _channels(fields["channels"])
        changed["channels"] = team["channels"]
    return changed


def find_member(team: dict[str, Any], ref: str) -> Optional[dict[str, Any]]:
    for m in team["members"]:
        if ref and (m["slot"] == ref or m.get("profile") == ref):
            return m
    return None


def _would_cycle(team: dict[str, Any], slot: str, reports_to: Optional[str]) -> bool:
    seen = {slot}
    cur = reports_to
    while cur:
        if cur in seen:
            return True
        seen.add(cur)
        boss = next((m for m in team["members"] if m["slot"] == cur), None)
        cur = boss.get("reports_to") if boss else None
    return False


def upsert_member(team: dict[str, Any], *, slot: Optional[str], fields: Mapping[str, Any], now: int,
                  ) -> tuple[dict[str, Any], bool]:
    existing = find_member(team, slot or "") or (
        find_member(team, str(fields.get("profile") or "")) if not slot else None)
    profile = fields.get("profile", existing.get("profile") if existing else None)
    profile = clean_text(profile, 80) or None
    if profile and any(m.get("profile") == profile and m is not existing for m in team["members"]):
        raise TeamError(f"'{profile}' already holds a seat on this team")

    member = existing or {"slot": new_id(SEAT_ID_PREFIX), "joined_at": now, "status": "active",
                          "budget": _budget({}, now), "reports_to": None, "lead": False}
    member["profile"] = profile
    if "role" in fields or not existing:
        member["role"] = clean_text(fields.get("role"), NAME_MAX)
    if "title" in fields or not existing:
        member["title"] = clean_text(fields.get("title"), NAME_MAX)
    for key in ("skills", "plugins", "credentials"):
        if key in fields or not existing:
            member[key] = _names(fields.get(key))
    if "status" in fields:
        status = str(fields["status"]).lower()
        if status not in MEMBER_STATUSES:
            raise TeamError(f"status must be one of {MEMBER_STATUSES}")
        member["status"] = status
    if "monthly_usd" in fields or "hard_stop" in fields:
        member["budget"] = _budget(
            {k: fields[k] for k in ("monthly_usd", "hard_stop") if k in fields}, now, member["budget"])
    if "reports_to" in fields:
        boss_ref = fields["reports_to"]
        boss = find_member(team, boss_ref) if boss_ref else None
        if boss_ref and boss is None:
            raise TeamError(f"reports_to '{boss_ref}' is not on this team")
        boss_slot = boss["slot"] if boss else None
        if boss_slot == member["slot"] or _would_cycle(team, member["slot"], boss_slot):
            raise TeamError("reporting line would create a cycle")
        member["reports_to"] = boss_slot
    if fields.get("lead"):
        for other in team["members"]:
            other["lead"] = False
        member["lead"] = True
    elif "lead" in fields:
        member["lead"] = False
    if not existing:
        team["members"].append(member)
    return member, existing is None


def remove_member(team: dict[str, Any], ref: str) -> dict[str, Any]:
    member = find_member(team, ref)
    if member is None:
        raise TeamError(f"no member '{ref}'")
    for other in team["members"]:
        if other.get("reports_to") == member["slot"]:
            other["reports_to"] = member.get("reports_to")
    for goal in team["goals"]:
        if goal.get("owner") == member["slot"]:
            goal["owner"] = None
    team["members"].remove(member)
    return member


def org_tree(team: dict[str, Any]) -> list[dict[str, Any]]:
    by_boss: dict[Optional[str], list[dict[str, Any]]] = {}
    for m in team["members"]:
        by_boss.setdefault(m.get("reports_to"), []).append(m)

    def build(m: dict[str, Any]) -> dict[str, Any]:
        return {**m, "reports": [build(c) for c in by_boss.get(m["slot"], [])]}

    return [build(m) for m in by_boss.get(None, [])]


def lead_member(team: dict[str, Any]) -> Optional[dict[str, Any]]:
    explicit = next((m for m in team["members"] if m.get("lead")), None)
    if explicit is not None:
        return explicit
    roots = [m for m in team["members"] if not m.get("reports_to")]
    return roots[0] if len(roots) == 1 else None


def room_lead_for(teams: Iterable[dict[str, Any]], profiles: Iterable[str]) -> Optional[dict[str, Any]]:
    want = {str(p).strip() for p in profiles if str(p).strip()}
    if not want:
        return None
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for team in teams:
        filled = {m.get("profile") for m in team["members"] if m.get("profile")}
        if not want.issubset(filled):
            continue
        lead = lead_member(team)
        if lead is None or not lead.get("profile") or lead["profile"] not in want or lead.get("status") == "paused":
            continue
        candidates.append((team, lead))
    if len({lead["profile"] for _team, lead in candidates}) != 1:
        return None
    team, lead = min(
        candidates,
        key=lambda tl: (sum(1 for m in tl[0]["members"] if m.get("profile")), str(tl[0]["id"])))
    return {
        "team_id": team["id"], "team_name": team["name"] or "",
        "lead": lead["profile"], "lead_slot": lead.get("slot") or "",
        "lead_title": lead.get("title") or "",
    }


def find_goal(team: dict[str, Any], goal_id: str) -> dict[str, Any]:
    for g in team["goals"]:
        if g["id"] == goal_id:
            return g
    raise TeamError(f"no goal '{goal_id}'")


def upsert_goal(team: dict[str, Any], *, goal_id: Optional[str], fields: Mapping[str, Any], now: int,
                ) -> dict[str, Any]:
    goal = find_goal(team, goal_id) if goal_id else None
    if goal is None:
        title = clean_text(fields.get("title"), NAME_MAX * 2)
        if not title:
            raise TeamError("goal title required")
        goal = {"id": new_id(GOAL_ID_PREFIX), "title": title, "detail": "", "parent_id": None,
                "owner": None, "status": "open", "task_ids": [], "created_at": now}
        team["goals"].append(goal)
    if "title" in fields and goal_id:
        title = clean_text(fields["title"], NAME_MAX * 2)
        if not title:
            raise TeamError("goal title required")
        goal["title"] = title
    if "detail" in fields:
        goal["detail"] = clean_text(fields["detail"], TEXT_MAX)
    if "parent_id" in fields:
        parent = fields["parent_id"] or None
        if parent:
            find_goal(team, parent)
            cur: Optional[str] = parent
            while cur:
                if cur == goal["id"]:
                    raise TeamError("goal hierarchy would create a cycle")
                cur = find_goal(team, cur).get("parent_id")
        goal["parent_id"] = parent
    if "owner" in fields:
        ref = fields["owner"]
        owner = find_member(team, ref) if ref else None
        if ref and owner is None:
            raise TeamError(f"owner '{ref}' is not on this team")
        goal["owner"] = owner["slot"] if owner else None
    if "status" in fields:
        status = str(fields["status"]).lower()
        if status not in GOAL_STATUSES:
            raise TeamError(f"status must be one of {GOAL_STATUSES}")
        goal["status"] = status
    goal["updated_at"] = now
    return goal


def link_task(team: dict[str, Any], goal_id: str, task_id: str, *, unlink: bool) -> dict[str, Any]:
    clean_task = clean_text(task_id, 80)
    if not clean_task:
        raise TeamError("task_id required")
    goal = find_goal(team, goal_id)
    if unlink:
        goal["task_ids"] = [t for t in goal["task_ids"] if t != clean_task]
    elif clean_task not in goal["task_ids"]:
        goal["task_ids"].append(clean_task)
    return goal


def goal_ancestry(team: dict[str, Any], goal_id: str) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    cur: Optional[str] = goal_id
    guard = 0
    while cur and guard < 64:
        g = find_goal(team, cur)
        chain.append({"id": g["id"], "title": g["title"]})
        cur = g.get("parent_id")
        guard += 1
    chain.append({"id": None, "title": team.get("mission") or team["name"]})
    return chain[::-1]


def rollup(team: dict[str, Any], statuses: Mapping[str, Optional[str]]) -> dict[str, Any]:
    children: dict[Optional[str], list[dict[str, Any]]] = {}
    for g in team["goals"]:
        children.setdefault(g.get("parent_id"), []).append(g)

    result: dict[str, dict[str, Any]] = {}

    def walk(g: dict[str, Any]) -> tuple[int, int, bool]:
        done = total = 0
        blocked = g["status"] == "blocked"
        for t in g["task_ids"]:
            s = (statuses.get(t) or "").lower()
            if s == "archived":
                continue
            total += 1
            done += s == "done"
            blocked = blocked or s == "blocked"
        for c in children.get(g["id"], []):
            cd, ct, cb = walk(c)
            done, total, blocked = done + cd, total + ct, blocked or cb
        if g["status"] in GOAL_SETTLED and total == 0:
            done = total = 1
        result[g["id"]] = {"done": done, "total": total, "blocked": blocked, "status": g["status"],
                           "percent": int(100 * done / total) if total else (100 if g["status"] == "done" else 0)}
        return done, total, blocked

    od = ot = 0
    for root_goal in children.get(None, []):
        d, t, _ = walk(root_goal)
        od, ot = od + d, ot + t
    return {"goals": result, "overall": {"done": od, "total": ot, "percent": int(100 * od / ot) if ot else 0}}


def check_budget(team: dict[str, Any], ref: str, now: int) -> dict[str, Any]:
    m = find_member(team, ref)
    if m is None:
        return {"allowed": True, "reason": "not a team member", "remaining_usd": None, "over_budget": False}
    b = roll_period(dict(m["budget"]), now)
    limit, spent = b["monthly_usd"], b["spent_usd"]
    remaining = None if limit is None else round(limit - spent, 6)
    over = limit is not None and spent >= limit
    if m["status"] == "paused":
        return {"allowed": False, "reason": "paused", "remaining_usd": remaining, "over_budget": over}
    if over and b["hard_stop"]:
        return {"allowed": False, "reason": "budget exhausted", "remaining_usd": remaining, "over_budget": True}
    return {"allowed": True, "reason": "", "remaining_usd": remaining, "over_budget": over}


def record_spend(team: dict[str, Any], ref: str, usd: Any, now: int) -> dict[str, Any]:
    try:
        amount = float(usd)
    except (TypeError, ValueError):
        raise TeamError("usd must be a number") from None
    if amount < 0:
        raise TeamError("spend cannot be negative")
    m = find_member(team, ref)
    if m is None:
        raise TeamError(f"no member '{ref}'")
    b = roll_period(m["budget"], now)
    b["recorded_usd"] = round(b.get("recorded_usd", 0.0) + amount, 6)
    b["spent_usd"] = round(b["recorded_usd"] + b.get("measured_usd", 0.0), 6)
    return m


def request_approval(team: dict[str, Any], *, kind: Any, subject: Any, requested_by: Any, detail: Any,
                     payload: Any, now: int) -> dict[str, Any]:
    clean_kind = str(kind or "").lower()
    if clean_kind not in APPROVAL_KINDS:
        raise TeamError(f"kind must be one of {APPROVAL_KINDS}")
    clean_subject = clean_text(subject, 200)
    if not clean_subject:
        raise TeamError("subject required")
    req = {"id": new_id(APPROVAL_ID_PREFIX), "kind": clean_kind, "subject": clean_subject,
           "detail": clean_text(detail, TEXT_MAX), "payload": payload if isinstance(payload, dict) else None,
           "requested_by": clean_text(requested_by, 80) or "unknown", "status": "pending",
           "decided_by": None, "decided_at": None, "note": "", "created_at": now}
    team["approvals"].append(req)
    return req


def can_decide(team: dict[str, Any], actor: str) -> bool:
    if actor == BOARD_ACTOR:
        return True
    if not team["policy"].get("lead_decides"):
        return False
    m = find_member(team, actor)
    return bool(m and m.get("lead"))


def decide_approval(team: dict[str, Any], approval_id: str, *, approve: bool, actor: str, note: str,
                    now: int) -> dict[str, Any]:
    req = next((a for a in team["approvals"] if a["id"] == approval_id), None)
    if req is None:
        raise TeamError(f"no approval '{approval_id}'")
    if req["status"] != "pending":
        raise TeamError(f"approval already {req['status']}")
    if not can_decide(team, actor):
        raise TeamError("only the board (or the lead, if the team allows it) can decide approvals")
    requester = find_member(team, req["requested_by"])
    actor_member = find_member(team, actor)
    if actor != BOARD_ACTOR and (actor == req["requested_by"] or (
            requester and actor_member and requester["slot"] == actor_member["slot"])):
        raise TeamError("a requester cannot decide its own approval")
    req.update(status="approved" if approve else "rejected", decided_by=actor, decided_at=now,
               note=clean_text(note, 500))
    return req


def add_learning(team: dict[str, Any], *, text: Any, by: Any, source: Any, now: int) -> tuple[dict[str, Any], bool]:
    clean = clean_text(text, 400)
    if not clean:
        raise TeamError("learning text required")
    key = norm_key(clean)
    for item in team["learnings"]:
        if norm_key(item["text"]) == key:
            item["count"] = item.get("count", 1) + 1
            item["last_at"] = now
            return item, False
    item = {"id": new_id(LEARNING_ID_PREFIX), "text": clean, "by": clean_text(by, 80) or "unknown",
            "source": clean_text(source, 120), "count": 1, "at": now, "last_at": now}
    team["learnings"].append(item)
    team["learnings"] = team["learnings"][-LEARNINGS_MAX:]
    return item, True


def remove_learning(team: dict[str, Any], learning_id: str) -> None:
    before = len(team["learnings"])
    team["learnings"] = [x for x in team["learnings"] if x["id"] != learning_id]
    if len(team["learnings"]) == before:
        raise TeamError(f"no learning '{learning_id}'")


def build_brief(team: dict[str, Any], ref: str, *, goal_id: Optional[str] = None, max_learnings: int = 12,
                include_goals: bool = True) -> str:
    m = find_member(team, ref)
    if m is None:
        raise TeamError(f"no member '{ref}'")
    lines = [f"You are {m.get('title') or m.get('role') or 'a teammate'} on the team “{team['name']}”."]
    if team.get("mission"):
        lines.append(f"Team mission: {team['mission']}")
    boss = next((x for x in team["members"] if x["slot"] == m.get("reports_to")), None)
    if boss:
        who = boss.get("profile") or boss.get("title") or boss.get("role")
        lines.append(f"You report to {who}" + (f" ({boss['title']})." if boss.get("title") and boss.get("profile") else "."))
    if m.get("role"):
        lines.append(f"Your role: {m['role']}.")
    mates = [x for x in team["members"] if x is not m and x.get("profile")]
    if mates:
        lines.append("Teammates: " + ", ".join(
            f"{x['profile']} ({x.get('role') or x.get('title') or 'member'})" for x in mates[:16]))
    if goal_id:
        chain = goal_ancestry(team, goal_id)
        lines.append("Why this work matters: " + " → ".join(c["title"] for c in chain))
    elif include_goals:
        mine = [g for g in team["goals"] if g.get("owner") == m["slot"] and g["status"] not in GOAL_SETTLED]
        if mine:
            lines.append("Your open goals: " + "; ".join(g["title"] for g in mine[:8]))
    learned = sorted(team["learnings"], key=lambda x: (x.get("count", 1), x.get("last_at", 0)), reverse=True)
    if learned:
        lines.append("What this team has learned:")
        lines.extend(f"- {x['text']}" for x in learned[:max_learnings])
    return "\n".join(lines)


def export_pack(team: dict[str, Any]) -> dict[str, Any]:
    slot_of = {m["slot"]: f"s{i + 1}" for i, m in enumerate(team["members"])}
    seats = []
    for m in team["members"]:
        seats.append({
            "slot": slot_of[m["slot"]], "role": m.get("role", ""), "title": m.get("title", ""),
            "reports_to_slot": slot_of.get(m.get("reports_to")), "lead": bool(m.get("lead")),
            "skills": list(m.get("skills", [])), "plugins": list(m.get("plugins", [])),
            "credentials": list(m.get("credentials", [])),
            "monthly_usd": m["budget"].get("monthly_usd"), "hard_stop": m["budget"].get("hard_stop", True),
        })
    return {"pack_version": PACK_VERSION, "name": team["name"], "mission": team.get("mission", ""),
            "policy": {"lead_decides": bool(team["policy"].get("lead_decides"))}, "seats": seats}


def pack_seats(pack: Any) -> tuple[str, str, bool, list[dict[str, Any]]]:
    """Validate a pack and return (name, mission, lead_decides, seats). Unknown keys are ignored."""
    if not isinstance(pack, dict) or pack.get("pack_version") != PACK_VERSION:
        raise TeamError("unsupported team pack")
    seats = pack.get("seats")
    if not isinstance(seats, list) or not 1 <= len(seats) <= PACK_MAX_SEATS:
        raise TeamError(f"team pack needs 1–{PACK_MAX_SEATS} seats")
    clean = [{k: raw.get(k) for k in PACK_MEMBER_KEYS if k in raw} for raw in seats if isinstance(raw, dict)]
    return (clean_text(pack.get("name"), NAME_MAX), clean_text(pack.get("mission"), MISSION_MAX),
            bool((pack.get("policy") or {}).get("lead_decides")), clean)


def spawn_brief(team: dict[str, Any], goal_id: str, ref: Optional[str], *, title: str, body: str,
                ) -> tuple[str, str, str]:
    clean_title = clean_text(title, 200)
    if not clean_title:
        raise TeamError("task title required")
    goal = find_goal(team, goal_id)
    member = find_member(team, ref or goal.get("owner") or "")
    if member is None:
        raise TeamError("pick a teammate to assign this to (the goal has no owner yet)")
    if not member.get("profile"):
        raise TeamError("that seat is open — hire a bot into it first")
    text = build_brief(team, member["slot"], goal_id=goal_id, include_goals=False)
    text += f"\n\n## Task\n{clean_title}" + (f"\n\n{clean_text(body, TEXT_MAX)}" if body.strip() else "")
    slug = _NORM_RE.sub("-", clean_title.lower())[:80]
    return member["profile"], text, f"{team['id']}:{goal_id}:{slug}"
