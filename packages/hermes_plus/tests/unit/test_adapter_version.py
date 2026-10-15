from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_plus.adapter.version import (
    _probe_environment,
    _python_from_launcher,
    commit_from_install_dir,
    install_directory,
    parse_version,
    parse_version_output,
)

# Real captured `hermes --version` outputs from the stock installs on the test machine.
GIT_STAMP_OUTPUT = "Hermes Agent vgit.379cf12 (2026.9.24)"
UNKNOWN_STAMP_OUTPUT = (
    "Hermes Agent vunknown (2026.9.24)\n"
    "Install directory: /home/demo/ship-test/sh/installs/a2caf6ebe0bf88e1/environments/"
    "c7a5974d86884d46b8f78718ac7522ad/workspace\n"
    "Install method: unknown\n"
)
SEMVER_OUTPUT = "Hermes Agent v0.22.1 (2026.9.24) · upstream 8ac5c74"


def test_parse_version_and_upstream_suffix() -> None:
    version, parsed = parse_version(SEMVER_OUTPUT)

    assert version == "0.22.1"
    assert parsed == (0, 22, 1)


def test_parse_version_does_not_mistake_a_date_for_a_release() -> None:
    assert parse_version("Hermes Agent build (2026.9.24)") == (None, None)


def test_parse_git_build_without_inventing_a_semver_release() -> None:
    assert parse_version("Hermes Agent vgit.379cf12 (2026.9.24)") == (None, None)


def test_parse_version_output_reads_all_three_stamp_formats() -> None:
    assert parse_version_output(GIT_STAMP_OUTPUT) == (None, None, "379cf12")
    assert parse_version_output(UNKNOWN_STAMP_OUTPUT) == (None, None, None)
    assert parse_version_output(SEMVER_OUTPUT) == ("0.22.1", (0, 22, 1), "8ac5c74")


def test_install_directory_is_extracted_from_the_vunknown_stamp() -> None:
    assert install_directory(UNKNOWN_STAMP_OUTPUT) == (
        "/home/demo/ship-test/sh/installs/a2caf6ebe0bf88e1/environments/"
        "c7a5974d86884d46b8f78718ac7522ad/workspace"
    )
    assert install_directory(GIT_STAMP_OUTPUT) is None


def test_commit_is_recovered_from_the_owning_checkout_of_an_install_dir(tmp_path) -> None:
    checkout = tmp_path / "sh" / "hermes-agent"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(checkout), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, timeout=60)
    expected = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--short", "HEAD"],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    workspace = (
        tmp_path / "sh" / "installs" / "a2caf6ebe0bf88e1" / "environments"
        / "c7a5974d86884d46b8f78718ac7522ad" / "workspace"
    )
    workspace.mkdir(parents=True)

    assert commit_from_install_dir(workspace) == expected


def test_commit_recovery_returns_none_without_any_owning_checkout(tmp_path) -> None:
    workspace = tmp_path / "sh" / "installs" / "demo" / "environments" / "gen" / "workspace"
    workspace.mkdir(parents=True)

    assert commit_from_install_dir(workspace) is None


def test_probe_environment_scrubs_hermes_vars_and_uses_a_fresh_home(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", "/home/demo/stale-home")
    monkeypatch.setenv("HERMES_WEB_DIST", "/home/demo/stale-dist")
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "stale-token")
    monkeypatch.setenv("PATH", "/usr/bin")

    env, probe_home = _probe_environment()
    try:
        assert env["PATH"] == "/usr/bin"
        assert not [key for key in env if key.startswith("HERMES_") and key != "HERMES_HOME"]
        assert env["HERMES_HOME"] == probe_home.name
        assert env["HERMES_HOME"] != "/home/demo/stale-home"
        assert Path(probe_home.name).is_dir()
    finally:
        probe_home.cleanup()
    assert not Path(probe_home.name).exists()


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


def test_polyglot_pm_launcher_resolves_sibling_python3(tmp_path) -> None:
    launcher = tmp_path / "hermes"
    launcher.write_text(
        "#!/bin/sh\n"
        "'''exec' \"$(dirname -- \"$(realpath -- \"$0\")\")\"/'python3' \"$0\" \"$@\"\n"
        "' '''\n"
        "# -*- coding: utf-8 -*-\n"
        "import sys\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    (tmp_path / "python3").write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
    (tmp_path / "python3").chmod(0o755)

    resolved = _python_from_launcher(str(launcher))

    assert resolved == str(tmp_path / "python3")
    assert os.access(resolved, os.X_OK)
