#!/usr/bin/env python3
"""Audit UI RPC/HTTP coverage: what we call vs what stock + plus serve."""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set
from collections import defaultdict


@dataclass
class Coverage:
    stock_rpc: Set[str] = field(default_factory=set)
    plus_rpc: Set[str] = field(default_factory=set)
    stock_http: Set[str] = field(default_factory=set)
    plus_http: Set[str] = field(default_factory=set)
    ui_rpc: Set[str] = field(default_factory=set)
    ui_http: Set[str] = field(default_factory=set)

    def unserved_rpc(self) -> Set[str]:
        """RPC methods called by UI but not served by stock or plus."""
        return self.ui_rpc - (self.stock_rpc | self.plus_rpc)

    def unserved_http(self) -> Set[str]:
        """HTTP paths called by UI but not served by stock or plus."""
        return self.ui_http - (self.stock_http | self.plus_http)


def extract_ui_rpc_calls(ui_src: Path) -> Set[str]:
    """Extract RPC method names the UI calls from TypeScript sources."""
    methods = set()
    patterns = [
        # gateway.request('method.name', ...)
        r"\.request\(\s*['\"]([a-zA-Z0-9._-]+)['\"]\s*[,\)]",
        # rpc('method.name', ...)
        r"rpc\(\s*['\"]([a-zA-Z0-9._-]+)['\"]\s*[,\)]",
        # @method decorators in Python (won't find in UI but patterns matter)
        r"['\"]([a-zA-Z0-9._-]+)['\"]",
    ]

    for ts_file in ui_src.rglob("*.ts"):
        if "test" in ts_file.name or "node_modules" in str(ts_file):
            continue
        try:
            content = ts_file.read_text(errors="ignore")
            for pattern in patterns[:2]:  # Only gateway and rpc patterns for UI
                for match in re.finditer(pattern, content):
                    method = match.group(1)
                    # Filter to look like RPC method names (namespace.method)
                    if "." in method or method in [
                        "ping", "sleep", "reload.mcp", "session.close",
                        "wake.pause", "connection.respond", "cli.exec"
                    ]:
                        methods.add(method)
        except Exception:
            pass

    for tsx_file in ui_src.rglob("*.tsx"):
        if "test" in tsx_file.name or "node_modules" in str(tsx_file):
            continue
        try:
            content = tsx_file.read_text(errors="ignore")
            for pattern in patterns[:2]:
                for match in re.finditer(pattern, content):
                    method = match.group(1)
                    if "." in method or method in [
                        "ping", "sleep", "reload.mcp", "session.close",
                        "wake.pause", "connection.respond", "cli.exec"
                    ]:
                        methods.add(method)
        except Exception:
            pass

    return methods


def extract_ui_http_calls(ui_src: Path) -> Set[str]:
    """Extract HTTP paths the UI calls from TypeScript sources."""
    paths = set()
    patterns = [
        # /api/..., /webapp/..., /share/..., /mobile
        r"['\"](/(?:api|webapp|share|mobile)[a-zA-Z0-9_/\-\?\=\*]*)['\"]",
        # fetch('...'), axios.get('...'), etc.
        r"(?:fetch|get|post|put|delete)\s*\(\s*['\"](/[^'\"]*)['\"]",
    ]

    for ts_file in ui_src.rglob("*.ts"):
        if "test" in ts_file.name or "node_modules" in str(ts_file):
            continue
        try:
            content = ts_file.read_text(errors="ignore")
            for pattern in patterns:
                for match in re.finditer(pattern, content):
                    path = match.group(1)
                    # Normalize template variables
                    path = re.sub(r"\$\{[^}]+\}|\*|:id|:\w+", "*", path)
                    paths.add(path)
        except Exception:
            pass

    for tsx_file in ui_src.rglob("*.tsx"):
        if "test" in tsx_file.name or "node_modules" in str(tsx_file):
            continue
        try:
            content = tsx_file.read_text(errors="ignore")
            for pattern in patterns:
                for match in re.finditer(pattern, content):
                    path = match.group(1)
                    path = re.sub(r"\$\{[^}]+\}|\*|:id|:\w+", "*", path)
                    paths.add(path)
        except Exception:
            pass

    return paths


def extract_stock_rpc(upstream_dir: Path) -> Set[str]:
    """Extract RPC methods served by stock Hermes from @method decorators."""
    methods = set()

    # Look for @method("name") in tui_gateway and hermes_cli
    for py_file in (upstream_dir / "hermes_cli").rglob("*.py"):
        try:
            content = py_file.read_text(errors="ignore")
            # @method("namespace.method")
            for match in re.finditer(r'@method\(\s*["\']([a-zA-Z0-9._-]+)["\']\s*\)', content):
                methods.add(match.group(1))
        except Exception:
            pass

    # tui_gateway methods
    tui_gateway_path = upstream_dir / "tui_gateway"
    if tui_gateway_path.exists():
        for py_file in tui_gateway_path.rglob("*.py"):
            try:
                content = py_file.read_text(errors="ignore")
                for match in re.finditer(r'@method\(\s*["\']([a-zA-Z0-9._-]+)["\']\s*\)', content):
                    methods.add(match.group(1))
            except Exception:
                pass

    return methods


