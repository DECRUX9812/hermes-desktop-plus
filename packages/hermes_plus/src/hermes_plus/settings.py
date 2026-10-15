"""Configuration settings for hermes-plus server."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


def _is_loopback(host: str) -> bool:
    """Return True if host is a loopback address or hostname."""
    if host.lower() in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback
    except ValueError:
        return False


def _default_ui_dir() -> Path:
    return Path(__file__).resolve().parent / "ui_dist"


@dataclass
class ServeSettings:
    host: str = "127.0.0.1"
    port: int = 9129
    profile: str | None = None
    hermes_bin: str | None = None
    upstream_url: str | None = None
    upstream_token_file: Path | None = None
    auth: Literal["token", "password", "none"] = "token"
    password_file: Path | None = None
    trust_proxy_headers: bool = False
    ui_dir: Path | None = None
    dev_ui_url: str | None = None
    base_path: str = "/"
    log_level: str = "info"
    host_terminal: bool | None = None

    def __post_init__(self) -> None:
        if self.ui_dir is None:
            self.ui_dir = _default_ui_dir()
        elif isinstance(self.ui_dir, str):
            self.ui_dir = Path(self.ui_dir)

        if isinstance(self.upstream_token_file, str):
            self.upstream_token_file = Path(self.upstream_token_file)

        if isinstance(self.password_file, str):
            self.password_file = Path(self.password_file)

        if isinstance(self.port, str):
            self.port = int(self.port)

        if isinstance(self.trust_proxy_headers, str):
            self.trust_proxy_headers = self.trust_proxy_headers.lower() in (
                "1",
                "true",
                "yes",
                "on",
            )

        if self.host_terminal is None:
            self.host_terminal = _is_loopback(self.host)
        elif isinstance(self.host_terminal, str):
            self.host_terminal = self.host_terminal.lower() in (
                "1",
                "true",
                "yes",
                "on",
            )

    def validate(self) -> ServeSettings:
        """Validate settings and refuse insecure or broken configurations."""
        if self.auth not in ("token", "password", "none"):
            raise ValueError(
                f"Invalid auth mode: {self.auth!r}. Must be 'token', 'password', or 'none'."
            )

        if not (1 <= self.port <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {self.port}")

        if self.auth == "none" and not _is_loopback(self.host):
            raise ValueError(
                f"Refusing auth='none' on non-loopback host {self.host!r}. "
                "Authentication ('token' or 'password') is required when binding to non-loopback interfaces."
            )

        if self.auth == "password":
            has_pwd_env = bool(os.environ.get("HERMES_PLUS_PASSWORD"))
            if not self.password_file and not has_pwd_env:
                raise ValueError(
                    "auth='password' requires a password file (--password-file / HERMES_PLUS_PASSWORD_FILE) "
                    "or HERMES_PLUS_PASSWORD environment variable."
                )
            if self.password_file and not self.password_file.exists():
                raise ValueError(f"Password file does not exist: {self.password_file}")

        if self.upstream_token_file and not self.upstream_token_file.exists():
            raise ValueError(
                f"Upstream token file does not exist: {self.upstream_token_file}"
            )

        return self

    @classmethod
    def from_env_and_args(
        cls, args: Any = None, **kwargs: Any
    ) -> ServeSettings:
        """Build ServeSettings with precedence: kwargs > CLI args > env vars > defaults."""
        fields_map = {
            "host": ("HERMES_PLUS_HOST", str, "127.0.0.1"),
            "port": ("HERMES_PLUS_PORT", int, 9129),
            "profile": ("HERMES_PLUS_PROFILE", str, None),
            "hermes_bin": ("HERMES_PLUS_HERMES_BIN", str, None),
            "upstream_url": ("HERMES_PLUS_UPSTREAM_URL", str, None),
            "upstream_token_file": ("HERMES_PLUS_UPSTREAM_TOKEN_FILE", Path, None),
            "auth": ("HERMES_PLUS_AUTH", str, "token"),
            "password_file": ("HERMES_PLUS_PASSWORD_FILE", Path, None),
            "trust_proxy_headers": ("HERMES_PLUS_TRUST_PROXY_HEADERS", bool, False),
            "ui_dir": ("HERMES_PLUS_UI_DIR", Path, None),
            "dev_ui_url": ("HERMES_PLUS_DEV_UI_URL", str, None),
            "base_path": ("HERMES_PLUS_BASE_PATH", str, "/"),
            "log_level": ("HERMES_PLUS_LOG_LEVEL", str, "info"),
            "host_terminal": ("HERMES_PLUS_HOST_TERMINAL", bool, None),
        }

        resolved: dict[str, Any] = {}

        for field_name, (env_var, field_type, default_val) in fields_map.items():
            val = None

            # 1. Direct kwargs
            if field_name in kwargs and kwargs[field_name] is not None:
                val = kwargs[field_name]

            # 2. CLI args
            elif args is not None:
                # Handle alias for upstream_url (--upstream or --upstream-url)
                if field_name == "upstream_url":
                    if hasattr(args, "upstream_url") and args.upstream_url is not None:
                        val = args.upstream_url
                    elif hasattr(args, "upstream") and args.upstream is not None:
                        val = args.upstream
                elif hasattr(args, field_name):
                    arg_val = getattr(args, field_name)
                    if arg_val is not None:
                        val = arg_val

            # 3. Environment variable
            if val is None and env_var in os.environ:
                env_raw = os.environ[env_var]
                if field_type is bool:
                    val = env_raw.lower() in ("1", "true", "yes", "on")
                elif field_type is int:
                    val = int(env_raw)
                elif field_type is Path:
                    val = Path(env_raw)
                else:
                    val = env_raw

            # 4. Default value
            if val is None:
                val = default_val

            # Cast if needed
            if val is not None:
                if field_type is Path and not isinstance(val, Path):
                    val = Path(val)
                elif field_type is int and not isinstance(val, int):
                    val = int(val)
                elif field_type is bool and not isinstance(val, bool):
                    if isinstance(val, str):
                        val = val.lower() in ("1", "true", "yes", "on")
                    else:
                        val = bool(val)

            resolved[field_name] = val

        return cls(**resolved)
