"""Hermes Desktop Plus CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

DEFAULT_GITHUB_REPO = "DECRUX9812/hermes-desktop-plus"
DEFAULT_RELEASE_API = "https://api.github.com"

# The release endpoint is GitHub's by default; a mirror or a test server can be
# pointed at without touching the code, and the repo slug can move the same way.
RELEASE_API_ENV = "HERMES_PLUS_RELEASE_API"
RELEASE_REPO_ENV = "HERMES_PLUS_RELEASE_REPO"

# A cold HERMES_HOME makes the first Hermes command provision the stock runtime (minutes).
INSTALL_TIMEOUT_ENV = "HERMES_PLUS_INSTALL_TIMEOUT"
DEFAULT_INSTALL_TIMEOUT = 900


class ReleaseLookupError(Exception):
    """A release lookup failed for a reason other than "no release published"."""


class NoPublishedRelease(Exception):
    """The release endpoint answered 404: the repo has no published release yet."""

    def __init__(self, repo: str, url: str) -> None:
        super().__init__(f"No published release found for {repo}")
        self.repo = repo
        self.url = url


def _get_hermes_home(profile: str | None = None) -> Path:
    """Resolve HERMES_HOME path."""
    try:
        from hermes_plus import adapter

        if hasattr(adapter, "hermes_home"):
            return Path(adapter.hermes_home(profile))
    except Exception:
        pass

    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        base = Path(env_home).expanduser().resolve()
    else:
        base = Path.home() / ".hermes"

    if profile:
        return base / "profiles" / profile
    return base


def _get_plus_home(profile: str | None = None) -> Path:
    """Resolve plus_home (<HERMES_HOME>/hermes-plus)."""
    try:
        from hermes_plus import adapter

        if hasattr(adapter, "plus_home"):
            return Path(adapter.plus_home(profile))
    except Exception:
        pass

    return _get_hermes_home(profile) / "hermes-plus"


def _copy_tree_atomic(source: Path, target: Path, workdir: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix=f".tmp_{target.name}_", dir=workdir))
    try:
        shutil.copytree(source, tmp, dirs_exist_ok=True)
        if target.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".tmp_old_{target.name}_", dir=workdir))
            target.replace(backup)
            tmp.replace(target)
            shutil.rmtree(backup, ignore_errors=True)
        else:
            tmp.replace(target)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _hash_tree(root: Path) -> dict[str, str]:
    """sha256 of every file under root, keyed by relative path."""
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return hashes


def _install_bridge_and_extras(*, bridge_src: Path, extras_src: Path, plugins_dir: Path,
                               skills_dir: Path, plus_home: Path, extras: str | None,
                               profile: str | None,
                               enable_plugin: Callable[..., Any] | None) -> list[str]:
    """Copy the bridge plugin, the selected extra plugins and every bundled skill.

    Skills are refreshed only when the user has not modified them since our last install; the
    per-file hashes recorded in <plus_home>/installed-extras.json decide. Returns log lines.
    """
    lines: list[str] = []

    # The bridge plugin: replace atomically on every install.
    if bridge_src.is_dir():
        _copy_tree_atomic(bridge_src, plugins_dir / "hermes-plus", plugins_dir)
        if enable_plugin is not None:
            enable_plugin("hermes-plus", profile=profile)
        lines.append("Installed and enabled bridge plugin 'hermes-plus'.")
    else:
        lines.append("Note: bridge/ plugin is not packaged yet, skipping bridge install.")

    # Selected extra plugins (opt-in via --extras).
    for extra_name in [x.strip() for x in (extras or "").split(",") if x.strip()]:
        extra_src = extras_src / "plugins" / extra_name
        if extra_src.is_dir():
            _copy_tree_atomic(extra_src, plugins_dir / extra_name, plugins_dir)
            if enable_plugin is not None:
                enable_plugin(extra_name, profile=profile)
            lines.append(f"Installed and enabled extra plugin '{extra_name}'.")
        else:
            lines.append(f"Note: extra plugin '{extra_name}' is not packaged yet, skipping.")

    # Bundled skills: always refreshed unless the user modified their copy.
    ledger_path = plus_home / "installed-extras.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}
    recorded_skills: dict[str, dict[str, str]] = ledger.get("skills", {})
    new_skills: dict[str, dict[str, str]] = {}

    for skill_src in sorted((extras_src / "skills").glob("*")) if extras_src.is_dir() else []:
        if not skill_src.is_dir():
            continue
        target = skills_dir / skill_src.name
        recorded = recorded_skills.get(skill_src.name, {})
        if target.exists() and _hash_tree(target) != recorded:
            lines.append(f"Kept user-modified skill '{skill_src.name}'.")
            # Record the shipped baseline, not the user's edit: recording the edit would make the
            # next install compare equal and overwrite it.
            new_skills[skill_src.name] = recorded or _hash_tree(skill_src)
            continue
        try:
            shutil.copytree(skill_src, target, dirs_exist_ok=True)
        except OSError as err:
            lines.append(f"Warning: could not install skill '{skill_src.name}': {err}")
            continue
        hashes = _hash_tree(skill_src)
        new_skills[skill_src.name] = hashes
        lines.append(f"Installed skill '{skill_src.name}'.")

    ledger["skills"] = new_skills
    try:
        ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    except OSError as err:
        lines.append(f"Warning: could not write {ledger_path}: {err}")
    return lines


def cmd_version(args: argparse.Namespace) -> int:
    """Show hermes-plus version, adapter API, and detected Hermes version."""
    from hermes_plus import __version__

    adapter_api: str = "unavailable"
    hermes_ver: str = "not detected"

    try:
        from hermes_plus import adapter

        if hasattr(adapter, "ADAPTER_API"):
            adapter_api = str(adapter.ADAPTER_API)
        if hasattr(adapter, "detect"):
            inst = adapter.detect()
            if inst and getattr(inst, "version", None):
                hermes_ver = str(inst.version)
    except Exception:
        pass

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "hermes_plus": __version__,
                    "adapter_api": adapter_api,
                    "hermes": hermes_ver,
                },
                indent=2,
            )
        )
    else:
        print(f"hermes-plus {__version__}")
        print(f"adapter API: {adapter_api}")
        print(f"hermes: {hermes_ver}")

    return 0


def _install_timeout_message(command: Sequence[str], budget: int) -> str:
    shown = " ".join([Path(command[0]).name, *command[1:]])
    return (
        f"'{shown}' did not finish within {budget} s. On a first run in a new HERMES_HOME, "
        "stock Hermes prepares its isolated runtime, which can take several minutes. "
        "Re-run 'hermes-plus install' (it is safe to repeat), or raise the budget with "
        f"{INSTALL_TIMEOUT_ENV}=<seconds>."
    )


def _budgeted_enable(enable_plugin: Callable[..., Any], budget: int) -> Callable[..., None]:
    announced = False

    def enable(name: str, *, profile: str | None) -> None:
        nonlocal announced
        if not announced:
            announced = True
            print("Stock Hermes may be preparing its runtime on first use, "
                  "which can take several minutes; its progress follows.")
        # Stock prints the provisioning steps on stderr; relay them, or a first
        # install in a cold HERMES_HOME is minutes of silence.
        enable_plugin(name, profile=profile, timeout=budget, relay_stderr=True)

    return enable


def cmd_install(args: argparse.Namespace) -> int:
    """Install bridge plugin, configure profile, and migrate database."""
    try:
        budget = int(os.environ.get(INSTALL_TIMEOUT_ENV) or DEFAULT_INSTALL_TIMEOUT)
    except ValueError:
        sys.stderr.write(f"{INSTALL_TIMEOUT_ENV} must be a whole number of seconds\n")
        return 2

    # 1. detect() and check_compat()
    try:
        from hermes_plus import adapter
    except ImportError:
        sys.stderr.write("Component not installed yet: hermes_plus.adapter\n")
        return 2

    if not hasattr(adapter, "detect") or not hasattr(adapter, "check_compat"):
        sys.stderr.write(
            "Component not installed yet: hermes_plus.adapter (missing detect or check_compat)\n"
        )
        return 2

    try:
        inst = adapter.detect(args.hermes)
    except Exception as exc:
        sys.stderr.write(f"Error detecting Hermes installation: {exc}\n")
        return 2

    # Pin the detected binary for every later adapter CLI call: resolve_hermes_bin()
    # falls back to `hermes` on PATH, which on a developer machine is often a
    # personal install — it would then enable plugins against the wrong Hermes
    # and provision its own pm runtime into this home.
    if getattr(inst, "hermes_bin", None):
        os.environ["HERMES_PLUS_HERMES_BIN"] = str(inst.hermes_bin)

    compat = adapter.check_compat(inst)
    compat_status = getattr(compat, "status", "untested")
    compat_msg = getattr(compat, "message", "")

    force = getattr(args, "force", False) or os.environ.get("HERMES_PLUS_FORCE") == "1"
    if compat_status == "unsupported":
        if not force:
            sys.stderr.write(
                f"Unsupported Hermes version: {compat_msg or 'Version not supported'}\n"
            )
            return 3
        print(f"Warning: Hermes version is unsupported ({compat_msg}), proceeding due to --force")
    elif compat_status == "untested":
        print(f"Warning: Hermes version is untested ({compat_msg})")

    # Resolve paths
    if hasattr(adapter, "hermes_home"):
        h_home = Path(adapter.hermes_home(args.profile))
    else:
        h_home = Path(getattr(inst, "hermes_home", _get_hermes_home(args.profile)))
    h_home.mkdir(parents=True, exist_ok=True)

    plugins_dir = h_home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    # 2+3. Bridge plugin, selected extra plugins, and the bundled skills
    if hasattr(adapter, "plus_home"):
        p_home = Path(adapter.plus_home(args.profile))
    else:
        p_home = _get_plus_home(args.profile)
    p_home.mkdir(parents=True, exist_ok=True)

    pkg_root = Path(__file__).resolve().parent
    enable_plugin = getattr(adapter, "enable_plugin", None)
    try:
        lines = _install_bridge_and_extras(
            bridge_src=pkg_root / "bridge",
            extras_src=pkg_root / "extras",
            plugins_dir=plugins_dir,
            skills_dir=h_home / "skills",
            plus_home=p_home,
            extras=args.extras,
            profile=args.profile,
            enable_plugin=(
                None if enable_plugin is None else _budgeted_enable(enable_plugin, budget)
            ))
    except subprocess.TimeoutExpired as exc:
        sys.stderr.write(_install_timeout_message(exc.cmd, budget) + "\n")
        return 1
    except subprocess.CalledProcessError as exc:
        detail = _first_error_line(exc.stderr or "")
        sys.stderr.write(f"Enabling a plugin failed (exit {exc.returncode}): {detail}\n")
        return 1
    for line in lines:
        print(line)

    # 4. open_store(plus_home).migrate()
    try:
        from hermes_plus.store import open_store
    except ImportError:
        sys.stderr.write("Component not installed yet: hermes_plus.store\n")
        return 2

    if not callable(open_store):
        sys.stderr.write(
            "Component not installed yet: hermes_plus.store.open_store\n"
        )
        return 2

    try:
        store = open_store(p_home)
        if hasattr(store, "migrate"):
            store.migrate()
            print(f"Database migrated at {p_home / 'plus.db'}.")
    except Exception as exc:
        sys.stderr.write(f"Database migration failed: {exc}\n")
        return 2

    # 5. Optionally run the importer
    if args.import_fork_data:
        try:
            from hermes_plus.store import import_fork_data  # type: ignore

            import_fork_data(p_home)
            print("Imported fork data successfully.")
        except ImportError:
            print("Note: fork importer is not packaged yet, skipping.")
        except Exception as exc:
            print(f"Warning: fork data import failed: {exc}")

    # 6. Print the next step
    print("\nHermes Plus installation complete!")
    print("Next step: run 'hermes-plus serve' to start the web app.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Build settings, validate them, and launch the hermes-plus server."""
    from hermes_plus.settings import ServeSettings

    try:
        settings = ServeSettings.from_env_and_args(args).validate()
    except ValueError as exc:
        sys.stderr.write(f"Configuration error: {exc}\n")
        return 1

    try:
        from hermes_plus import server
    except ImportError:
        sys.stderr.write("Component not installed yet: hermes_plus.server\n")
        return 2

    if not hasattr(server, "run"):
        sys.stderr.write("Component not installed yet: hermes_plus.server.run\n")
        return 2

    server.run(settings)
    return 0


