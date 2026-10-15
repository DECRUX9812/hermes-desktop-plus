from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hermes_plus.store import ApprovalJournal, TeamStore, import_fork_data


def _fork_state(home: Path) -> None:
    conn = sqlite3.connect(home / "state.db")
    conn.execute("CREATE TABLE fork_approvals (request_id TEXT PRIMARY KEY, session_id TEXT, tool_call_id TEXT, "
                 "tool_name TEXT, command TEXT, description TEXT, created_at REAL, status TEXT, decided_at REAL, "
                 "choice TEXT, ran_at REAL, reason TEXT)")
    conn.execute("INSERT INTO fork_approvals VALUES ('req_ok', 'sess_1', 'call_1', 'shell', 'ls', 'list', "
                 "1.0, 'allowed', 2.0, 'session', 3.0, '')")
    conn.execute("INSERT INTO fork_approvals VALUES ('req_bad', 'sess_1', 'call_2', 'shell', 'rm', 'remove', "
                 "1.0, 'bogus', NULL, NULL, NULL, '')")
    conn.commit()
    conn.close()


def _team_files(home: Path) -> None:
    root = home / "bot-teams"
    (root / "teams").mkdir(parents=True)
    (root / "audit").mkdir()
    doc = {
        "id": "alpha_team", "name": "Alpha", "mission": "Ship the demo", "policy": {"lead_decides": False},
        "channels": {}, "members": [{"slot": "seat_1", "profile": "demo", "role": "lead", "lead": True}],
        "goals": [], "approvals": [], "learnings": [], "created_at": 1, "updated_at": 1,
    }
    (root / "teams" / "alpha_team.json").write_text(json.dumps(doc), encoding="utf-8")
    (root / "teams" / "broken.json").write_text(json.dumps({"id": "broken"}), encoding="utf-8")
    audit = {"at": 1, "actor": "you", "action": "team.create", "detail": {"name": "Alpha"}}
    (root / "audit" / "alpha_team.jsonl").write_text(json.dumps(audit) + "\nnot json\n", encoding="utf-8")


def _snapshot(home: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}


def test_import_copies_fork_state_once_and_leaves_source_untouched(tmp_path, store):
    home = tmp_path / "fork-home"
    home.mkdir()
    _fork_state(home)
    _team_files(home)
    before = _snapshot(home)

    first = import_fork_data(home, store)
    assert first == {
        "approvals_imported": 1, "approvals_existing": 0, "approvals_bad": 1,
        "teams_imported": 1, "teams_existing": 0, "team_files_bad": 1, "audit_rows_imported": 1,
    }
    assert ApprovalJournal(store).get("req_ok")["status"] == "allowed"
    assert TeamStore(store).get("alpha_team")["name"] == "Alpha"
    assert len(TeamStore(store).audit_list("alpha_team")) == 1

    second = import_fork_data(home, store)
    assert second["approvals_existing"] == 1
    assert second["teams_existing"] == 1
    assert second["audit_rows_imported"] == 0
    assert _snapshot(home) == before


def test_import_from_an_empty_home_is_a_no_op(tmp_path, store):
    home = tmp_path / "empty-home"
    home.mkdir()
    assert all(value == 0 for value in import_fork_data(home, store).values())
