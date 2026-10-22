"""Build hook: bundle the prebuilt web UI into the wheel.

The web UI is built from ``../ui`` (see its ``build:webapp`` script) and its
output is gitignored, so hatchling would never pick it up on its own. This
hook copies a built UI into ``src/hermes_plus/ui_dist`` and forces that
directory into the wheel via ``force_include``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class UIBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        # The sdist needs none of this; only the wheel carries the UI.
        if self.target_name != "wheel":
            return

        root = Path(self.root)
        ui_dist = root / "src" / "hermes_plus" / "ui_dist"
        ui_dist_index = ui_dist / "index.html"
        built_ui = root.parent / "ui" / "dist-webapp"
        ui_package = root.parent / "ui" / "package.json"

        if not ui_dist_index.is_file():
            if (built_ui / "index.html").is_file():
                self._copy_ui(built_ui, ui_dist)
            elif ui_package.is_file() and os.environ.get("HERMES_PLUS_SKIP_UI_BUILD") != "1":
                self._build_ui(ui_package.parent)
                self._copy_ui(built_ui, ui_dist)

        if not ui_dist_index.is_file():
            if os.environ.get("HERMES_PLUS_ALLOW_NO_UI") == "1":
                print(
                    "warning: no UI bundle found (src/hermes_plus/ui_dist/index.html); "
                    "the wheel will ship without a UI",
                    file=sys.stderr,
                )
            else:
                raise RuntimeError(
                    "no UI bundle found (src/hermes_plus/ui_dist/index.html): build it "
                    "with `npm --prefix packages/ui run build:webapp` (or let this hook "
                    "build it from packages/ui), copy the output to "
                    "packages/hermes_plus/src/hermes_plus/ui_dist, or set "
                    "HERMES_PLUS_ALLOW_NO_UI=1 to build a wheel without a UI"
                )
            return

        build_data.setdefault("force_include", {})[str(ui_dist)] = "hermes_plus/ui_dist"

    def _build_ui(self, ui_dir: Path) -> None:
        subprocess.run(
            ["npm", "--prefix", str(ui_dir), "run", "build:webapp"],
            check=True,
        )

    def _copy_ui(self, built_ui: Path, ui_dist: Path) -> None:
        shutil.copytree(built_ui, ui_dist, dirs_exist_ok=True)
