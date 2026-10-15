"""Basic integration tests for HTTP routes (module import and instantiation)."""

import pytest


def test_import_all_http_modules():
    """Verify all HTTP route modules import without errors."""
    from hermes_plus.http import (
        uploads,
        share,
        mobile,
        desktop_plugins,
        webapp,
        files,
        git,
    )

    # Verify routes are exported
    assert hasattr(uploads, "routes")
    assert hasattr(share, "routes")
    assert hasattr(mobile, "routes")
    assert hasattr(desktop_plugins, "routes")
    assert hasattr(webapp, "routes")
    assert hasattr(files, "routes")
    assert hasattr(git, "routes")

    # Verify routes are lists
    assert isinstance(uploads.routes, list)
    assert isinstance(share.routes, list)
    assert isinstance(mobile.routes, list)
    assert isinstance(desktop_plugins.routes, list)
    assert isinstance(webapp.routes, list)
    assert isinstance(files.routes, list)
    assert isinstance(git.routes, list)


def test_import_http_routers():
    """Verify ROUTERS list is populated correctly."""
    from hermes_plus.http import ROUTERS

    # ROUTERS should be a list
    assert isinstance(ROUTERS, list)

    # Should have routes from multiple modules
    route_paths = [
        getattr(r, "path", None) for r in ROUTERS
        if hasattr(r, "path")
    ]

    # Verify some expected routes are present
    assert any("/api/chat/file-upload" in str(p) for p in route_paths if p)
    assert any("/api/share" in str(p) for p in route_paths if p)
    assert any("/api/mobile" in str(p) for p in route_paths if p)
    assert any("/api/desktop-plugins" in str(p) for p in route_paths if p)
    assert any("/webapp" in str(p) for p in route_paths if p)
    assert any("/api/git" in str(p) for p in route_paths if p)


def test_host_terminal_module():
    """Verify host_terminal module works correctly."""
    from hermes_plus.http import host_terminal

    # Check core functions exist
    assert hasattr(host_terminal, "request_allowed")
    assert hasattr(host_terminal, "shell_spec")
    assert hasattr(host_terminal, "safe_cwd")
    assert hasattr(host_terminal, "build_env")
    assert hasattr(host_terminal, "handle_host_terminal_ws")

    # Test shell_spec returns a tuple of (argv, name)
    argv, name = host_terminal.shell_spec()
    assert isinstance(argv, list)
    assert isinstance(name, str)
    assert len(argv) > 0

    # Test safe_cwd with home directory
    import os
    from pathlib import Path
    home = str(Path.home())
    cwd = host_terminal.safe_cwd(home)
    assert isinstance(cwd, str)
    assert Path(cwd).exists()

    # Test safe_cwd with invalid path
    invalid_cwd = host_terminal.safe_cwd("/nonexistent/path/that/does/not/exist")
    assert isinstance(invalid_cwd, str)
    assert Path(invalid_cwd).is_dir()  # Should fall back to home


def test_request_allowed_with_disabled_terminal():
    """Test that request_allowed returns False when disabled."""
    from hermes_plus.http import host_terminal
    from unittest import mock

    settings = mock.MagicMock()
    settings.host_terminal = False

    result = host_terminal.request_allowed(settings)
    assert result is False


def test_request_allowed_with_enabled_terminal():
    """Test that request_allowed returns True when enabled."""
    from hermes_plus.http import host_terminal
    from unittest import mock

    settings = mock.MagicMock()
    settings.host_terminal = True

    result = host_terminal.request_allowed(settings)
    assert result is True


def test_git_argument_validation():
    """Test git argument validation."""
    from hermes_plus.http import git
    from pathlib import Path
    import tempfile
    import subprocess

    # Test invalid git operation
    with pytest.raises(ValueError, match="Operation not allowed"):
        git._validate_args("invalid_op", [])

    # Test valid git operation with valid args
    try:
        git._validate_args("status", ["--porcelain"])
    except ValueError:
        pytest.fail("Valid git operation should not raise")

    # Test valid git operation with invalid args
    with pytest.raises(ValueError, match="Argument not allowed"):
        git._validate_args("status", ["--invalid-flag"])


def test_git_validate_path():
    """Test git path validation."""
    from hermes_plus.http import git
    from pathlib import Path
    import tempfile
    import os

    # Test invalid path (not a directory)
    with pytest.raises(ValueError):
        git._validate_git_path("/nonexistent/path")

    # Test invalid path (no .git)
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match="not a git repository"):
            git._validate_git_path(tmpdir)

    # Test valid git repo (create a fake one)
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        result = git._validate_git_path(tmpdir)
        assert result == Path(tmpdir).resolve()


