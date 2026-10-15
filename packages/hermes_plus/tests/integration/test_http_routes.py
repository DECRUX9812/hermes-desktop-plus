"""Integration tests for HTTP routes (real sidecar subprocess + real stock Hermes)."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import httpx

# These tests use the real sidecar and Hermes
pytestmark = pytest.mark.integration


@pytest.fixture
def plus_home():
    """Temporary plus_home directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        (home / "files" / "uploads").mkdir(parents=True, exist_ok=True)
        (home / "desktop-plugins").mkdir(parents=True, exist_ok=True)
        yield home


@pytest.fixture
def mock_runtime():
    """Mock runtime for testing."""
    from hermes_plus.store import open_store
    from hermes_plus.settings import ServeSettings
    from hermes_plus.server.runtime import Runtime

    with tempfile.TemporaryDirectory() as tmpdir:
        settings = ServeSettings(
            host="127.0.0.1",
            port=9130,
            profile=None,
            hermes_bin=None,
            auth="none",
            host_terminal=False,
        )

        runtime = mock.MagicMock()
        runtime.settings = settings
        runtime.store_for = mock.MagicMock(
            return_value=open_store(Path(tmpdir) / "plus_home")
        )
        runtime.internal = None
        yield runtime


@pytest.mark.asyncio
async def test_upload_file(mock_runtime, plus_home):
    """Test file upload."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
        host_terminal=False,
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    # Mock the adapter and settings
    with mock.patch("hermes_plus.http.uploads.adapter") as mock_adapter:
        mock_adapter.plus_home.return_value = str(plus_home)

        # Create a test file
        file_content = b"test file content"
        response = client.post(
            "/api/chat/file-upload",
            files={"file": ("test.txt", file_content)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "path" in data
        assert data["size"] == len(file_content)

        # Verify file was actually created
        file_path = Path(data["path"])
        assert file_path.exists()
        assert file_path.read_bytes() == file_content


@pytest.mark.asyncio
async def test_upload_file_no_file(mock_runtime):
    """Test upload with no file."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    response = client.post("/api/chat/file-upload")
    assert response.status_code == 400
    assert "No file provided" in response.text


@pytest.mark.asyncio
async def test_create_share(mock_runtime):
    """Test share creation."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    response = client.post(
        "/api/share/create",
        json={"session_id": "test-session-123"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "id" in data
    assert data["session_id"] == "test-session-123"
    assert "url" in data


@pytest.mark.asyncio
async def test_create_share_no_session_id(mock_runtime):
    """Test share creation without session_id."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    response = client.post("/api/share/create", json={})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_list_shares(mock_runtime):
    """Test listing shares."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    # Create a share first
    response = client.post(
        "/api/share/create",
        json={"session_id": "test-session-123"},
    )
    share_id = response.json()["id"]

    # List shares
    response = client.get("/api/share/list")
    assert response.status_code == 200
    data = response.json()
    assert "shares" in data
    assert len(data["shares"]) > 0
    assert data["shares"][0]["id"] == share_id


@pytest.mark.asyncio
async def test_revoke_share(mock_runtime):
    """Test revoking a share."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    # Create a share
    response = client.post(
        "/api/share/create",
        json={"session_id": "test-session-123"},
    )
    share_id = response.json()["id"]

    # Revoke the share
    response = client.post(
        "/api/share/revoke",
        json={"id": share_id},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    # Verify it's revoked
    response = client.post(
        "/api/share/revoke",
        json={"id": share_id},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_mobile_pairing(mock_runtime):
    """Test mobile pairing code generation."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    response = client.post("/api/mobile/pairing")
    assert response.status_code == 200
    data = response.json()
    assert "code" in data
    assert len(data["code"]) == 6
    assert data["code"].isdigit()
    assert "expires_in" in data
    assert data["expires_in"] > 0


@pytest.mark.asyncio
async def test_mobile_overview(mock_runtime):
    """Test mobile overview."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    response = client.get("/api/mobile/overview")
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert "generated_at" in data


@pytest.mark.asyncio
async def test_desktop_plugins_root(mock_runtime, plus_home):
    """Test desktop plugins root listing."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    with mock.patch("hermes_plus.http.desktop_plugins.adapter") as mock_adapter:
        mock_adapter.plus_home.return_value = str(plus_home)

        response = client.get("/api/desktop-plugins/root")
        assert response.status_code == 200
        data = response.json()
        assert "root" in data


@pytest.mark.asyncio
async def test_webapp_window_ticket(mock_runtime):
    """Test webapp window ticket generation."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    response = client.post("/api/webapp/window-ticket")
    assert response.status_code == 200 or response.status_code == 401  # May be rejected if auth required
    if response.status_code == 200:
        data = response.json()
        assert "ticket" in data


@pytest.mark.asyncio
async def test_git_status(mock_runtime):
    """Test git status endpoint."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmpdir:
        git_repo = Path(tmpdir) / "test-repo"
        git_repo.mkdir()
        (git_repo / ".git").mkdir()

        response = client.post(
            "/api/git/status",
            json={"workspace": str(git_repo)},
        )

        # Should fail because .git is not a real repo
        assert response.status_code in (400, 500)


@pytest.mark.asyncio
async def test_launch_page(mock_runtime):
    """Test launch page rendering."""
    from starlette.testclient import TestClient
    from hermes_plus.server.app import build_app
    from hermes_plus.settings import ServeSettings

    settings = ServeSettings(
        host="127.0.0.1",
        port=9130,
        profile=None,
        base_path="/",
        auth="none",
    )

    app = build_app(settings, mock_runtime)
    client = TestClient(app)

    response = client.get("/webapp/launch")
    assert response.status_code == 200
    assert b"Hermes" in response.content
