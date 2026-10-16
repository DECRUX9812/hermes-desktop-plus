"""The personal-data guard run for real against throwaway git repositories (zero mocks)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
GUARD = REPO_ROOT / "scripts" / "check-no-personal-data.sh"
GIT = ["git", "-c", "user.email=test@example.com", "-c", "user.name=test"]


def _make_repo(root: Path, files: dict[str, str]) -> Path:
    repo = root / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(GUARD, repo / "scripts" / GUARD.name)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name, body in files.items():
        (repo / name).write_text(body, encoding="utf-8")
    subprocess.run([*GIT, "-C", str(repo), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return repo


def _run_guard(repo: Path, patterns: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "scripts" / GUARD.name)],
        cwd=repo,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HERMES_PPD_PATTERNS": str(patterns)},
        check=False,
    )


@pytest.fixture
def patterns(tmp_path: Path) -> Path:
    path = tmp_path / "patterns.txt"
    path.write_text(
        "# owner identifiers\nzz-owner-test=>demo\n\nfavoownerortcut\n", encoding="utf-8"
    )
    return path


def test_planted_owner_pattern_fails_with_location_not_value(tmp_path: Path, patterns: Path) -> None:
    repo = _make_repo(tmp_path, {"notes.txt": "first line\nhello ZZ-OWNER-TEST here\n"})
    result = _run_guard(repo, patterns)
    assert result.returncode == 1
    assert "notes.txt:2 (pattern #2)" in result.stderr
    assert "zz-owner-test" not in result.stderr.lower()


def test_allowlisted_public_identifier_passes(tmp_path: Path, patterns: Path) -> None:
    repo = _make_repo(tmp_path, {"notes.txt": "the favoownerortcut binding\n"})
    result = _run_guard(repo, patterns)
    assert result.returncode == 0, result.stderr


def test_invalid_pattern_is_reported_without_its_value(tmp_path: Path) -> None:
    patterns = tmp_path / "bad.txt"
    patterns.write_text("(unclosed=>demo\n", encoding="utf-8")
    repo = _make_repo(tmp_path, {"notes.txt": "plain text\n"})
    result = _run_guard(repo, patterns)
    assert result.returncode == 2
    assert "pattern #2" in result.stderr
    assert "unclosed" not in result.stderr
