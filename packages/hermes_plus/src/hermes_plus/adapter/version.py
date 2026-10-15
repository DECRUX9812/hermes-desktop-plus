"""Hermes executable and version discovery."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hermes_plus.adapter.paths import hermes_home

_VERSION_PATTERNS = (
    re.compile(r"\bHermes(?:\s+Agent)?\s+(?:version\s+)?[vV]?(\d+)\.(\d+)\.(\d+)", re.IGNORECASE),
    re.compile(r"^\s*(?:version\s*[:=]?\s*)?[vV]?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?", re.IGNORECASE),
)
_COMMIT_RE = re.compile(
    r"\b(?:upstream|commit)\s*(?:[:=]\s*)?([0-9a-f]{7,40})\b|\bvgit\.([0-9a-f]{7,40})\b",
    re.IGNORECASE,
)
_POLYGLOT_LAUNCHER_PREFIX = """'''exec' "$(dirname -- "$(realpath -- "$0")")"/"""


@dataclass(frozen=True)
class HermesInstall:
    hermes_bin: str
    python: str | None
    version: str | None
    version_tuple: tuple[int, int, int] | None
    upstream_commit: str | None
    hermes_home: Path


def resolve_hermes_bin(hermes_bin: str | None = None) -> str | None:
    """Resolve an explicit binary, the adapter override, or the PATH entry."""
    candidate = hermes_bin or os.environ.get("HERMES_PLUS_HERMES_BIN") or "hermes"
    resolved = shutil.which(candidate)
    if resolved:
        return str(Path(resolved).expanduser().resolve())
    path = Path(candidate).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return str(path.resolve())
    return None


def parse_version(output: str) -> tuple[str | None, tuple[int, int, int] | None]:
    """Extract the first release version from Hermes' human-readable version output."""
    match = next(
        (match for pattern in _VERSION_PATTERNS for line in output.splitlines()
         if (match := pattern.search(line))),
        None,
    )
    if match is None:
        return None, None
    parsed = tuple(int(value) for value in match.groups())
    return ".".join(str(value) for value in parsed), parsed


def _resolve_shebang_python(line: str) -> str | None:
    try:
        words = shlex.split(line[2:].strip())
    except ValueError:
        return None
    if not words:
        return None
    executable = words[0]
    if Path(executable).name == "env":
        words = words[1:]
        if words[:1] == ["-S"]:
            words = shlex.split(" ".join(words[1:]))
        words = [word for word in words if not word.startswith("-")]
        if not words:
            return None
        executable = words[0]
        resolved = shutil.which(executable)
        return str(Path(resolved).resolve()) if resolved else None
    path = Path(executable).expanduser()
    return str(path.resolve()) if path.is_file() else None


def _python_from_launcher(hermes_bin: str) -> str | None:
    """Read Python from a console-script shebang or Hermes' shell wrapper."""
    path = Path(hermes_bin)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    first_line = text.splitlines()[0] if text else ""
    shebang = first_line[2:].strip() if first_line.startswith("#!") else ""
    if shebang and Path(shebang.split()[0]).name not in {"sh", "bash", "dash", "zsh"}:
        python = _resolve_shebang_python(first_line)
        if python:
            return python

    for line in text.splitlines():
        if line.startswith(_POLYGLOT_LAUNCHER_PREFIX):
            name = line.removeprefix(_POLYGLOT_LAUNCHER_PREFIX).split("'")[1]
            candidate = path.resolve().parent / name
            return str(candidate) if candidate.is_file() else None
        if not line.startswith("exec "):
            continue
        lexer = shlex.shlex(line.removeprefix("exec "), posix=True)
        lexer.whitespace_split = True
        try:
            executable = lexer.get_token()
        except ValueError:
            return None
        if not executable:
            return None
        candidate = Path(executable).expanduser()
        return str(candidate.resolve()) if candidate.is_file() else None
    return None


def detect(hermes_bin: str | None = None) -> HermesInstall:
    """Find Hermes, parse its version, and identify the interpreter behind its launcher."""
    binary = resolve_hermes_bin(hermes_bin)
    if binary is None:
        raise FileNotFoundError(
            "Hermes executable not found; set HERMES_PLUS_HERMES_BIN or add hermes to PATH"
        )

    env = os.environ.copy()
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
        )
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    except (OSError, subprocess.SubprocessError):
        output = ""
    version, version_tuple = parse_version(output)
    commit_match = _COMMIT_RE.search(output)
    return HermesInstall(
        hermes_bin=binary,
        python=_python_from_launcher(binary),
        version=version,
        version_tuple=version_tuple,
        upstream_commit=next((group for group in commit_match.groups() if group), None)
        if commit_match else None,
        hermes_home=hermes_home(),
    )