def extract_stock_http(upstream_dir: Path) -> Set[str]:
    """Extract HTTP routes served by stock Hermes from router decorators."""
    paths = set()

    # Look for @app.route, @router.post, @router.get etc. in web_routers
    for py_file in (upstream_dir / "hermes_cli" / "web_routers").rglob("*.py"):
        try:
            content = py_file.read_text(errors="ignore")
            # @app.route("/path"), @router.post("/path"), etc.
            for match in re.finditer(r'@(?:app|router)\.\w+\(\s*["\'](/[^"\']*)["\']', content):
                path = match.group(1)
                path = re.sub(r"<[^>]+>|:id|:\w+", "*", path)
                paths.add(path)
        except Exception:
            pass

    return paths


def extract_plus_rpc(package_dir: Path) -> Set[str]:
    """Extract RPC methods served by plus from @method decorators in handlers."""
    methods = set()

    # Look for @method decorators in our handlers
    handlers_dir = package_dir / "src" / "hermes_plus" / "rpc" / "handlers"
    if handlers_dir.exists():
        for py_file in handlers_dir.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            try:
                content = py_file.read_text(errors="ignore")
                # Pattern: @method("namespace.method")
                for match in re.finditer(r'@method\(\s*["\']([a-zA-Z0-9._-]+)["\']\s*\)', content):
                    methods.add(match.group(1))
            except Exception:
                pass

    return methods


def extract_plus_http(package_dir: Path) -> Set[str]:
    """Extract HTTP routes served by plus from server and http modules."""
    paths = set()

    # Look in http directory
    http_dir = package_dir / "src" / "hermes_plus" / "http"
    if http_dir.exists():
        for py_file in http_dir.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            try:
                content = py_file.read_text(errors="ignore")
                # Starlette Route patterns: Route("/path", ...) and Route("/path", ..., methods=[...])
                for match in re.finditer(r'Route\(\s*["\'](/[^"\']*)["\']', content):
                    path = match.group(1)
                    path = re.sub(r"<[^>]+>|:id|:\w+", "*", path)
                    paths.add(path)
                # Also look for @app.route, @router.post, etc. (in case they're used)
                for match in re.finditer(r'@(?:app|router)\.\w+\(\s*["\'](/[^"\']*)["\']', content):
                    path = match.group(1)
                    path = re.sub(r"<[^>]+>|:id|:\w+", "*", path)
                    paths.add(path)
            except Exception:
                pass

    # Look in server package (recursively)
    server_dir = package_dir / "src" / "hermes_plus" / "server"
    if server_dir.exists():
        for py_file in server_dir.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            try:
                content = py_file.read_text(errors="ignore")
                # Starlette Route patterns: Route("/path", ...)
                for match in re.finditer(r'Route\(\s*["\'](/[^"\']*)["\']', content):
                    path = match.group(1)
                    path = re.sub(r"<[^>]+>|:id|:\w+", "*", path)
                    paths.add(path)
                # Also look for @app.route, @router.post, etc.
                for match in re.finditer(r'@(?:app|router)\.\w+\(\s*["\'](/[^"\']*)["\']', content):
                    path = match.group(1)
                    path = re.sub(r"<[^>]+>|:id|:\w+", "*", path)
                    paths.add(path)
            except Exception:
                pass

    # Look in server.py if it exists (for backwards compatibility)
    server_file = package_dir / "src" / "hermes_plus" / "server.py"
    if server_file.exists():
        try:
            content = server_file.read_text(errors="ignore")
            for match in re.finditer(r'Route\(\s*["\'](/[^"\']*)["\']', content):
                path = match.group(1)
                path = re.sub(r"<[^>]+>|:id|:\w+", "*", path)
                paths.add(path)
            for match in re.finditer(r'@app\.\w+\(\s*["\'](/[^"\']*)["\']', content):
                path = match.group(1)
                path = re.sub(r"<[^>]+>|:id|:\w+", "*", path)
                paths.add(path)
        except Exception:
            pass

    return paths


def normalize_path(path: str) -> str:
    """Normalize paths for comparison."""
    # Remove trailing slashes and normalize template vars
    path = path.rstrip("/") or "/"
    path = re.sub(r"<[^>]+>|:[\w]+", "*", path)
    return path


