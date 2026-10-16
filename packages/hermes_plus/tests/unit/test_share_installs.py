"""`share_installs` is opt-in and refuses a value it cannot use.

The variable is test-only (see CONTRIBUTING.md): unset it must create nothing at
all, and a value that is not a provisioned home must fail loudly instead of
silently letting the run pay stock's 387-645s per-home dependency build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from support import SHARED_INSTALLS_ENV, share_installs


@pytest.fixture
def unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SHARED_INSTALLS_ENV, raising=False)


def test_unset_creates_nothing(tmp_path: Path, unset: None) -> None:
    home = tmp_path / "home"
    assert share_installs(home) is None
    assert not home.exists()


def test_blank_value_is_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SHARED_INSTALLS_ENV, "   ")
    home = tmp_path / "home"
    assert share_installs(home) is None
    assert not home.exists()


def test_missing_directory_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "no-such-home"
    monkeypatch.setenv(SHARED_INSTALLS_ENV, str(missing))
    with pytest.raises(pytest.fail.Exception, match="is not a directory"):
        share_installs(tmp_path / "home")


def test_directory_without_installs_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provisioned = tmp_path / "provisioned"
    provisioned.mkdir()
    monkeypatch.setenv(SHARED_INSTALLS_ENV, str(provisioned))
    with pytest.raises(pytest.fail.Exception, match="has no installs/ directory"):
        share_installs(tmp_path / "home")


def test_links_the_provisioned_installs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provisioned = tmp_path / "provisioned"
    (provisioned / "installs" / "abc123").mkdir(parents=True)
    monkeypatch.setenv(SHARED_INSTALLS_ENV, str(provisioned))
    home = tmp_path / "home"

    assert share_installs(home) == provisioned / "installs"
    assert (home / "installs").is_symlink()
    assert (home / "installs" / "abc123").is_dir()
    # A fixture that runs twice against one home must not fail the second time.
    assert share_installs(home) == provisioned / "installs"


def test_a_home_with_its_own_installs_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provisioned = tmp_path / "provisioned"
    (provisioned / "installs").mkdir(parents=True)
    monkeypatch.setenv(SHARED_INSTALLS_ENV, str(provisioned))
    home = tmp_path / "home"
    (home / "installs").mkdir(parents=True)

    with pytest.raises(pytest.fail.Exception, match="needs a fresh home"):
        share_installs(home)


def test_a_home_linked_elsewhere_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provisioned = tmp_path / "provisioned"
    (provisioned / "installs").mkdir(parents=True)
    other = tmp_path / "other-installs"
    other.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / "installs").symlink_to(other)
    monkeypatch.setenv(SHARED_INSTALLS_ENV, str(provisioned))

    with pytest.raises(pytest.fail.Exception, match="already points at"):
        share_installs(home)
