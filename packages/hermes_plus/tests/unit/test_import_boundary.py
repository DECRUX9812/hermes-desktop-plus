from __future__ import annotations

import ast
import sys
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


def test_the_bridge_is_stdlib_only() -> None:
    """The bridge runs inside Hermes' venv: no hermes_plus, no Hermes internals, no third-party code."""
    stdlib = set(sys.stdlib_module_names)
    violations: list[str] = []
    for source in (_SOURCE_ROOT / "bridge").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # a relative import inside the bridge package
                    continue
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                root = module.split(".", maxsplit=1)[0]
                if root == "hermes_plus":
                    violations.append(f"{source}:{node.lineno}: {module} (the sidecar's own package)")
                elif _forbidden(module):
                    violations.append(f"{source}:{node.lineno}: {module} (Hermes internal)")
                elif root not in stdlib:
                    violations.append(f"{source}:{node.lineno}: {module} (third-party)")

    assert not violations, "the bridge must stay stdlib-only:\n" + "\n".join(violations)
