from pathlib import Path

from hermes_plus.adapter import HermesInstall, check_compat, known_good_ref_commit


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
    assert check_compat(_install("0.22.2", (0, 22, 2))).status == "supported"
    assert check_compat(_install("0.22.3", (0, 22, 3))).status == "untested"
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


def test_known_good_refs_map_release_tags_to_their_proven_commit() -> None:
    assert known_good_ref_commit("v0.22.1") == "297552a"
    assert known_good_ref_commit("v0.22.2") == "d923a5e"
    assert known_good_ref_commit("v9.9.9") is None
    assert known_good_ref_commit("") is None


def test_proven_release_commits_are_supported_without_a_semver_stamp() -> None:
    # A checkout install at a pinned tag prints no semver; its recovered commit
    # must classify as supported once the ref mapping is in the matrix.
    assert check_compat(_install(None, None, "297552a")).status == "supported"
    assert check_compat(_install(None, None, "297552a9d6")).status == "supported"
    assert check_compat(_install(None, None, "d923a5e")).status == "supported"
    assert check_compat(_install(None, None, "d923a5eb487a17c0a68801bba9ef0f56c81040a8")).status == "supported"
