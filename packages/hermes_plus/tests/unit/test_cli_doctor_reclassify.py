"""Doctor's compat re-classification: when the adapter's sandboxed probe comes
back with no parsed version (a cold runtime can time it out) but the direct
`hermes --version` fallback DID print a parsable stamp, doctor re-runs the
compatibility check from that raw text instead of warning "could not parse".
"""

from __future__ import annotations

from pathlib import Path

from hermes_plus.adapter.version import HermesInstall
from hermes_plus.cli import _reclassify_compat_from_raw

# Real captured `hermes --version` output (check-nous-release.sh's wrapper, whose
# probe can time out on a cold runtime while the direct call succeeds).
RAW_STAMP = (
    "Hermes Agent v0.22.4 (2026.9.24) · stable · local 4d04303818b6\n"
    "Install directory: /opt/demo/hermes-stock\n"
    "Install method: git\n"
    "Python: 3.14.7\n"
    "Up to date"
)


def _install(tmp_path: Path) -> HermesInstall:
    return HermesInstall(
        hermes_bin="/opt/demo/bin/hermes",
        python=None,
        version=None,  # the probe came back empty
        version_tuple=None,
        upstream_commit=None,
        hermes_home=tmp_path,
    )


def test_unparsed_probe_recovers_the_version_from_the_raw_stamp(tmp_path) -> None:
    compat = _reclassify_compat_from_raw(_install(tmp_path), RAW_STAMP)

    assert compat is not None
    assert compat.status in {"supported", "untested"}
    assert "0.22.4" in compat.message
    assert "Could not parse" not in compat.message


def test_a_known_good_version_classifies_supported(tmp_path) -> None:
    compat = _reclassify_compat_from_raw(_install(tmp_path), "Hermes Agent v0.22.1 (2026.9.24)")

    assert compat is not None
    assert compat.status == "supported"


def test_unparsable_raw_output_stays_none(tmp_path) -> None:
    assert _reclassify_compat_from_raw(_install(tmp_path), "Hermes Agent vgit.379cf12 (2026.9.24)") is None
    assert _reclassify_compat_from_raw(_install(tmp_path), "") is None
