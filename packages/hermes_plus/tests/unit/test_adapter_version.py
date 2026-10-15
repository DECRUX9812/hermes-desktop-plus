from __future__ import annotations

import sys
from pathlib import Path

from hermes_plus.adapter.version import _python_from_launcher, parse_version


def test_parse_version_and_upstream_suffix() -> None:
    version, parsed = parse_version(
        "Hermes Agent v0.21.6+183.g8ac5c74.dirty (2026.9.24) · upstream 0e427d78"
    )

    assert version == "0.21.6"
    assert parsed == (0, 21, 6)


def test_parse_version_does_not_mistake_a_date_for_a_release() -> None:
    assert parse_version("Hermes Agent build (2026.9.24)") == (None, None)


def test_parse_git_build_without_inventing_a_semver_release() -> None:
    assert parse_version("Hermes Agent vgit.379cf12 (2026.9.24)") == (None, None)


def test_console_script_shebang_resolves_python(tmp_path) -> None:
    launcher = tmp_path / "hermes"
    launcher.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n", encoding="utf-8")

    assert _python_from_launcher(str(launcher)) == str(Path(sys.executable).resolve())


def test_shell_launcher_resolves_exec_interpreter_not_shell(tmp_path) -> None:
    launcher = tmp_path / "hermes"
    launcher.write_text(
        f"#!/bin/sh\nexec {sys.executable} -I -c 'print(1)' \"$@\"\n",
        encoding="utf-8",
    )

    assert _python_from_launcher(str(launcher)) == str(Path(sys.executable).resolve())
