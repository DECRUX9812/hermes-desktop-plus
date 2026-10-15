"""Upload-into-prompt tests: the fork's staged_upload shape translated into
what STOCK file.attach accepts, end to end over the real WS multiplexer."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from conftest import Sidecar, ws_call, ws_open

pytestmark = pytest.mark.integration


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


async def test_staged_upload_reaches_stock_file_attach(sidecar: Sidecar):
    payload = b"attachment body for the prompt"
    upload = httpx.post(
        f"{sidecar.url}/api/chat/file-upload",
        files={"file": ("demo-note.txt", payload, "text/plain")},
        headers=_auth(sidecar),
        timeout=30,
    )
    assert upload.status_code == 200, upload.text
    staged = upload.json()["staged_upload"]

    async with await ws_open(sidecar) as ws:
        created = await ws_call(ws, "session.create", {"source": "desktop"})
        assert "error" not in created, created
        session_id = created["result"]["session_id"]

        # Exactly the fork UI call (contracts StagedFileUpload): STOCK must
        # accept the translated params, i.e. no 4000 unknown-key error and the
        # file must be staged with a workspace ref back.
        attached = await ws_call(ws, "file.attach", {
            "session_id": session_id,
            "name": "demo-note.txt",
            "staged_upload": staged,
        })
        assert "error" not in attached, attached
        result = attached["result"]
        assert result["attached"] is True
        assert result["uploaded"] is True
        # Stock derives the stored name from the staged path
        # (_stage_session_file_attachment: filename = resolved.name).
        assert result["name"].endswith("demo-note.txt")
        assert result["ref_text"].startswith("@file:")
        # The bytes stock copied into the session attachments match the upload.
        assert Path(result["path"]).read_bytes() == payload
