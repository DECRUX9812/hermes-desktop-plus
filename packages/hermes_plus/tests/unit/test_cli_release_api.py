"""`hermes-plus update --check` against a real HTTP server on 127.0.0.1.

The release endpoint is overridable (HERMES_PLUS_RELEASE_API / _REPO), so these
tests drive the real CLI as a subprocess against a real socket: a 404 (no release
published yet) is an informational state with exit 0, while an endpoint that
errors or refuses the connection stays an error whose message names the URL.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from hermes_plus import __version__
from hermes_plus.cli import (
    RELEASE_API_ENV,
    RELEASE_REPO_ENV,
    ReleaseLookupError,
    _resolve_github_release_tag,
    release_api_url,
)

REPO = "demo/hermes-plus"
RELEASE_PATH = f"/repos/{REPO}/releases/latest"
NOT_FOUND = (404, json.dumps({"message": "Not Found"}).encode())
RELEASE = (200, json.dumps({"tag_name": "v0.9.0", "name": "0.9.0"}).encode())


class _ReleaseHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's API
        status, body = self.server.responses.get(self.path, NOT_FOUND)
        self.server.requests.append((self.path, status))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class _ReleaseServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, responses: dict[str, tuple[int, bytes]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, int]] = []
        super().__init__(("127.0.0.1", 0), _ReleaseHandler)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


@pytest.fixture
def release_server() -> Iterator[_ReleaseServer]:
    server = _ReleaseServer({RELEASE_PATH: NOT_FOUND})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _closed_port() -> int:
    """A loopback port nothing listens on: bind, read the number, release it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _cli_env(api_base: str) -> dict[str, str]:
    env = os.environ.copy()
    env[RELEASE_API_ENV] = api_base
    env[RELEASE_REPO_ENV] = REPO
    return env


def _run_cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "hermes_plus.cli", *args],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )


def test_update_check_reports_no_published_release(release_server: _ReleaseServer) -> None:
    res = _run_cli("update", "--check", env=_cli_env(release_server.base))

    assert res.returncode == 0, res.stderr
    assert f"No published release found for {REPO}; installed {__version__}." in res.stdout
    assert f"Looked up: {release_server.base}{RELEASE_PATH}" in res.stdout
    assert res.stderr == ""
    assert release_server.requests == [(RELEASE_PATH, 404)]


def test_update_check_uses_the_published_release(release_server: _ReleaseServer) -> None:
    release_server.responses[RELEASE_PATH] = RELEASE

    res = _run_cli("update", "--check", env=_cli_env(release_server.base))

    assert res.returncode == 0, res.stderr
    assert "hermes_plus-0.9.0-py3-none-any.whl" in res.stdout
    assert "No published release" not in res.stdout


def test_update_check_server_error_is_an_error(release_server: _ReleaseServer) -> None:
    release_server.responses[RELEASE_PATH] = (500, b"boom")

    res = _run_cli("update", "--check", env=_cli_env(release_server.base))

    assert res.returncode == 1
    assert f"Error resolving latest release: Could not fetch latest release from {release_server.base}{RELEASE_PATH}" in res.stderr
    assert "HTTP Error 500" in res.stderr


def test_update_check_unreachable_endpoint_names_the_url() -> None:
    base = f"http://127.0.0.1:{_closed_port()}"

    res = _run_cli("update", "--check", env=_cli_env(base))

    assert res.returncode == 1
    assert f"Could not fetch latest release from {base}{RELEASE_PATH}" in res.stderr


def test_update_without_check_refuses_when_nothing_is_published(release_server: _ReleaseServer) -> None:
    res = _run_cli("update", env=_cli_env(release_server.base))

    assert res.returncode == 1
    assert f"No published release found for {REPO}; nothing to upgrade to (installed {__version__})." in res.stderr


def test_resolve_returns_none_for_a_404(release_server: _ReleaseServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RELEASE_API_ENV, release_server.base)
    assert _resolve_github_release_tag(REPO) is None


def test_resolve_returns_the_tag(release_server: _ReleaseServer, monkeypatch: pytest.MonkeyPatch) -> None:
    release_server.responses[RELEASE_PATH] = RELEASE
    monkeypatch.setenv(RELEASE_API_ENV, release_server.base)
    assert _resolve_github_release_tag(REPO) == "v0.9.0"


def test_resolve_raises_and_names_the_url_on_a_server_error(
    release_server: _ReleaseServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_server.responses[RELEASE_PATH] = (503, b"nope")
    monkeypatch.setenv(RELEASE_API_ENV, release_server.base)
    with pytest.raises(ReleaseLookupError) as excinfo:
        _resolve_github_release_tag(REPO)
    assert release_api_url(REPO) in str(excinfo.value)


def test_resolve_raises_when_the_release_has_no_tag(
    release_server: _ReleaseServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_server.responses[RELEASE_PATH] = (200, b'{"name": "0.9.0"}')
    monkeypatch.setenv(RELEASE_API_ENV, release_server.base)
    with pytest.raises(ReleaseLookupError) as excinfo:
        _resolve_github_release_tag(REPO)
    assert "no tag_name" in str(excinfo.value)


def test_release_api_defaults_to_github(monkeypatch: pytest.MonkeyPatch) -> None:
    from hermes_plus.cli import DEFAULT_GITHUB_REPO, release_api_url, release_repo

    monkeypatch.delenv(RELEASE_API_ENV, raising=False)
    monkeypatch.delenv(RELEASE_REPO_ENV, raising=False)
    assert release_repo() == DEFAULT_GITHUB_REPO
    assert release_api_url(DEFAULT_GITHUB_REPO) == (
        f"https://api.github.com/repos/{DEFAULT_GITHUB_REPO}/releases/latest"
    )
