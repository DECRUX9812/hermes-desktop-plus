"""Tests for ServeSettings validation and environment variable mapping."""

import argparse
import os
from pathlib import Path

import pytest
from hermes_plus.settings import ServeSettings


def test_default_settings_valid() -> None:
    settings = ServeSettings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 9129
    assert settings.auth == "token"
    assert settings.validate() is settings


def test_auth_none_loopback_allowed() -> None:
    # Loopback hosts must be allowed with auth='none'
    for loopback in ["127.0.0.1", "localhost", "::1", "127.0.0.5"]:
        s = ServeSettings(host=loopback, auth="none")
        assert s.validate() is s


def test_auth_none_non_loopback_refused() -> None:
    # Non-loopback hosts must be refused with auth='none'
    for external in ["0.0.0.0", "192.168.1.100", "example.com", "::"]:
        s = ServeSettings(host=external, auth="none")
        with pytest.raises(ValueError, match="Refusing auth='none' on non-loopback host"):
            s.validate()


def test_auth_password_requires_password_env_or_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("HERMES_PLUS_PASSWORD", raising=False)
    s = ServeSettings(auth="password", password_file=None)
    with pytest.raises(ValueError, match="auth='password' requires a password file"):
        s.validate()

    # With HERMES_PLUS_PASSWORD in env, it validates
    monkeypatch.setenv("HERMES_PLUS_PASSWORD", "secret123")
    assert s.validate() is s

    # With non-existent password file
    monkeypatch.delenv("HERMES_PLUS_PASSWORD", raising=False)
    non_existent = tmp_path / "missing.txt"
    s2 = ServeSettings(auth="password", password_file=non_existent)
    with pytest.raises(ValueError, match="Password file does not exist"):
        s2.validate()

    # With existing password file
    pwd_file = tmp_path / "pwd.txt"
    pwd_file.write_text("file-secret\n")
    s3 = ServeSettings(auth="password", password_file=pwd_file)
    assert s3.validate() is s3


def test_invalid_auth_mode() -> None:
    s = ServeSettings(auth="invalid")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Invalid auth mode"):
        s.validate()


def test_invalid_port() -> None:
    with pytest.raises(ValueError, match="Port must be between 1 and 65535"):
        ServeSettings(port=0).validate()

    with pytest.raises(ValueError, match="Port must be between 1 and 65535"):
        ServeSettings(port=70000).validate()


def test_upstream_token_file_validation(tmp_path: Path) -> None:
    token_file = tmp_path / "token.txt"
    s_missing = ServeSettings(upstream_token_file=token_file)
    with pytest.raises(ValueError, match="Upstream token file does not exist"):
        s_missing.validate()

    token_file.write_text("token-abc")
    s_present = ServeSettings(upstream_token_file=token_file)
    assert s_present.validate() is s_present


def test_env_var_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HERMES_PLUS_HOST", "127.0.0.2")
    monkeypatch.setenv("HERMES_PLUS_PORT", "9500")
    monkeypatch.setenv("HERMES_PLUS_PROFILE", "prod")
    monkeypatch.setenv("HERMES_PLUS_HERMES_BIN", "/opt/hermes/bin/hermes")
    monkeypatch.setenv("HERMES_PLUS_UPSTREAM_URL", "http://127.0.0.1:8000")
    token_path = tmp_path / "up_token.txt"
    token_path.write_text("xyz")
    monkeypatch.setenv("HERMES_PLUS_UPSTREAM_TOKEN_FILE", str(token_path))
    monkeypatch.setenv("HERMES_PLUS_AUTH", "password")
    pwd_path = tmp_path / "pwd.txt"
    pwd_path.write_text("secret")
    monkeypatch.setenv("HERMES_PLUS_PASSWORD_FILE", str(pwd_path))
    monkeypatch.setenv("HERMES_PLUS_TRUST_PROXY_HEADERS", "1")
    ui_dir = tmp_path / "custom_ui"
    ui_dir.mkdir()
    monkeypatch.setenv("HERMES_PLUS_UI_DIR", str(ui_dir))
    monkeypatch.setenv("HERMES_PLUS_DEV_UI_URL", "http://localhost:5173")
    monkeypatch.setenv("HERMES_PLUS_BASE_PATH", "/hermes")
    monkeypatch.setenv("HERMES_PLUS_LOG_LEVEL", "debug")

    settings = ServeSettings.from_env_and_args()
    assert settings.host == "127.0.0.2"
    assert settings.port == 9500
    assert settings.profile == "prod"
    assert settings.hermes_bin == "/opt/hermes/bin/hermes"
    assert settings.upstream_url == "http://127.0.0.1:8000"
    assert settings.upstream_token_file == token_path
    assert settings.auth == "password"
    assert settings.password_file == pwd_path
    assert settings.trust_proxy_headers is True
    assert settings.ui_dir == ui_dir
    assert settings.dev_ui_url == "http://localhost:5173"
    assert settings.base_path == "/hermes"
    assert settings.log_level == "debug"
    assert settings.validate() is settings


def test_cli_args_precedence_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PLUS_PORT", "9500")
    monkeypatch.setenv("HERMES_PLUS_HOST", "127.0.0.2")

    args = argparse.Namespace(
        port=9999,
        host="127.0.0.3",
        profile=None,
        hermes_bin=None,
        upstream_url=None,
        upstream_token_file=None,
        auth=None,
        password_file=None,
        trust_proxy_headers=None,
        ui_dir=None,
        dev_ui_url=None,
        base_path=None,
        log_level=None,
    )

    settings = ServeSettings.from_env_and_args(args)
    assert settings.port == 9999
    assert settings.host == "127.0.0.3"
