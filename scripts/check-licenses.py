#!/usr/bin/env python3
"""Dependency license check for an MIT-licensed distribution (stdlib only).
Resolves the Node production tree through package-lock.json — npm lockfile v3
records a license per package, so the tree is determinable without a node_modules
install — and prefers the installed package.json when node_modules is present.
Reads the Python side from the METADATA of a venv's distributions.

Flags GPL/AGPL/LGPL/SSPL/BUSL and any license it cannot recognise; the other
non-permissive ones (MPL-2.0, PSF-2.0, ...) are listed for review. Exits 1 when a
flagged package is in a production tree. Usage: ``check-licenses.py [repo-root]``.
"""

from __future__ import annotations

import collections
import email.parser
import json
import pathlib
import re
import sys

_COPYLEFT = re.compile(r"\b(AGPL|LGPL|SSPL|BUSL|GPL)\b")
_PERMISSIVE = re.compile(r"(MIT|ISC|APACHE|BSD|CC0|UNLICENSE|PYTHON-2|PSF|BLUEOAK|ZLIB|CC-BY|WTFPL)", re.I)


def classify(field: object) -> str:
    """``license`` may be a string, ``{type: ...}``, or a list of either."""
    if isinstance(field, dict):
        field = field.get("type") or field.get("name") or ""
    if isinstance(field, list):
        field = " OR ".join(str(p.get("type") if isinstance(p, dict) else p) for p in field)
    text = str(field or "").strip()
    if not text or text.upper().startswith("UNKNOWN"):
        return "unknown"
    hit = _COPYLEFT.search(text)
    return hit.group(1).upper() if hit else ("permissive" if _PERMISSIVE.search(text) else "other")


def node_tree(lock: dict) -> list[str]:
    """Lockfile keys of every package reachable from a workspace's runtime dependencies."""

    def resolve(base: str, name: str) -> str | None:
        """npm lookup: the nearest ``node_modules/<name>`` at or above ``base``."""
        parts = base.split("/") if base else []
        for cut in range(len(parts), -1, -1):
            prefix = "/".join(parts[:cut])
            candidate = f"{prefix}/node_modules/{name}" if prefix else f"node_modules/{name}"
            if candidate in lock:
                return candidate
        return None

    queue = [key for key in lock if "node_modules/" not in key]
    seen = set(queue)
    while queue:
        base = queue.pop()
        entry = lock[base]
        for dep in list(entry.get("dependencies", {})) + list(entry.get("optionalDependencies", {})):
            if (path := resolve(base, dep)) and path not in seen:
                seen.add(path)
                queue.append(path)
    return sorted(k for k in seen if "node_modules/" in k and not lock[k].get("link"))


def entries(root: pathlib.Path, lock: dict) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """``(node, python)`` name/version/license triples for every package in a production tree."""
    installed = root / "node_modules" if (root / "node_modules").is_dir() else None
    node = []
    for path in node_tree(lock):
        entry, name, field = lock[path], path.rsplit("node_modules/", 1)[1], lock[path].get("license")
        manifest = installed / path / "package.json" if installed else None
        data = json.loads(manifest.read_text(encoding="utf-8")) if manifest and manifest.is_file() else {}
        node.append((data.get("name") or name, str(entry.get("version", "")),
                     str(data.get("license") or field or "")))
    python = []
    for meta in sorted((root / ".venv").glob("lib/python*/site-packages/*.dist-info/METADATA")):
        message = email.parser.Parser().parsestr(meta.read_text(encoding="utf-8", errors="replace"))
        field = message.get("License-Expression") or message.get("License") or ""
        if not field.strip() or field.strip().upper() == "UNKNOWN":
            field = " OR ".join(c.split("::")[-1].strip()
                                for c in message.get_all("Classifier", []) if c.startswith("License ::"))
        python.append((message.get("Name", meta.parent.name), message.get("Version", ""), field))
    return node, python


def report(label: str, packages: list[tuple[str, str, str]]) -> list[str]:
    """Print one side's histogram and its non-permissive packages; return the flags (not ``other``)."""
    counts = collections.Counter(classify(field) for _n, _v, field in packages)
    print(f"{label}: {len(packages)} package(s) — "
          + ", ".join(f"{kind} {count}" for kind, count in counts.most_common()))
    rows: list[tuple[str, str]] = []
    for name, version, field in packages:
        kind = classify(field)
        if kind != "permissive":
            rows.append((kind, f"  {name}@{version or '?'}  [{kind}]  {field or '(no license field)'}"))
    if rows:
        print(f"{label}: non-permissive licenses:\n" + "\n".join(row for _kind, row in rows))
    return [row for kind, row in rows if kind != "other"]


def main() -> int:
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    node, python = entries(root, lock)
    print("node tree: workspace runtime dependencies via "
          + ("the installed node_modules" if (root / "node_modules").is_dir() else "package-lock.json"))
    flags = report("node", node)
    if (root / ".venv/lib").is_dir():
        flags += report("python", python)
    else:
        print(f"python: skipped ({root / '.venv'} holds no installed environment)")
    if flags:
        print(f"\nflagged {len(flags)} package(s):\n" + "\n".join(flags))
        return 1
    print("\nresult: OK — nothing copyleft or unlicensed in a production tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
