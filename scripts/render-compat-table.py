#!/usr/bin/env python3
"""Print the README compatibility table from compat/matrix.json."""

import json
import sys
from pathlib import Path

MATRIX = Path(__file__).resolve().parent.parent / "packages/hermes_plus/src/hermes_plus/compat/matrix.json"


def main() -> int:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    rows = [
        ("Adapter API", f"`{matrix['adapter_api']}`", "Wire contract between the sidecar and the adapter."),
        ("Minimum", f"`{matrix['min']}`", "Below this, `serve` refuses and `doctor` explains the fix."),
        (
            "Known good releases",
            ", ".join(f"`{v}`" for v in matrix["known_good"]),
            "Full feature set, contract-tested in CI.",
        ),
        (
            "Known good commits",
            ", ".join(f"`{c}`" for c in matrix["known_good_commits"]),
            "Abbreviated upstream shas accepted as known-good.",
        ),
        ("Tested main", f"`{matrix['tested_main']}`", "The upstream main commit nightly tracks."),
        ("Newest tested minor", f"`{matrix['max_minor']}`", "A newer minor is served with a banner, features gated on capability probes."),
    ]
    print("| Field | Value | Meaning |")
    print("| --- | --- | --- |")
    for field, value, meaning in rows:
        print(f"| {field} | {value} | {meaning} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
