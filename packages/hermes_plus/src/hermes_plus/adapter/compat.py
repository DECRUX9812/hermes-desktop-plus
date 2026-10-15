"""Compatibility checks against the packaged upstream matrix."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal

from hermes_plus.adapter.version import HermesInstall

_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class Compat:
    status: Literal["supported", "untested", "unsupported"]
    message: str


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(value)
    return tuple(int(part) for part in match.groups()) if match else None


def _same_commit(left: str, right: str) -> bool:
    """Match abbreviated commit forms of the same upstream sha (7+ hex chars)."""
    left, right = left.strip().lower(), right.strip().lower()
    return (
        len(left) >= 7 and len(right) >= 7
        and (left.startswith(right) or right.startswith(left))
    )


def _matrix() -> dict:
    raw = files("hermes_plus").joinpath("compat/matrix.json").read_text(encoding="utf-8")
    matrix = json.loads(raw)
    if not isinstance(matrix, dict):
        raise ValueError("Hermes compatibility matrix must be a JSON object")
    return matrix


def check_compat(inst: HermesInstall) -> Compat:
    """Classify an installation using its parsed version and the packaged matrix."""
    try:
        matrix = _matrix()
        minimum = _version_tuple(str(matrix["min"]))
        known_good = {str(item) for item in matrix["known_good"]}
        known_good_commits = [str(item) for item in matrix.get("known_good_commits", [])]
    except (OSError, KeyError, TypeError, ValueError):
        return Compat("untested", "Hermes compatibility matrix is unavailable or invalid.")
    if minimum is None:
        return Compat("untested", "Hermes compatibility matrix has an invalid minimum version.")
    tested_main = matrix.get("tested_main")
    if inst.upstream_commit:
        commit_allowlist = [str(tested_main)] if tested_main else []
        commit_allowlist.extend(known_good_commits)
        for candidate in commit_allowlist:
            if _same_commit(inst.upstream_commit, candidate):
                return Compat(
                    "supported",
                    f"Hermes commit {inst.upstream_commit} is a known-good upstream commit.",
                )
    if inst.version_tuple is None or inst.version is None:
        return Compat("untested", "Could not parse the installed Hermes version.")
    if inst.version_tuple < minimum:
        return Compat(
            "unsupported",
            f"Hermes {inst.version} is older than the supported minimum {matrix['min']}.",
        )
    if inst.version in known_good:
        return Compat("supported", f"Hermes {inst.version} is in the tested compatibility matrix.")
    return Compat("untested", f"Hermes {inst.version} is not listed as a tested version.")
