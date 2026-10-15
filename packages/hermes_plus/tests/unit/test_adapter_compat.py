from pathlib import Path

from hermes_plus.adapter import HermesInstall, check_compat


def _install(version: str | None, parsed: tuple[int, int, int] | None) -> HermesInstall:
    return HermesInstall(
        hermes_bin="hermes",
        python=None,
        version=version,
        version_tuple=parsed,
        upstream_commit=None,
        hermes_home=Path("/tmp/hermes-home"),
    )


def test_compat_matrix_classifies_minimum_known_and_old_versions() -> None:
    assert check_compat(_install("0.22.0", (0, 22, 0))).status == "supported"
    assert check_compat(_install("0.22.2", (0, 22, 2))).status == "untested"
    assert check_compat(_install("0.21.9", (0, 21, 9))).status == "unsupported"
    assert check_compat(_install(None, None)).status == "untested"
