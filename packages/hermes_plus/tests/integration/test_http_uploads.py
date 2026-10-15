"""Upload route tests against a real sidecar + real stock Hermes (no mocks)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import httpx
import pytest

from conftest import Sidecar, HERMES_BIN

pytestmark = pytest.mark.integration


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


def test_upload_round_trip(sidecar: Sidecar):
    payload = b"hello hermes-plus upload"
    reply = httpx.post(
        f"{sidecar.url}/api/chat/file-upload",
        files={"file": ("demo notes.txt", payload, "text/plain")},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    # Fork response shape (fork web_routers/uploads.py:186-195).
    assert body["ok"] is True
    assert body["size"] == len(payload)
    staged = body["staged_upload"]
    assert staged["install_id"] and staged["profile_home"] == str(sidecar.home)

    target = Path(body["path"])
    assert target.parent == sidecar.home / "uploads"
    assert target.name.startswith("web-")
    assert target.name.endswith("demo-notes.txt")
    assert target.read_bytes() == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_upload_over_limit_rejected_with_fork_error(sidecar: Sidecar):
    blob = b"0" * (16 * 1024 * 1024 + 1)
    reply = httpx.post(
        f"{sidecar.url}/api/chat/file-upload",
        files={"file": ("big.bin", blob, "application/octet-stream")},
        headers=_auth(sidecar),
        timeout=60,
    )
    assert reply.status_code == 413
    assert reply.json()["detail"] == "File is too large; cap is 16 MiB"


def test_upload_requires_auth(sidecar: Sidecar):
    reply = httpx.post(
        f"{sidecar.url}/api/chat/file-upload",
        files={"file": ("demo.txt", b"x", "text/plain")},
        timeout=10,
    )
    assert reply.status_code == 401


def test_upload_without_file_field(sidecar: Sidecar):
    reply = httpx.post(
        f"{sidecar.url}/api/chat/file-upload",
        headers=_auth(sidecar),
        timeout=10,
    )
    assert reply.status_code in (400, 413)