def test_parse_range_header():
    """Test HTTP Range header parsing."""
    from hermes_plus.http import files

    # Test simple range
    result = files._parse_range_header("bytes=0-999", 10000)
    assert result == (0, 1000)

    # Test range without end
    result = files._parse_range_header("bytes=500-", 10000)
    assert result == (500, 10000)

    # Test range at end of file
    result = files._parse_range_header("bytes=999-999", 1000)
    assert result == (999, 1000)  # Valid: byte 999 exists in 1000-byte file

    # Test None
    result = files._parse_range_header(None, 1000)
    assert result is None

    # Test out of bounds
    result = files._parse_range_header("bytes=10000-11000", 10000)
    assert result is None


def test_mobile_pairing_in_memory():
    """Test that mobile pairing uses in-memory storage."""
    from hermes_plus.http import mobile
    import time

    # Test code pruning
    now = time.time()
    mobile._prune_codes(now + 1000)  # All codes have expired
    assert len(mobile._pairing_codes) == 0

    # Add a code and prune with older time
    mobile._pairing_codes["123456"] = now + 100  # Expires in future
    mobile._prune_codes(now)
    assert "123456" in mobile._pairing_codes

    # Prune with time after expiry
    mobile._prune_codes(now + 101)
    assert "123456" not in mobile._pairing_codes


def test_webapp_window_tickets():
    """Test window ticket management."""
    from hermes_plus.http import webapp
    import time

    # Test ticket minting
    now = time.monotonic()
    with webapp._ticket_lock:
        webapp._window_tickets.clear()
        ticket = "test-ticket-12345"
        webapp._window_tickets[ticket] = (now + 60, "test-token", False)

        # Verify ticket is stored
        assert ticket in webapp._window_tickets
        assert webapp._window_tickets[ticket][1] == "test-token"
        assert webapp._window_tickets[ticket][2] is False

        # Verify expired tickets would be cleaned
        for k, (exp, _, _) in list(webapp._window_tickets.items()):
            if exp <= now - 1:
                del webapp._window_tickets[k]

        # Recent ticket should still be there
        assert ticket in webapp._window_tickets


def test_desktop_plugins_path_traversal_protection():
    """Test that desktop_plugins validates paths safely."""
    from hermes_plus.http import desktop_plugins
    from pathlib import Path

    root = Path("/tmp")

    # Test valid plugin name
    try:
        result = desktop_plugins._validated_plugin_dir(root, "valid-plugin-name")
        # Will fail if dir doesn't exist, but function should accept the name
    except ValueError as e:
        if "Desktop plugin not found" not in str(e):
            raise

    # Test invalid plugin names (path traversal attempts)
    invalid_names = [
        "../etc/passwd",
        "..\\..\\windows\\system32",
        "..",
        ".",
        "",
        "plugin\x00with\x00null",
        "/absolute/path",
        "\\windows\\path",
    ]

    for invalid_name in invalid_names:
        with pytest.raises(ValueError, match="Invalid plugin"):
            desktop_plugins._validated_plugin_dir(root, invalid_name)


def test_share_grants_table_schema():
    """Verify share_grants table migration exists."""
    from hermes_plus.store.db import MIGRATIONS

    # Find the share_grants migration
    migration_names = {m.name for m in MIGRATIONS}
    assert any("share_grants" in name for name in migration_names), \
        "share_grants migration should exist"

    # Verify it's migration 0005
    for m in MIGRATIONS:
        if "share_grants" in m.name:
            assert m.version == 5, "share_grants should be migration 5"
            assert "CREATE TABLE share_grants" in m.sql


def test_mobile_pairings_table_schema():
    """Verify mobile_pairings table migration exists."""
    from hermes_plus.store.db import MIGRATIONS

    # Find the mobile_pairings migration
    migration_names = {m.name for m in MIGRATIONS}
    assert any("mobile_pairing" in name for name in migration_names), \
        "mobile_pairing migration should exist"

    # Verify it's migration 0006
    for m in MIGRATIONS:
        if "mobile_pairing" in m.name:
            assert m.version == 6, "mobile_pairing should be migration 6"
            assert "CREATE TABLE mobile_pairings" in m.sql
