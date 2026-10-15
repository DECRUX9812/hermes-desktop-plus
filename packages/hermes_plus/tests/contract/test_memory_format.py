from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from hermes_plus.adapter import memory, run_hermes

pytestmark = pytest.mark.contract


@pytest.fixture
def stock_profile(stock_hermes: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PLUS_HERMES_BIN", stock_hermes)
    created = run_hermes(["profile", "create", "memfmt", "--no-alias"], timeout=180)
    if created.returncode:
        pytest.fail(f"stock profile create failed: {created.stderr.strip() or created.stdout.strip()}")
    return home / "profiles" / "memfmt"


def _stock_json(python: str, profile_dir: Path, code: str) -> dict:
    env = {**os.environ, "HERMES_HOME": str(profile_dir)}
    result = subprocess.run([python, "-c", code], env=env, capture_output=True, text=True, timeout=180, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


READ = """
import json
from tools.memory_tool import load_on_disk_store
s = load_on_disk_store()
print(json.dumps({"memory": s.memory_entries, "user": s.user_entries,
                  "limits": [s.memory_char_limit, s.user_char_limit]}))
"""

WRITE = """
import json
from tools.memory_tool import load_on_disk_store
s = load_on_disk_store()
added = s.add("memory", "Written by the stock memory tool")
replaced = s.replace("memory", "Written by the stock memory tool", "Edited by the stock memory tool")
print(json.dumps({"add": added.get("success"), "replace": replaced.get("success")}))
"""


def test_stock_loader_reads_what_the_adapter_wrote(stock_install, stock_profile: Path) -> None:
    memory.add("memfmt", "memory", "Prefers terse answers")
    memory.add("memfmt", "memory", "Second note")
    memory.add("memfmt", "user", "Works in CET")

    stock = _stock_json(stock_install.python, stock_profile, READ)

    assert stock["memory"] == ["Prefers terse answers", "Second note"]
    assert stock["user"] == ["Works in CET"]
    assert stock["limits"] == [memory.DEFAULT_LIMITS["memory"], memory.DEFAULT_LIMITS["user"]]


def test_adapter_reads_what_the_stock_tool_wrote(stock_install, stock_profile: Path) -> None:
    written = _stock_json(stock_install.python, stock_profile, WRITE)
    assert written == {"add": True, "replace": True}

    snapshot = memory.snapshot("memfmt")

    assert [(e.target, e.text) for e in snapshot["entries"]] == [("memory", "Edited by the stock memory tool")]
    assert snapshot["usage"]["memory"]["used"] == len("Edited by the stock memory tool")
    assert (stock_profile / "memories" / "MEMORY.md").read_text(encoding="utf-8") == "Edited by the stock memory tool"
