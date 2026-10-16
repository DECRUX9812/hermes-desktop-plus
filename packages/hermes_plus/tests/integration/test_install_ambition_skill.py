"""The bundled ambition skill: packaged with hermes-plus, installed into a temp HERMES_HOME,
and listed by stock Hermes' own ``hermes skills list``. Real subprocesses, no mocks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from hermes_resolve import resolve_stock_hermes
from support import share_installs

REPO_ROOT = Path(__file__).resolve().parents[4]
SHIPPED_SKILL_DIR = (
    REPO_ROOT / "packages" / "hermes_plus" / "src" / "hermes_plus"
    / "extras" / "skills" / "ambition"
)
HERMES_PLUS_BIN = REPO_ROOT / ".venv" / "bin" / "hermes-plus"
STOCK_HERMES = resolve_stock_hermes()[0]

pytestmark = pytest.mark.integration


def _require_tools() -> None:
    if not STOCK_HERMES.is_file():
        pytest.skip(f"stock Hermes not found at {STOCK_HERMES} (set HERMES_PLUS_TEST_HERMES)")
    if not HERMES_PLUS_BIN.is_file():
        pytest.skip("hermes-plus is not installed in the worktree venv (.venv/bin/hermes-plus)")


def _env(home: Path) -> dict[str, str]:
    """Bind the temp home and pin the stock binary.

    ``HERMES_PLUS_HERMES_BIN`` matters: the adapter resolves ``hermes`` from it or from PATH, and
    a developer machine usually has a different ``hermes`` (a real install) first on PATH.
    """
    return {**os.environ, "HERMES_HOME": str(home), "HERMES_PLUS_HERMES_BIN": str(STOCK_HERMES)}


def _install(home: Path) -> subprocess.CompletedProcess[str]:
    # --force: this stock test binary predates the compatibility matrix minimum; the test is
    # about the bundled skill, not about compat gating.
    return subprocess.run(
        [str(HERMES_PLUS_BIN), "install", "--hermes", str(STOCK_HERMES), "--force"],
        env=_env(home),
        capture_output=True,
        text=True,
        timeout=600,
    )


def _skills_list(home: Path) -> str:
    result = subprocess.run(
        [str(STOCK_HERMES), "skills", "list"],
        env=_env(home),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"'hermes skills list' failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout


def _row_for(listing: str, name: str) -> list[str] | None:
    """Cells of the table row whose first cell is *name*, or None when it is not listed."""
    for line in listing.splitlines():
        if "│" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("│").split("│")]
        if cells and cells[0] == name:
            return cells
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shipped_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def installed_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real ``hermes-plus install`` into a temp HERMES_HOME (cold homes take ~40s)."""
    _require_tools()
    home = tmp_path_factory.mktemp("ambition-home")
    share_installs(home)
    result = _install(home)
    assert result.returncode == 0, (
        f"hermes-plus install failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return home


def test_install_copies_the_shipped_skill_verbatim(installed_home: Path) -> None:
    installed = installed_home / "skills" / "ambition"
    assert installed.is_dir(), f"ambition skill missing at {installed}"

    # /ambition resolves through the frontmatter name, so the installed copy must be the
    # shipped one byte for byte: frontmatter, body and the whole references/ tree.
    assert _shipped_hashes(installed) == _shipped_hashes(SHIPPED_SKILL_DIR), (
        "the installed skill differs from the packaged one"
    )
    skill_md = (installed / "SKILL.md").read_text(encoding="utf-8")
    assert "name: ambition" in skill_md
    assert "Altitude" in skill_md and "Depth" in skill_md and "Range" in skill_md
    assert (installed / "references" / "crew-catalog.md").is_file()
    assert (installed / "references" / "judges.md").is_file()


def test_stock_hermes_lists_the_ambition_skill(installed_home: Path) -> None:
    row = _row_for(_skills_list(installed_home), "ambition")
    assert row is not None, "'ambition' is missing from `hermes skills list`"
    assert row[-1] == "enabled", f"ambition is not enabled in stock Hermes: {row}"


def test_ledger_records_the_shipped_hashes(installed_home: Path) -> None:
    ledger = json.loads(
        (installed_home / "hermes-plus" / "installed-extras.json").read_text(encoding="utf-8")
    )
    assert ledger["skills"]["ambition"] == _shipped_hashes(SHIPPED_SKILL_DIR)


def test_user_modified_skill_survives_repeated_installs(tmp_path: Path) -> None:
    """A local edit is kept, and stays kept: the ledger keeps the shipped baseline."""
    _require_tools()
    home = tmp_path / "hermes-home"
    share_installs(home)

    first = _install(home)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "Installed skill 'ambition'." in first.stdout

    skill_md = home / "skills" / "ambition" / "SKILL.md"
    marker = "\n<!-- local note: keep me -->\n"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + marker, encoding="utf-8")

    for attempt in range(2):
        again = _install(home)
        assert again.returncode == 0, again.stdout + again.stderr
        assert "Kept user-modified skill 'ambition'." in again.stdout, (
            f"install {attempt + 2} did not report keeping the local copy:\n{again.stdout}"
        )
        assert skill_md.read_text(encoding="utf-8").endswith(marker), (
            f"install {attempt + 2} overwrote the user's copy"
        )

    # The ledger must still hold the shipped baseline: recording the user's hash would make the
    # next install compare equal and overwrite their copy.
    ledger = json.loads((home / "hermes-plus" / "installed-extras.json").read_text(encoding="utf-8"))
    assert ledger["skills"]["ambition"]["SKILL.md"] == _sha256(SHIPPED_SKILL_DIR / "SKILL.md")
