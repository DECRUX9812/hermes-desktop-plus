"""File-ticket tests: ticket → download with Range → 206 with the right bytes;
expired/reused tickets refused; HEAD supported."""

from __future__ import annotations

import httpx
import pytest

from conftest import Sidecar

pytestmark = pytest.mark.integration

DEMO = bytes(range(65, 91)) * 4  # 104 bytes: ABCDEFGHIJ...ZABC...


@pytest.fixture
def managed_file(sidecar: Sidecar) -> str:
    # .bin for downloads; the stream route serves media extensions only
    # (stock _STREAMABLE_MEDIA_EXTENSIONS), so the same bytes also exist as .mp4.
    base = sidecar.home / "files"
    base.mkdir(parents=True, exist_ok=True)
    (base / "demo.bin").write_bytes(DEMO)
    (base / "demo.mp4").write_bytes(DEMO)
    return str(base / "demo.bin")


def _managed_file_named(sidecar: Sidecar, name: str) -> str:
    return str(sidecar.home / "files" / name)


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


def _ticket(sidecar: Sidecar, managed_file: str, route: str = "download") -> str:
    reply = httpx.post(
        f"{sidecar.url}/api/files/ticket",
        json={"route": route, "path": managed_file},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert reply.status_code == 200, reply.text
    ticket = reply.json()["ticket"]
    assert ticket
    return ticket


def test_ticket_download_with_range(sidecar: Sidecar, managed_file: str):
    ticket = _ticket(sidecar, managed_file)
    url = f"{sidecar.url}/api/files/download"
    params = {"path": managed_file, "ticket": ticket}

    partial = httpx.get(url, params=params, headers={"Range": "bytes=0-9"}, timeout=30)
    assert partial.status_code == 206, partial.text
    assert partial.content == DEMO[:10]
    assert partial.headers.get("content-range") == f"bytes 0-9/{len(DEMO)}"

    # A download ticket is single-use: the second request is refused (401/403).
    reused = httpx.get(url, params=params, headers={"Range": "bytes=0-9"}, timeout=30)
    assert reused.status_code in (401, 403)


def test_ticket_stream_is_reusable(sidecar: Sidecar, managed_file: str):
    media = _managed_file_named(sidecar, "demo.mp4")
    ticket = _ticket(sidecar, media, route="stream")
    url = f"{sidecar.url}/api/files/stream"
    params = {"path": media, "ticket": ticket}

    first = httpx.get(url, params=params, headers={"Range": "bytes=10-19"}, timeout=30)
    assert first.status_code == 206, first.text
    assert first.content == DEMO[10:20]

    second = httpx.get(url, params=params, headers={"Range": "bytes=20-29"}, timeout=30)
    assert second.status_code == 206, second.text
    assert second.content == DEMO[20:30]


def test_head_download_with_ticket(sidecar: Sidecar, managed_file: str):
    ticket = _ticket(sidecar, managed_file)
    reply = httpx.head(
        f"{sidecar.url}/api/files/download",
        params={"path": managed_file, "ticket": ticket},
        timeout=30,
    )
    assert reply.status_code == 200, reply.text
    assert reply.headers.get("content-length") == str(len(DEMO))
    assert reply.content == b""


def test_tampered_or_bogus_ticket_refused(sidecar: Sidecar, managed_file: str):
    ticket = _ticket(sidecar, managed_file)
    url = f"{sidecar.url}/api/files/download"

    wrong_path = httpx.get(
        url, params={"path": managed_file, "ticket": ticket[:40] + "AAAAA"}, timeout=30)
    assert wrong_path.status_code in (401, 403)

    minted_stream = _ticket(sidecar, managed_file, route="stream")
    wrong_route = httpx.get(
        url, params={"path": managed_file, "ticket": minted_stream}, timeout=30)
    # A stream ticket only covers /api/files/stream, never download.
    assert wrong_route.status_code in (401, 403)

    scope_changed = httpx.get(
        url, params={"path": f"{managed_file}x", "ticket": ticket}, timeout=30)
    assert scope_changed.status_code in (401, 403)


def test_authenticated_download_without_ticket_still_works(sidecar: Sidecar, managed_file: str):
    reply = httpx.get(
        f"{sidecar.url}/api/files/download",
        params={"path": managed_file},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert reply.status_code == 200
    assert reply.content == DEMO
