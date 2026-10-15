from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _run(plus_home: Path, requests: list[Any]) -> list[dict[str, Any]]:
    stdin = "".join(request if isinstance(request, str) else json.dumps(request) + "\n" for request in requests)
    proc = subprocess.run(
        [sys.executable, "-m", "hermes_plus.rpc", "--plus-home", str(plus_home)],
        input=stdin, capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines()]


def _request(rid: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return body


@pytest.mark.integration
def test_team_lifecycle_over_stdio_survives_restart(tmp_path):
    plus_home = tmp_path / "plus-home"

    first = _run(plus_home, [
        _request(1, "bots_team.create", {"name": "Alpha", "mission": "Ship the demo", "actor": "you"}),
        "{this is not json}\n",
        _request(2, "bots_team.member.upsert", {"team_id": "placeholder", "profile": "lead", "lead": True}),
    ])
    created = first[0]["result"]
    team_id = created["team"]["id"]
    assert first[1]["error"]["code"] == -32700
    assert first[2]["error"]["code"] == 4111

    second = _run(plus_home, [
        _request(3, "bots_team.member.upsert", {"team_id": team_id, "actor": "you", "profile": "lead",
                                               "role": "Lead", "lead": True}),
        _request(4, "bots_team.get", {"team_id": team_id}),
        _request(5, "bots_team.audit.list", {"team_id": team_id}),
    ])
    assert second[0]["result"]["member"]["profile"] == "lead"
    assert second[1]["result"]["team"]["members"][0]["profile"] == "lead"
    assert [entry["action"] for entry in second[2]["result"]["entries"]] == ["member.add", "team.create"]

    restarted = _run(plus_home, [
        _request(6, "bots_team.get", {"team_id": team_id}),
        _request(7, "bots_team.audit.list", {"team_id": team_id}),
        _request(8, "bots_team.list"),
    ])
    assert restarted[0]["result"]["team"]["name"] == "Alpha"
    assert [entry["action"] for entry in restarted[1]["result"]["entries"]] == ["member.add", "team.create"]
    assert [team["id"] for team in restarted[2]["result"]["teams"]] == [team_id]


@pytest.mark.integration
def test_identity_and_memory_persist_across_processes(tmp_path, monkeypatch):
    profile = tmp_path / "hermes" / "profiles" / "demo"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    plus_home = tmp_path / "plus-home"
    _run(plus_home, [
        _request(1, "fork.bot.identity.set", {"profile": "demo", "display_name": "Demo Bot", "role": "Support"}),
        _request(2, "fork.memory.add", {"profile": "demo", "target": "memory", "text": "Keep it short"}),
    ])
    replies = _run(plus_home, [
        _request(3, "fork.bot.identity.get", {"profile": "demo"}),
        _request(4, "fork.memory.list", {"profile": "demo"}),
    ])
    assert replies[0]["result"]["display_name"] == "Demo Bot"
    assert replies[0]["result"]["revision"] == 1
    assert [entry["text"] for entry in replies[1]["result"]["entries"]] == ["Keep it short"]
