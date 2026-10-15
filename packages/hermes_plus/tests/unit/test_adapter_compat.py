from pathlib import Path

from hermes_plus.adapter import HermesInstall, check_compat


def _install(version: str | None, parsed: tuple[int, int, int] | None, commit: str | None = None) -> HermesInstall:
    return HermesInstall(
        hermes_bin="hermes",
        python=None,
        version=version,
        version_tuple=parsed,
        upstream_commit=commit,
        hermes_home=Path("/tmp/hermes-home"),
    )


def test_compat_matrix_classifies_minimum_known_and_old_versions() -> None:
    assert check_compat(_install("0.22.0", (0, 22, 0))).status == "supported"
    assert check_compat(_install("0.22.2", (0, 22, 2))).status == "untested"
    assert check_compat(_install("0.21.9", (0, 21, 9))).status == "unsupported"
    assert check_compat(_install(None, None)).status == "untested"


def test_known_good_commit_is_supported_even_without_a_semver_stamp() -> None:
    assert check_compat(_install(None, None, "379cf12")).status == "supported"
    assert check_compat(_install(None, None, "379cf122")).status == "supported"


def test_unknown_commit_degrades_to_untested_but_keeps_running() -> None:
    assert check_compat(_install(None, None, "deadbeef")).status == "untested"
    assert check_compat(_install(None, None, "deadbeef00000000000000000000000000000000")).status == "untested"


def test_old_semver_is_unsupported_even_with_an_unknown_commit() -> None:
    assert check_compat(_install("0.21.6", (0, 21, 6), "deadbeef")).status == "unsupported"
