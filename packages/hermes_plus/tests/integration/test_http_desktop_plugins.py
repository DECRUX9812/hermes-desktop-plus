"""Desktop-plugins tests: real plugin dir on disk, fork shapes, traversal refused."""

from __future__ import annotations

import httpx
import pytest

from conftest import Sidecar

pytestmark = pytest.mark.integration


def _auth(sidecar: Sidecar) -> dict[str, str]:
    return {"X-Hermes-Session-Token": sidecar.token}


@pytest.fixture
def plugin_root(sidecar: Sidecar) -> str:
    root = sidecar.home / "desktop-plugins"
    (root / "demo-plugin").mkdir(parents=True, exist_ok=True)
    (root / "demo-plugin" / "plugin.js").write_text(
        "export const name = 'demo';\n", encoding="utf-8")
    (root / "demo-plugin" / ".hermes-package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    (root / "demo-plugin" / "secret.txt").write_text("nope", encoding="utf-8")
    return str(root)


def test_root_list_and_source(sidecar: Sidecar, plugin_root: str):
    root = httpx.get(
        f"{sidecar.url}/api/desktop-plugins/root", headers=_auth(sidecar), timeout=30)
    assert root.status_code == 200
    assert root.json() == {"root": plugin_root}

    listed = httpx.get(
        f"{sidecar.url}/api/desktop-plugins/list", headers=_auth(sidecar), timeout=30)
    assert listed.status_code == 200
    entries = listed.json()["entries"]
    demo = [e for e in entries if e["name"] == "demo-plugin"]
    assert demo and demo[0]["isDirectory"] is True
    assert set(demo[0]) == {"name", "path", "isDirectory"}

    per_folder = httpx.get(
        f"{sidecar.url}/api/desktop-plugins/list",
        params={"name": "demo-plugin"}, headers=_auth(sidecar), timeout=30)
    assert per_folder.status_code == 200
    names = {e["name"] for e in per_folder.json()["entries"]}
    # Dotfiles are not listed (fork _list_root_entries) but the package marker
    # is still readable by name.
    assert names == {"plugin.js", "secret.txt"}

    source = httpx.get(
        f"{sidecar.url}/api/desktop-plugins/source",
        params={"name": "demo-plugin"}, headers=_auth(sidecar), timeout=30)
    assert source.status_code == 200, source.text
    body = source.json()
    # Fork shape (fork web_routers/desktop_plugins.py:262-271).
    assert body == {
        "name": "plugin.js",
        "path": f"{plugin_root}/demo-plugin/plugin.js",
        "byteSize": len("export const name = 'demo';\n"),
        "text": "export const name = 'demo';\n",
    }

    marker = httpx.get(
        f"{sidecar.url}/api/desktop-plugins/source",
        params={"name": "demo-plugin", "file": ".hermes-package.json"},
        headers=_auth(sidecar), timeout=30)
    assert marker.status_code == 200
    assert marker.json()["name"] == ".hermes-package.json"


def test_traversal_and_nonreadable_files_refused(sidecar: Sidecar, plugin_root: str):
    auth = _auth(sidecar)
    cases = [
        ({'name': '../escape'}, 4),
        ({'name': 'demo-plugin', 'file': 'secret.txt'}, 4),
        ({'name': '.hidden'}, 4),
        ({'name': 'demo-plugin/..', }, 4),
        ({'name': 'no-such-plugin'}, 4),
    ]
    for params, status_decade in cases:
        reply = httpx.get(
            f"{sidecar.url}/api/desktop-plugins/source", params=params, headers=auth, timeout=30)
        assert reply.status_code // 100 == status_decade, (params, reply.status_code)


def test_plugins_require_auth(sidecar: Sidecar):
    reply = httpx.get(f"{sidecar.url}/api/desktop-plugins/list", timeout=10)
    assert reply.status_code == 401