def paths_match(ui_path: str, served_path: str) -> bool:
    """Check if a UI path matches a served path pattern."""
    ui_norm = normalize_path(ui_path)
    served_norm = normalize_path(served_path)

    # Exact match
    if ui_norm == served_norm:
        return True

    # Pattern match: * matches anything
    if "*" in served_norm:
        # Convert pattern to regex
        pattern = re.escape(served_norm).replace(r"\*", "[^/]*")
        if re.match(f"^{pattern}$", ui_norm):
            return True

    return False


def main():
    parser = argparse.ArgumentParser(
        description="Check UI RPC/HTTP coverage against stock and plus services."
    )
    parser.add_argument(
        "--upstream",
        type=Path,
        required=True,
        help="Path to upstream Hermes checkout (for extracting stock methods/routes)"
    )
    parser.add_argument(
        "--hermes-plus",
        type=Path,
        default=Path.cwd() / "packages" / "hermes_plus",
        help="Path to hermes-plus package (default: packages/hermes_plus)"
    )
    parser.add_argument(
        "--ui-src",
        type=Path,
        default=Path.cwd() / "packages" / "ui" / "src",
        help="Path to UI source (default: packages/ui/src)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of text"
    )
    parser.add_argument(
        "--fail-on-unserved",
        action="store_true",
        help="Exit with code 1 if any unserved RPC/HTTP is found (respecting allowlist)"
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path.cwd() / "scripts" / "ui-coverage-allowlist.txt",
        help="Path to allowlist file (one name per line with # reason comments)"
    )

    args = parser.parse_args()

    # Verify inputs exist
    if not args.upstream.exists():
        print(f"Error: upstream dir not found: {args.upstream}", file=sys.stderr)
        return 1
    if not args.ui_src.exists():
        print(f"Error: UI src not found: {args.ui_src}", file=sys.stderr)
        return 1

    # Load allowlist
    allowlist = set()
    if args.allowlist.exists():
        try:
            for line in args.allowlist.read_text().splitlines():
                # Strip comments
                name = line.split("#")[0].strip()
                if name:
                    allowlist.add(name)
        except Exception:
            pass

    # Extract coverage
    cov = Coverage()
    cov.ui_rpc = extract_ui_rpc_calls(args.ui_src)
    cov.ui_http = extract_ui_http_calls(args.ui_src)
    cov.stock_rpc = extract_stock_rpc(args.upstream)
    cov.stock_http = extract_stock_http(args.upstream)
    cov.plus_rpc = extract_plus_rpc(args.hermes_plus)
    cov.plus_http = extract_plus_http(args.hermes_plus)

    # Find unserved
    unserved_rpc = sorted(cov.unserved_rpc() - allowlist)
    unserved_http = sorted(cov.unserved_http() - allowlist)

    if args.json:
        result = {
            "stock_rpc": sorted(cov.stock_rpc),
            "plus_rpc": sorted(cov.plus_rpc),
            "ui_rpc": sorted(cov.ui_rpc),
            "served_rpc": sorted((cov.stock_rpc | cov.plus_rpc)),
            "unserved_rpc": unserved_rpc,
            "stock_http": sorted(cov.stock_http),
            "plus_http": sorted(cov.plus_http),
            "ui_http": sorted(cov.ui_http),
            "served_http": sorted((cov.stock_http | cov.plus_http)),
            "unserved_http": unserved_http,
        }
        print(json.dumps(result, indent=2))
    else:
        print("=== RPC Methods ===")
        print(f"\nStock Hermes serves ({len(cov.stock_rpc)} methods):")
        for m in sorted(cov.stock_rpc):
            print(f"  {m}")
        print(f"\nHermes Plus serves ({len(cov.plus_rpc)} methods):")
        for m in sorted(cov.plus_rpc):
            print(f"  {m}")
        print(f"\nUI calls ({len(cov.ui_rpc)} methods):")
        for m in sorted(cov.ui_rpc):
            print(f"  {m}")

        print(f"\nUNSERVED RPC ({len(unserved_rpc)}):")
        if unserved_rpc:
            for m in unserved_rpc:
                print(f"  {m}")
        else:
            print("  (none)")

        print("\n=== HTTP Routes ===")
        print(f"\nStock Hermes serves ({len(cov.stock_http)} routes):")
        for p in sorted(cov.stock_http):
            print(f"  {p}")
        print(f"\nHermes Plus serves ({len(cov.plus_http)} routes):")
        for p in sorted(cov.plus_http):
            print(f"  {p}")
        print(f"\nUI calls ({len(cov.ui_http)} routes):")
        for p in sorted(cov.ui_http):
            print(f"  {p}")

        print(f"\nUNSERVED HTTP ({len(unserved_http)}):")
        if unserved_http:
            for p in unserved_http:
                print(f"  {p}")
        else:
            print("  (none)")

    if args.fail_on_unserved and (unserved_rpc or unserved_http):
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