_ENV_FAILURE_HINT = (
    "this HERMES_HOME can't borrow the Hermes install; use the home that owns it "
    "(the one containing hermes-agent/) or a profile under it"
)


def _first_error_line(text: str) -> str:
    """Pick the line worth showing from a failed command's output."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        lowered = line.lower()
        if "error" in lowered or "no module named" in lowered or "traceback" in lowered:
            return line[:200]
    return lines[-1][:200] if lines else "no output"


def _check_hermes_env(hermes_bin: str | None, h_home: Path) -> dict[str, Any]:
    """Run the stock CLI against HERMES_HOME.

    Hermes resolves its Python environment from the home it is handed; a home
    that does not own a checkout fails here with missing modules or an
    unresolved install, which is what breaks `hermes-plus serve` later.
    """
    if not hermes_bin:
        return {
            "name": "hermes_env",
            "status": "warn",
            "message": "Cannot check the Hermes environment: no hermes executable found",
            "hint": "Install Hermes Agent, or set HERMES_PLUS_HERMES_BIN.",
        }
    if not h_home.exists():
        return {
            "name": "hermes_env",
            "status": "warn",
            "message": f"HERMES_HOME does not exist yet: {h_home}",
            "hint": "Run 'hermes-plus install' to initialize it.",
        }
    env = os.environ.copy()
    env["HERMES_HOME"] = str(h_home)
    try:
        version = subprocess.run(
            [hermes_bin, "--version"], capture_output=True, text=True, env=env,
            timeout=30, check=False,
        )
        plugins = subprocess.run(
            [hermes_bin, "plugins", "list", "--plain", "--no-bundled"],
            capture_output=True, text=True, env=env, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "name": "hermes_env",
            "status": "fail",
            "message": f"Hermes CLI could not be executed for HERMES_HOME={h_home}: {exc}",
            "hint": _ENV_FAILURE_HINT,
        }

    if version.returncode == 0 and plugins.returncode == 0:
        reported = (version.stdout or version.stderr).strip().splitlines()
        return {
            "name": "hermes_env",
            "status": "ok",
            "message": (
                f"Hermes CLI runs for HERMES_HOME={h_home}"
                + (f" ({reported[0]})" if reported else "")
            ),
            "hint": "",
        }

    failed = plugins if plugins.returncode else version
    return {
        "name": "hermes_env",
        "status": "fail",
        "message": (
            f"Hermes CLI fails for HERMES_HOME={h_home} "
            f"(exit {failed.returncode}): {_first_error_line(failed.stderr or failed.stdout)}"
        ),
        "hint": _ENV_FAILURE_HINT,
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run health checks on environment, dependencies, and configuration."""
    checks: list[dict[str, Any]] = []

    profile = getattr(args, "profile", None)
    h_home = _get_hermes_home(profile)
    p_home = _get_plus_home(profile)

    # Check 1: hermes found and its version
    hermes_bin: str | None = None
    hermes_ver: str | None = None
    compat_status: str | None = None
    compat_msg: str = ""

    adapter_available = False
    inst = None
    try:
        from hermes_plus import adapter

        adapter_available = True
        if hasattr(adapter, "detect"):
            inst = adapter.detect(getattr(args, "hermes", None))
            if inst:
                hermes_bin = inst.hermes_bin
                hermes_ver = inst.version
        if inst and hasattr(adapter, "check_compat"):
            compat = adapter.check_compat(inst)
            compat_status = getattr(compat, "status", None)
            compat_msg = getattr(compat, "message", "")
    except Exception:
        pass

    if not hermes_bin:
        # Same precedence the adapter uses: HERMES_PLUS_HERMES_BIN, then PATH.
        hermes_bin = shutil.which(os.environ.get("HERMES_PLUS_HERMES_BIN") or "hermes")

    if hermes_bin:
        if not hermes_ver:
            try:
                out = subprocess.check_output(
                    [hermes_bin, "--version"], text=True, timeout=5
                ).strip()
                hermes_ver = out
            except Exception:
                hermes_ver = "unknown"
        checks.append({
            "name": "hermes_binary",
            "status": "ok",
            "message": f"Hermes binary found: {hermes_bin} ({hermes_ver})",
            "hint": "",
        })
    else:
        checks.append({
            "name": "hermes_binary",
            "status": "fail",
            "message": "Hermes binary not found in PATH or standard locations",
            "hint": "Install Hermes Agent or run install.sh with HERMES_PLUS_INSTALL_HERMES=1.",
        })

    # Check 2: compat status
    if compat_status == "supported":
        checks.append({
            "name": "hermes_compat",
            "status": "ok",
            "message": f"Hermes version is supported ({hermes_ver})",
            "hint": "",
        })
    elif compat_status == "untested":
        checks.append({
            "name": "hermes_compat",
            "status": "warn",
            "message": f"Hermes version is untested: {compat_msg}",
            "hint": "Upgrade hermes-plus or test with caution.",
        })
    elif compat_status == "unsupported":
        checks.append({
            "name": "hermes_compat",
            "status": "fail",
            "message": f"Hermes version is unsupported: {compat_msg}",
            "hint": "Use a supported Hermes version from compat matrix.",
        })
    else:
        if hermes_ver and hermes_ver != "unknown":
            checks.append({
                "name": "hermes_compat",
                "status": "ok",
                "message": f"Detected Hermes version {hermes_ver}",
                "hint": "",
            })
        else:
            checks.append({
                "name": "hermes_compat",
                "status": "warn",
                "message": "Unable to verify Hermes compatibility matrix",
                "hint": "Check adapter installation and compat/matrix.json.",
            })

    # Check 3: the stock CLI can resolve its environment for THIS HERMES_HOME
    checks.append(_check_hermes_env(hermes_bin, h_home))

    # Check 4: HERMES_HOME exists and is writable
    if h_home.exists():
        if os.access(h_home, os.W_OK):
            checks.append({
                "name": "hermes_home",
                "status": "ok",
                "message": f"HERMES_HOME exists and is writable: {h_home}",
                "hint": "",
            })
        else:
            checks.append({
                "name": "hermes_home",
                "status": "fail",
                "message": f"HERMES_HOME is not writable: {h_home}",
                "hint": f"Grant write permissions to {h_home}.",
            })
    else:
        if h_home.parent.exists() and os.access(h_home.parent, os.W_OK):
            checks.append({
                "name": "hermes_home",
                "status": "warn",
                "message": f"HERMES_HOME does not exist yet: {h_home} (parent writable)",
                "hint": "Run 'hermes-plus install' to initialize it.",
            })
        else:
            checks.append({
                "name": "hermes_home",
                "status": "fail",
                "message": f"HERMES_HOME does not exist and parent is not writable: {h_home}",
                "hint": f"Create {h_home} with write permissions.",
            })

    # Check 5: bridge plugin installed (and its version matches ours) and enabled (via adapter.load_config)
    from hermes_plus import __version__

    plugin_dir = h_home / "plugins" / "hermes-plus"
    if not plugin_dir.exists():
        checks.append({
            "name": "bridge_plugin",
            "status": "fail",
            "message": f"Bridge plugin is not installed at {plugin_dir}",
            "hint": "Run 'hermes-plus install' to install the bridge plugin.",
        })
    else:
        # Check enabled via adapter.load_config
        is_enabled = False
        if adapter_available:
            try:
                from hermes_plus import adapter

                if hasattr(adapter, "load_config"):
                    cfg = adapter.load_config(profile)
                    plugins = cfg.get("plugins", {})
                    if isinstance(plugins, dict):
                        enabled_list = plugins.get("enabled", [])
                        is_enabled = "hermes-plus" in enabled_list or plugins.get(
                            "hermes-plus"
                        ) is True
                    elif isinstance(plugins, list):
                        is_enabled = "hermes-plus" in plugins
                else:
                    is_enabled = True
            except Exception:
                is_enabled = True
        else:
            is_enabled = True

        if is_enabled:
            checks.append({
                "name": "bridge_plugin",
                "status": "ok",
                "message": "Bridge plugin is installed and enabled",
                "hint": "",
            })
        else:
            checks.append({
                "name": "bridge_plugin",
                "status": "warn",
                "message": "Bridge plugin is installed but not enabled in config.yaml",
                "hint": "Run 'hermes-plus install' or enable with hermes CLI.",
            })

        # The bridge's own handshake: what it registered, and against which version.
        bridge_json = p_home / "bridge.json"
        if bridge_json.exists():
            try:
                handshake = json.loads(bridge_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                handshake = {}
            bridge_version = str(handshake.get("version") or "")
            if bridge_version and bridge_version != __version__:
                checks.append({
                    "name": "bridge_version",
                    "status": "warn",
                    "message": f"Bridge {bridge_version} does not match hermes-plus {__version__}",
                    "hint": "Run 'hermes-plus install' to refresh the bridge plugin.",
                })
            else:
                checks.append({
                    "name": "bridge_version",
                    "status": "ok",
                    "message": f"Bridge {bridge_version or __version__} registered "
                               f"{len(handshake.get('features') or [])} features",
                    "hint": "",
                })

    # Check 6: plus.db schema version vs the code's
    db_path = p_home / "plus.db"
    if not db_path.exists():
        checks.append({
            "name": "plus_db",
            "status": "warn",
            "message": f"plus.db does not exist at {db_path}",
            "hint": "Run 'hermes-plus install' to create and migrate database.",
        })
    else:
        current_schema = None
        try:
            from hermes_plus.store import open_store

            store = open_store(p_home)
            if hasattr(store, "schema_version"):
                current_schema = store.schema_version()
        except Exception:
            pass

        if current_schema is None:
            try:
                import sqlite3

                with sqlite3.connect(db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT value FROM plus_meta WHERE key = 'schema_version'"
                    )
                    row = cursor.fetchone()
                    if row:
                        current_schema = row[0]
            except Exception:
                pass

        if current_schema is not None:
            checks.append({
                "name": "plus_db",
                "status": "ok",
                "message": f"plus.db schema version: {current_schema}",
                "hint": "",
            })
        else:
            checks.append({
                "name": "plus_db",
                "status": "warn",
                "message": "plus.db exists but schema version could not be determined",
                "hint": "Run 'hermes-plus install' to apply migrations.",
            })

    # Check 7: ui_dist/index.html present
    ui_dir = Path(__file__).resolve().parent / "ui_dist"
    index_file = ui_dir / "index.html"
    if index_file.exists():
        checks.append({
            "name": "ui_dist",
            "status": "ok",
            "message": f"Web UI found at {index_file}",
            "hint": "",
        })
    else:
        checks.append({
            "name": "ui_dist",
            "status": "fail",
            "message": f"Web UI not found: {index_file} is missing",
            "hint": "Build UI with 'npm run build:webapp' in packages/ui or install pre-built wheel.",
        })

    # Check 8: default port is free
    port = getattr(args, "port", None) or 9129
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_free = False
    try:
        sock.bind(("127.0.0.1", port))
        port_free = True
    except OSError:
        port_free = False
    finally:
        sock.close()

    if port_free:
        checks.append({
            "name": "default_port",
            "status": "ok",
            "message": f"Port {port} is free",
            "hint": "",
        })
    else:
        checks.append({
            "name": "default_port",
            "status": "fail",
            "message": f"Port {port} is already in use",
            "hint": f"Stop the process using port {port} or configure a different port with --port.",
        })

    # Check 9: free disk above 1G
    disk_target = h_home if h_home.exists() else Path.cwd()
    try:
        free_bytes = shutil.disk_usage(disk_target).free
        free_gb = free_bytes / (1024**3)
        if free_bytes >= 1024 * 1024 * 1024:
            checks.append({
                "name": "free_disk",
                "status": "ok",
                "message": f"Free disk space is {free_gb:.2f} GB (minimum 1.0 GB required)",
                "hint": "",
            })
        else:
            checks.append({
                "name": "free_disk",
                "status": "fail",
                "message": f"Free disk space is low: {free_gb:.2f} GB (minimum 1.0 GB required)",
                "hint": f"Free up disk space on the partition hosting {disk_target}.",
            })
    except Exception as exc:
        checks.append({
            "name": "free_disk",
            "status": "warn",
            "message": f"Unable to check free disk space: {exc}",
            "hint": "",
        })

    # Output formatting
    if getattr(args, "json", False):
        print(json.dumps(checks, indent=2))
    else:
        print("Hermes Desktop Plus Doctor\n" + "-" * 30)
        for c in checks:
            tag = f"[{c['status'].upper():4}]"
            print(f"{tag} {c['name']}: {c['message']}")
            if c["status"] != "ok" and c["hint"]:
                print(f"       -> Fix: {c['hint']}")

    has_fails = any(c["status"] == "fail" for c in checks)
    return 1 if has_fails else 0


def _extract_version_from_tag(tag: str) -> str:
    """Extract semantic version from a git tag.

    Args:
        tag: Git tag (e.g., "v0.1.0" or "0.1.0").

    Returns:
        The semantic version without "v" prefix (e.g., "0.1.0").
    """
    return tag.lstrip("v")


def release_api_base() -> str:
    """The release API base: HERMES_PLUS_RELEASE_API, else GitHub."""
    return os.environ.get(RELEASE_API_ENV, "").strip().rstrip("/") or DEFAULT_RELEASE_API


def release_repo() -> str:
    """The release repo slug: HERMES_PLUS_RELEASE_REPO, else DEFAULT_GITHUB_REPO."""
    return os.environ.get(RELEASE_REPO_ENV, "").strip() or DEFAULT_GITHUB_REPO


def release_api_url(repo: str) -> str:
    """The latest-release endpoint for ``repo`` on the configured API base."""
    return f"{release_api_base()}/repos/{repo}/releases/latest"


def _resolve_github_release_tag(repo: str, timeout: int = 5) -> str | None:
    """Resolve the latest release tag for a repo.

    Args:
        repo: repo in owner/name format (e.g., DECRUX9812/hermes-desktop-plus).
        timeout: HTTP request timeout in seconds.

    Returns:
        The tag name (e.g., "v0.1.0"), or None when the repo has no published
        release (HTTP 404) — an install that has never published one is a normal
        state, not a failure.

    Raises:
        ReleaseLookupError: the endpoint could not be reached, or answered with
            something other than a release document. The message names the URL.
    """
    import urllib.error
    import urllib.request

    url = release_api_url(repo)
    try:
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ReleaseLookupError(f"Could not fetch latest release from {url}: {exc}") from exc
    except urllib.error.URLError as exc:
        raise ReleaseLookupError(f"Could not fetch latest release from {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseLookupError(f"Invalid JSON from {url}: {exc}") from exc
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not tag:
        raise ReleaseLookupError(f"Latest release at {url} has no tag_name")
    return tag


def _build_wheel_url(repo: str, tag: str, version: str) -> str:
    """Build a wheel URL for a GitHub release.

    Args:
        repo: GitHub repo in owner/name format.
        tag: Release tag (e.g., "v0.1.0").
        version: Package version (e.g., "0.1.0"), used to construct the wheel filename.

    Returns:
        The full URL to the wheel on GitHub.
    """
    return (
        f"https://github.com/{repo}/releases/download/{tag}/"
        f"hermes_plus-{version}-py3-none-any.whl"
    )


def _detect_upgrade_command(
    repo: str | None = None,
    version: str | None = None,
    source: str | None = None,
) -> list[str]:
    """Detect installation method and build upgrade command.

    Args:
        repo: GitHub repo in owner/name format (defaults to DEFAULT_GITHUB_REPO).
        version: Package version for wheel filename construction (defaults to hermes_plus.__version__).
        source: Override the package source (wheel URL, directory, git URL, or package name).

    Returns:
        A command suitable for the tool manager (uv tool install/upgrade or pipx install/upgrade).

    Raises:
        NoPublishedRelease: no release is published for ``repo``, so there is no wheel to upgrade to.
        ReleaseLookupError: the release endpoint could not be read.
    """
    from hermes_plus import __version__

    if repo is None:
        repo = release_repo()
    if version is None:
        version = __version__

    prefix = sys.prefix
    uv_tool_dir = os.environ.get("UV_TOOL_DIR", "")
    is_uv = (
        "uv/tools" in prefix
        or (uv_tool_dir and uv_tool_dir in prefix)
        or "uv" in Path(prefix).parts
    )

    pipx_home = os.environ.get("PIPX_HOME", "")
    is_pipx = (
        "pipx" in prefix
        or (pipx_home and pipx_home in prefix)
        or "pipx" in Path(prefix).parts
    )

    is_venv = hasattr(sys, "base_prefix") and sys.prefix != sys.base_prefix

    # Resolve the package source.
    if source is None:
        # Default: fetch the latest release and build its wheel URL.
        tag = _resolve_github_release_tag(repo)
        if tag is None:
            raise NoPublishedRelease(repo, release_api_url(repo))
        version = _extract_version_from_tag(tag)
        source = _build_wheel_url(repo, tag, version)

    # Build the upgrade command based on the detected installation method.
    if is_uv:
        return ["uv", "tool", "install", "--force", source]
    elif is_pipx:
        return ["pipx", "install", "--force", source]
    elif is_venv:
        return [sys.executable, "-m", "pip", "install", "-U", source]
    else:
        return ["pip", "install", "-U", source]


def cmd_update(args: argparse.Namespace) -> int:
    """Detect installation method, upgrade package, re-install plugins, and run doctor."""
    from hermes_plus import __version__

    source = getattr(args, "source", None)
    checking = getattr(args, "check", False)
    try:
        upgrade_cmd = _detect_upgrade_command(source=source)
    except NoPublishedRelease as exc:
        # An unpublished repo (or one whose first release is not cut yet) is a
        # normal state: --check says so and exits 0. A real upgrade cannot run,
        # because there is no wheel to install.
        if checking:
            print("Upgrade Check:")
            print(f"  No published release found for {exc.repo}; installed {__version__}.")
            print(f"  Looked up: {exc.url}")
            print("  Nothing to upgrade to yet.")
            return 0
        sys.stderr.write(
            f"No published release found for {exc.repo}; nothing to upgrade to "
            f"(installed {__version__}).\n"
        )
        return 1
    except ReleaseLookupError as exc:
        sys.stderr.write(f"Error resolving latest release: {exc}\n")
        return 1

    if checking:
        print("Upgrade Check:")
        print(f"  Upgrade command: {' '.join(upgrade_cmd)}")
        profile_flag = f" --profile {args.profile}" if args.profile else ""
        print(f"  Re-install command: hermes-plus install{profile_flag}")
        print("  Health check command: hermes-plus doctor")
        return 0

    print(f"Upgrading hermes-plus with: {' '.join(upgrade_cmd)}...")
    try:
        subprocess.run(upgrade_cmd, check=True)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"Upgrade failed with exit code {exc.returncode}\n")
        return exc.returncode

    # Re-exec hermes-plus install
    print("Re-executing 'hermes-plus install'...")
    install_args = [sys.executable, "-m", "hermes_plus.cli", "install"]
    if args.profile:
        install_args.extend(["--profile", args.profile])
    res = subprocess.run(install_args)
    if res.returncode != 0:
        sys.stderr.write(f"Post-upgrade install failed (code {res.returncode})\n")
        return res.returncode

    # Run doctor
    print("\nRunning health checks...")
    doctor_args = [sys.executable, "-m", "hermes_plus.cli", "doctor"]
    if args.profile:
        doctor_args.extend(["--profile", args.profile])
    return subprocess.run(doctor_args).returncode


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Disable bridge plugin, remove plugin directory, and optionally purge data."""
    h_home = _get_hermes_home(args.profile)
    p_home = _get_plus_home(args.profile)

    # Disable plugin
    try:
        from hermes_plus import adapter

        if hasattr(adapter, "disable_plugin"):
            adapter.disable_plugin("hermes-plus", profile=args.profile)
        elif hasattr(adapter, "run_hermes"):
            adapter.run_hermes(
                ["plugins", "disable", "hermes-plus"], profile=args.profile
            )
    except Exception:
        pass

    # Remove plugin dir
    target_plugin = h_home / "plugins" / "hermes-plus"
    if target_plugin.exists():
        shutil.rmtree(target_plugin, ignore_errors=True)
        print(f"Removed bridge plugin directory: {target_plugin}")
    else:
        print(f"Bridge plugin directory not found at {target_plugin}")

    # Purge if requested
    if args.purge:
        if not args.yes:
            sys.stderr.write("Purging data requires --yes confirmation.\n")
            return 1
        if p_home.exists():
            shutil.rmtree(p_home, ignore_errors=True)
            print(f"Purged hermes-plus data directory: {p_home}")
        else:
            print(f"Data directory {p_home} does not exist.")

    print("Uninstallation complete.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for hermes-plus."""
    parser = argparse.ArgumentParser(
        prog="hermes-plus",
        description="Hermes Desktop Plus: standalone add-on for stock Hermes Agent",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # version
    p_version = subparsers.add_parser("version", help="Show version information")
    p_version.add_argument("--json", action="store_true", help="Output as JSON")
    p_version.set_defaults(func=cmd_version)

    # install
    p_install = subparsers.add_parser(
        "install", help="Install bridge plugin and migrate database"
    )
    p_install.add_argument(
        "--profile", "-p", default=None, help="Hermes profile name"
    )
    p_install.add_argument(
        "--hermes", default=None, help="Path to hermes binary"
    )
    p_install.add_argument(
        "--extras",
        default=None,
        help="Comma-separated extra plugins (e.g. memory,langfuse,backdrops)",
    )
    p_install.add_argument(
        "--import-fork-data",
        action="store_true",
        help="Import historical data from fork install",
    )
    p_install.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Bypass compatibility check on unsupported Hermes version",
    )
    p_install.set_defaults(func=cmd_install)

    # serve
    p_serve = subparsers.add_parser("serve", help="Run the hermes-plus web server")
    p_serve.add_argument("--host", default=None, help="Host to bind (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=None, help="Port to bind (default: 9129)")
    p_serve.add_argument("--profile", "-p", default=None, help="Hermes profile name")
    p_serve.add_argument("--hermes-bin", default=None, help="Path to hermes binary")
    p_serve.add_argument(
        "--upstream-url", "--upstream", dest="upstream_url", default=None, help="Stock hermes upstream URL"
    )
    p_serve.add_argument(
        "--upstream-token-file",
        type=Path,
        default=None,
        help="Path to stock hermes upstream session token file",
    )
    p_serve.add_argument(
        "--auth",
        choices=["token", "password", "none"],
        default=None,
        help="Authentication method (token, password, none; none loopback only)",
    )
    p_serve.add_argument(
        "--password-file",
        type=Path,
        default=None,
        help="Path to password file for password auth",
    )
    p_serve.add_argument(
        "--trust-proxy-headers",
        action="store_true",
        default=None,
        help="Trust reverse proxy headers (e.g. Cloudflare Access)",
    )
    p_serve.add_argument(
        "--ui-dir",
        type=Path,
        default=None,
        help="Path to directory containing compiled static UI bundle",
    )
    p_serve.add_argument(
        "--dev-ui-url",
        default=None,
        help="Upstream URL for Vite dev server proxying",
    )
    p_serve.add_argument(
        "--base-path",
        default=None,
        help="URL base path prefix (default: /)",
    )
    p_serve.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error", "critical"],
        default=None,
        help="Logging level (default: info)",
    )
    p_serve.add_argument(
        "--host-terminal",
        action="store_true",
        default=None,
        help="Enable host terminal (default: true on loopback, false otherwise)",
    )
    p_serve.set_defaults(func=cmd_serve)

    # doctor
    p_doctor = subparsers.add_parser("doctor", help="Run diagnostic health checks")
    p_doctor.add_argument("--json", action="store_true", help="Output checks as JSON")
    p_doctor.add_argument("--profile", "-p", default=None, help="Hermes profile name")
    p_doctor.add_argument("--hermes", default=None, help="Path to hermes binary")
    p_doctor.add_argument("--port", type=int, default=None, help="Port to verify")
    p_doctor.set_defaults(func=cmd_doctor)

    # update
    p_update = subparsers.add_parser("update", help="Update hermes-plus installation")
    p_update.add_argument(
        "--check", action="store_true", help="Print what update would do without executing"
    )
    p_update.add_argument(
        "--source", default=None, help="Package source override (wheel URL, directory, git URL, or package name)"
    )
    p_update.add_argument("--profile", "-p", default=None, help="Hermes profile name")
    p_update.set_defaults(func=cmd_update)

    # uninstall
    p_uninstall = subparsers.add_parser("uninstall", help="Uninstall hermes-plus plugin and data")
    p_uninstall.add_argument("--profile", "-p", default=None, help="Hermes profile name")
    p_uninstall.add_argument(
        "--purge", action="store_true", help="Also delete plus_home database and data"
    )
    p_uninstall.add_argument(
        "--yes", "-y", action="store_true", help="Confirm destructive purge action"
    )
    p_uninstall.set_defaults(func=cmd_uninstall)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
