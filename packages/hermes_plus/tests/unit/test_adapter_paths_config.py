from __future__ import annotations

import pytest

from hermes_plus.adapter import enable_plugin, hermes_home, list_profiles, load_config, plus_home


def test_profiles_are_scoped_to_home_and_only_identity_dirs_listed(tmp_path, monkeypatch) -> None:
    root = tmp_path / "hermes-root"
    monkeypatch.setenv("HERMES_HOME", str(root))
    (root / "profiles" / "demo").mkdir(parents=True)
    (root / "profiles" / "demo" / "config.yaml").write_text("display: {}\n", encoding="utf-8")
    (root / "profiles" / "empty-shell").mkdir()
    (root / "profiles" / "removed").mkdir()
    (root / "profiles" / "removed" / "profile.yaml").write_text("name: removed\n", encoding="utf-8")
    (root / "profiles" / ".deleted").mkdir()
    (root / "profiles" / ".deleted" / "removed").touch()

    assert hermes_home() == root
    assert hermes_home("demo") == root / "profiles" / "demo"
    assert list_profiles() == ["default", "demo"]


def test_profile_home_resolves_custom_root_from_an_active_profile(tmp_path, monkeypatch) -> None:
    profile_home = tmp_path / "custom-root" / "profiles" / "demo"
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    assert hermes_home() == profile_home
    assert hermes_home("default") == tmp_path / "custom-root"
    assert hermes_home("other") == tmp_path / "custom-root" / "profiles" / "other"


def test_config_load_is_read_only_and_missing_config_is_empty(tmp_path, monkeypatch) -> None:
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))

    assert load_config() == {}
    assert not home.exists()
    home.mkdir()
    config_path = home / "config.yaml"
    config_path.write_text("display:\n  tui_theme: dark\n", encoding="utf-8")

    assert load_config() == {"display": {"tui_theme": "dark"}}
    assert config_path.read_text(encoding="utf-8") == "display:\n  tui_theme: dark\n"


def test_plus_home_is_created_on_demand(tmp_path, monkeypatch) -> None:
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))

    assert plus_home() == home / "hermes-plus"
    assert (home / "hermes-plus").is_dir()


def test_enable_plugin_uses_atomic_yaml_fallback_only_without_cli(tmp_path, monkeypatch) -> None:
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("HERMES_PLUS_HERMES_BIN", raising=False)
    home.mkdir()
    (home / "config.yaml").write_text("display:\n  tui_theme: dark\n", encoding="utf-8")

    enable_plugin("hermes-plus-contract-probe")

    config = load_config()
    assert config["display"]["tui_theme"] == "dark"
    assert config["plugins"]["enabled"] == ["hermes-plus-contract-probe"]
    assert not list(home.glob(".config.yaml.*.tmp"))


def test_invalid_profile_name_cannot_escape_profiles_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    with pytest.raises(ValueError):
        hermes_home("../outside")
