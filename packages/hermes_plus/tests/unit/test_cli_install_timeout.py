"""Wording of the install budget-overrun error."""

from __future__ import annotations

from hermes_plus.cli import INSTALL_TIMEOUT_ENV, _install_timeout_message


def test_overrun_message_names_command_budget_and_remedies() -> None:
    message = _install_timeout_message(
        ["/opt/stock/bin/hermes", "plugins", "enable", "hermes-plus"], 900
    )
    assert "'hermes plugins enable hermes-plus' did not finish within 900 s" in message
    assert "Re-run 'hermes-plus install' (it is safe to repeat)" in message
    assert f"{INSTALL_TIMEOUT_ENV}=<seconds>" in message
    assert "\n" not in message
