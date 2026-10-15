from __future__ import annotations

import ast
from pathlib import Path


_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "hermes_plus"
_ALLOWED_DIRS = {"adapter", "bridge"}
# extras/ holds the fork's directory plugins, copied verbatim into $HERMES_HOME/plugins/ by
# `hermes-plus install`; they run inside Hermes' own venv and import its internals by design.
_EXEMPT_DIRS = {"extras"}
_FORBIDDEN_ROOTS = {
    "hermes_cli",
    "tui_gateway",
    "hermes_constants",
    "run_agent",
    "agent",
    "tools",
    "gateway",
    "model_tools",
}


def _forbidden(module: str) -> bool:
    root = module.split(".", maxsplit=1)[0]
    return root in _FORBIDDEN_ROOTS or root.startswith("hermes_state")


def test_hermes_internals_are_imported_only_by_adapter_or_bridge() -> None:
    violations: list[str] = []
    for source in _SOURCE_ROOT.rglob("*.py"):
        relative = source.relative_to(_SOURCE_ROOT)
        if relative.parts[0] in _ALLOWED_DIRS or relative.parts[0] in _EXEMPT_DIRS:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if _forbidden(module):
                    violations.append(f"{source}:{node.lineno}: {module}")

    assert not violations, "Hermes internal imports outside adapter/bridge:\n" + "\n".join(violations)
