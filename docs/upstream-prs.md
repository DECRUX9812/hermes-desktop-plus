# Upstream PRs for NousResearch/hermes-agent

Ready-to-paste pull request texts. Each PR is optional: hermes-desktop-plus reaches feature parity without any of them, because the sidecar multiplexer and the bridge plugin cover every need. These PRs would let a future version drop parts of the proxy or make the bridge less clever.

Paths cited here are real paths: upstream paths are from stock `main`, and fork-only paths are marked as such.

## Overview

| # | PR | Type | Needed for parity | Priority |
|---|---|---|---|---|
| 1 | `feat(plugins): let plugins register gateway JSON-RPC methods` | hook | no | medium |
| 2 | `feat(plugins): serve dashboard routers from pip-installed plugins` | hook | no | low |
| 3 | `feat(plugins): register public (credential-free) dashboard paths` | hook | no | medium |
| 4 | `feat(plugins): fire an on_delegation_complete hook` | hook | no | high |
| 5 | `fix(checkpoints): delimiter-safe listing and optional per-turn trailer context` | bugfix | no | low |
| 6 | `fix(docker): stable container reuse fingerprint when skills contain symlinks` | bugfix | no | low |
| 7 | `fix(mcp): hide app-only tools from the model and negotiate MCP Apps extensions` | bugfix | no | low |
| 8 | `fix(qqbot): stop blocking the event loop during media sends and narrow error handling` | bugfix | no | low |
| 9 | `fix(credential-files): report the original skills source for sanitized copies` | bugfix | no | low |
| 10 | `feat(cua): configurable cursor motion style for the cua-driver backend` | bugfix | no | low |

---

## 1. `feat(plugins): let plugins register gateway JSON-RPC methods`

**Problem.** Plugins can register tools, hooks, commands, memory providers and dashboard auth providers, but there is no public way to add a JSON-RPC method to the dashboard gateway at `/api/ws`. The method registry lives in `tui_gateway/server.py` (the `_methods` dict around line 97, `register_method` around line 906) and is a private seam. An add-on that wants new RPCs must either patch that module from inside the Hermes process or run a proxy in front of the dashboard.

**Proposed change.** Add to `PluginContext` in `hermes_cli/plugins.py`:

```python
def register_gateway_method(
    self, name: str, fn: Callable[[dict], Awaitable[Any] | Any]
) -> PluginRegistration:
    """Handle a JSON-RPC method on the dashboard gateway.

    fn receives the request "params" object and returns the result.
    Name must be namespaced as "<plugin-name>.<method>".
    """
    return self._register_entry("gateway_method", name, fn, self._manager._gateway_methods)
```

When the dashboard server loads plugins, it hands the collected mapping to `tui_gateway.server` (one new public function, or a parameter on the existing registry) so each method dispatches like a built-in. Contract validation stays optional, matching `tui_gateway/rpc_dispatch.py`, where methods without a contract still dispatch.

**Backward compatibility.** Purely additive. Built-in method names always win; a duplicate registration raises at plugin load with a clear error, so one plugin cannot shadow core or another plugin.

**Test plan.** A directory plugin registers `demo.echo`. The contract test starts `hermes dashboard` in a temp home, connects a WebSocket client to `/api/ws` with a session token, sends `demo.echo`, and asserts the result round-trips. It also asserts a built-in method still works in the same session, and that registering a name without the plugin prefix fails loudly.

**What hermes-desktop-plus does today without it.** The sidecar's `/api/ws` multiplexer answers the add-on's own method namespaces and forwards everything else to upstream, so no feature waits on this PR. With the hook, those namespaces could move into the bridge plugin and shrink the proxy.

---

## 2. `feat(plugins): serve dashboard routers from pip-installed plugins`

**Problem.** Pip entry-point plugins (entry-point group `hermes_agent.plugins`, discovered by `_scan_entry_points` around line 1518 of `hermes_cli/plugins.py`) get their Python loaded. Dashboard assets, however, are only read from a filesystem root: `<plugins root>/<name>/dashboard/` with a `manifest.json` and an api module whose `router` mounts at `/api/plugins/<name>/` (`hermes_cli/web_server_dashboard.py`, loading around lines 477 to 585, mounting around lines 1025 to 1037). A wheel-installed plugin has no such directory, so it cannot ship dashboard routes or a UI tab.

**Proposed change.** Accept a second entry-point group, `hermes_agent.dashboard_routers`, whose target is an ASGI app or a zero-argument callable returning one:

```toml
# pyproject of the plugin package
[project.entry-points."hermes_agent.dashboard_routers"]
demo = "demo_plugin.dashboard:get_router"
```

```python
# in hermes_cli/web_server_dashboard.py, next to the directory-plugin mounts
router = entry_point.load()
app.mount(f"/api/plugins/{name}", ensure_async_router(router))
```

The manifest (label, icon, tab, slots) can come from a `plugin.yaml` inside the package or from a `manifest()` function in the same module. The existing `plugins.isolation` setting applies unchanged.

**Backward compatibility.** Additive. No existing plugin changes. The mount path stays inside `/api/plugins/<name>/`, and the router is only mounted for plugins in `plugins.enabled`, like directory plugins today.

**Test plan.** Install a wheel that declares the entry point into a scratch venv, enable the plugin in `config.yaml`, start the dashboard, and assert `GET /api/plugins/demo/health` returns the router's response. Disable the plugin and assert the route is gone. Assert a router that raises on import is skipped with a logged warning and does not take the dashboard down.

**What hermes-desktop-plus does today without it.** The sidecar serves the add-on's own HTTP routes in front of upstream, so nothing is blocked. With this PR, small pure-pip add-on features could ship without that route layer.

---

## 3. `feat(plugins): register public (credential-free) dashboard paths`

**Problem.** The dashboard protects routes with a session token (`HERMES_DASHBOARD_SESSION_TOKEN`) or a registered auth provider (`register_dashboard_auth_provider`, `hermes_cli/plugins.py` line 733). A plugin that serves a public page, such as a share viewer or a webhook receiver, has no supported way to exempt its path from authentication. The fork had to patch the auth layer's public-path handling (`hermes_cli/dashboard_auth/` files in the fork inventory) to expose share and mobile pages.

**Proposed change.** Add to `PluginContext`:

```python
def register_public_path(
    self, path: str, *, methods: Sequence[str] = ("GET",), prefix: bool = False
) -> PluginRegistration:
    """Exempt a plugin-served path from dashboard authentication.

    prefix=True exempts the whole subtree. Only paths under the
    plugin's own /api/plugins/<name>/ mount are accepted.
    """
```

The auth middleware merges these exemptions after plugins load. Registered auth providers still see the request; the exemption only means no credential is required.

**Backward compatibility.** Additive and default-deny. Nothing becomes public until a plugin registers it. Restricting registrations to the plugin's own mount stops one plugin from opening core routes.

**Test plan.** A test plugin registers one public GET path. Assert an unauthenticated GET succeeds, an unauthenticated GET on a non-exempt path of the same plugin still fails with the existing 401 or 403 behavior, and a POST to the public path still requires auth because only GET was exempted.

**What hermes-desktop-plus does today without it.** The sidecar is the front door and owns authentication, so share links and mobile pairing work without upstream changes. This PR would let a lighter deployment (plugin only, no sidecar) keep those features.

---

## 4. `feat(plugins): fire an on_delegation_complete hook`

**Problem.** When a delegate tool run finishes, plugins have only generic hooks (`post_tool_call` and friends in `VALID_HOOKS`, `hermes_cli/plugins.py` line 107) and must parse tool output text to learn what happened. The fork added delegation report emission by patching the dispatch path (`tools/async_delegation.py` and `tools/delegate_tool_dispatch.py`, both fork-only files). A structured hook lets plugins record delegation outcomes without touching tool results or dispatch code.

**Proposed change.** Add `on_delegation_complete` to `VALID_HOOKS` and fire it where delegate tool dispatch finishes, with a read-only info dict:

```python
await self._run_hook("on_delegation_complete", {
    "session_id": session_id,       # parent session
    "child_session_id": child_id,   # spawned worker session, if any
    "task": task_summary,           # short text, no secrets
    "status": "ok",                 # "ok" | "error" | "cancelled"
    "tool_call_id": tool_call_id,
})
```

**Backward compatibility.** Purely additive. The hook only runs if a plugin registered it, and hook callbacks already fail soft per plugin, so a broken listener cannot break delegation.

**Test plan.** A test plugin registers the hook and appends the payload to a file in its own directory. A scripted run that delegates a trivial task asserts exactly one event with the parent and child session ids and status `ok`. A run with no delegation asserts no event. An exception inside the listener asserts delegation still completes.

**What hermes-desktop-plus does today without it.** The bridge plugin derives completion from `post_tool_call` on the delegate tool, parses the output, and writes the report to its own database, which the sidecar serves. The dedicated hook removes the output parsing, which is the most fragile part of that path. This is the highest-value hook of the four.

---

## 5. `fix(checkpoints): delimiter-safe listing and optional per-turn trailer context`

**Problem.** Two issues in `tools/checkpoint_manager.py`. First, `list_checkpoints` formats `git log` as `%H|%h|%aI|%s` and splits each line on `|`. A checkpoint reason (the commit subject) containing `|` shifts the fields and corrupts the entry. Second, there is no machine-readable link between a snapshot and the user turn that preceded it, so clients cannot offer a "revert to before this prompt" affordance. Snapshots are commits on a git ref, so no schema migration is involved.

**Proposed change.** Switch the log format to a record separator and parse trailers:

```python
fmt = "%H|%h|%aI|%s%x1e%b%x1e"  # \x1e delimits meta from commit body
# _take appends git trailers in a second -m paragraph when the
# caller supplies turn context:
ensure_checkpoint(working_dir, reason, turn={
    "session": session_id, "turn": turn_number, "row_id": user_row_id,
})
# trailers written: Hermes-Session, Hermes-Turn, Hermes-User-Row
```

`list_checkpoints` pairs meta and body records on the separator, reads the three trailers back into `entry["sid"]`, `entry["turn"]` and `entry["user_row_id"]`, and leaves all existing fields unchanged.

**Backward compatibility.** The `turn` parameter is optional and defaults to `None`, so existing callers are unaffected. Old snapshot refs list as before, just without the new keys. The on-disk format does not change; trailers are ordinary git commit bodies.

**Test plan.** Unit tests against a real git repository in a temp directory: a reason containing `|` and newlines lists intact; `ensure_checkpoint` with a turn writes trailers that `list_checkpoints` reads back; `ensure_checkpoint` without a turn still lists; restore behavior is unchanged.

**What hermes-desktop-plus does today without it.** Nothing is blocked; the add-on stores no checkpoint data of its own. With the turn trailers, a per-message checkpoint restore in the web UI becomes possible without the add-on keeping extra state.

---

## 6. `fix(docker): stable container reuse fingerprint when skills contain symlinks`

**Problem.** For credential-file mounts, `tools/credential_files.py` replaces a skills directory containing any symlink with a sanitized temp copy created per process. `tools/environments/docker.py` hashes mount arguments into the container reuse label (`_reuse_environment_fingerprint`), and the host path of that sanitized copy is random per process. The label therefore differs across processes, and cross-process container reuse never matches for any user with a symlink under `skills/`. The runtime sniff `_is_volatile_mount_spec` tries to detect tempdir-sourced mounts by path, which is brittle and also treats genuine user temp binds as volatile.

**Proposed change.** Mark generated copies at the source instead of sniffing paths. `tools/credential_files.py` entries carry `reuse_source` (the original skills path) when a sanitized copy was made (see PR 9). `_readonly_skill_mount_args` accepts a fingerprint list and emits those mounts in a stable form; `_reuse_environment_fingerprint` hashes that form and `_is_volatile_mount_spec` is deleted:

```python
fingerprint_mounts = [*writable_args, *volume_args]
volume_args.extend(_readonly_skill_mount_args(fingerprint_args=fingerprint_mounts))
# in the fingerprint builder, generated copies hash as
# "<sanitized-skills-copy>:<container_path>:ro" with the original source
self._labels[_ENVIRONMENT_LABEL_KEY] = _reuse_environment_fingerprint(
    image=image, mount_args=fingerprint_mounts, hermes_home=hermes_home)
```

**Backward compatibility.** Only the label content changes. Existing containers stop matching once, so the first run after the upgrade starts one fresh container. No flags or user-visible behavior change otherwise, and user temp binds keep their identity in the hash.

**Test plan.** Unit tests on the fingerprint builder: two sanitized copies at different temp paths with the same `reuse_source` produce the same label; a changed image, container path or home produces a different label. An integration test runs a real container and asserts a second process reuses it; skip when Docker is unavailable.

**What hermes-desktop-plus does today without it.** The add-on runs tools on the host and does not manage Docker environments, so the reuse miss is invisible to it. Upstream users get faster starts from the fix.

---

## 7. `fix(mcp): hide app-only tools from the model and negotiate MCP Apps extensions`

**Problem.** Two gaps around MCP Apps. The app surface itself is a fork-only module (`tools/mcp_apps.py`, listed as a pure backend addition in the fork inventory); the two hunks below are the parts that touch shared files. First, `tools/mcp_tool_registration.py` registers every tool of a connected server as model-callable and writes them all into the schema cache, including tools marked app-only in `_meta.ui.visibility`. The model sees tools that are meant only for the app UI. Second, `tools/mcp_tool_transport.py` creates the client session without extensions, so a server cannot negotiate MCP Apps support on the connection.

**Proposed change.** In `tools/mcp_tool_registration.py`, skip app-only tools during registration and schema-cache writing:

```python
if not model_visible(t):  # from tools/mcp_apps.py
    logger.debug("MCP server '%s': tool '%s' is app-only (_meta.ui.visibility)", name, t.name)
    continue
```

In `tools/mcp_tool_transport.py`, advertise extensions when the SDK accepts them:

```python
if _core._client_session_accepts("extensions"):
    kwargs["extensions"] = CLIENT_EXTENSIONS  # from tools/mcp_apps.py
```

Graceful degradation still holds per the spec: an Apps tool must return usable text, so text-only clients advertising extensions are harmless.

**Backward compatibility.** Servers without `_meta.ui.visibility` on their tools see no change. A server that relied on app-only tools being callable by the model relied on behavior the visibility flag exists to prevent.

**Test plan.** A real stdio MCP server fixture exposes one normal and one app-only tool: assert only the normal tool registers and appears in the schema cache, and assert the connect keyword arguments include extensions when the installed SDK supports them. The app-only tool still runs when invoked through the app surface.

**What hermes-desktop-plus does today without it.** The sidecar runs its own MCP client for app tool cards and does not depend on upstream registration behavior. Without the fix, the model sees UI-only tools on servers that use MCP Apps, which wastes context and invites wrong calls.

---

## 8. `fix(qqbot): stop blocking the event loop during media sends and narrow error handling`

**Problem.** In `tools/send_message_senders.py`, the QQbot sender does blocking filesystem work directly on the event loop: `_qqbot_upload_local_file` resolves and stats the media path with `pathlib`, and `_send_qqbot` calls `os.path.exists` for each media file. On a slow disk or network mount, each call stalls the gateway loop for every connected client. Error handling is also too broad in two places: `_qqbot_api_json` catches a bare `Exception` around `resp.json()`, and `_qqbot_deliver_one_media` catches a bare `Exception` per recipient, which hides programming errors behind "send failed".

**Proposed change.**

```python
# resolve and stat off the loop
local_path = await asyncio.to_thread(_resolve_local_file)
...
if not await asyncio.to_thread(os.path.exists, media_path):
    ...
except ValueError:  # non-JSON error body; the status check below reports it
    data = {}
...
except (httpx.HTTPError, OSError, RuntimeError, ValueError, _UploadError) as exc:
    errors.append(f"{chat_type}: {exc}")
```

**Backward compatibility.** No API change. The same failures produce the same user-facing messages; the loop no longer stalls, and real errors such as an HTTP timeout still surface as send errors instead of being lumped together.

**Test plan.** Unit tests with real files in a temp directory: a missing media file raises `FileNotFoundError`; an async test asserts the send path with a nonexistent local media file produces the skip warning while a concurrent sleep task keeps running (the loop was not blocked). Integration against the real QQ API is gated by credentials and skipped otherwise.

**What hermes-desktop-plus does today without it.** Bot messaging goes through the proxy to upstream `send_message`, so the add-on inherits this bug on stock Hermes. A large media upload currently stalls the dashboard for all connected clients, which users report as the web UI "freezing".

---

## 9. `fix(credential-files): report the original skills source for sanitized copies`

**Problem.** `get_skills_directory_mount` in `tools/credential_files.py` replaces a skills directory that contains any symlink with a sanitized temp copy and returns the mount list. The caller cannot tell a generated copy from a direct bind, because the replacement is invisible in the return value. Any consumer that needs a stable identity for the mount (the Docker reuse fingerprint, PR 6) ends up hashing the per-process temp path.

**Proposed change.**

```python
mounts = []
for source, destination in _skill_dir_roots(container_base):
    safe_source = _safe_skills_path(source)
    entry = _mount(safe_source, destination)
    if safe_source != str(source):
        entry["reuse_source"] = str(source)  # stable identity for reuse labels
    mounts.append(entry)
return mounts
```

**Backward compatibility.** Purely additive dict key on a return value consumed inside the package.

**Test plan.** Unit tests with a real skills tree in a temp directory: a symlink-free directory mounts directly with no `reuse_source`; a directory containing a symlink yields a temp copy plus `reuse_source` equal to the original path; non-regular files behind symlinks are still excluded from the copy.

**What hermes-desktop-plus does today without it.** Nothing on its own; this PR only matters together with PR 6. It is split out because it touches a different module with its own tests.

---

## 10. `feat(cua): configurable cursor motion style for the cua-driver backend`

**Problem.** The cua-driver backend in `tools/computer_use/cua_backend.py` starts sessions with a fixed cursor behavior. There is no `computer_use` config key for cursor motion and no backend action to change it at runtime, so users cannot tune how the agent cursor moves.

**Proposed change.** Read an optional style from config and pass it in the start payload:

```python
def _cua_cursor_motion_style() -> Optional[str]:
    cfg = _computer_use_cfg()
    style = cfg.get("cursor_style") or cfg.get("cursor_motion_style")
    if isinstance(style, str) and style.strip():
        return style.strip()
    motion = cfg.get("cursor_motion")
    if isinstance(motion, dict) and motion.get("style"):
        return str(motion["style"]).strip()
    return None

start_payload: dict[str, Any] = {"session": self._session_id}
if (style := _cua_cursor_motion_style()):
    start_payload["cursor_motion"] = {"style": style}
self._best_effort("start_session failed (continuing anonymous)",
                  self._session.call_tool, "start_session", start_payload)
```

Plus a backend method:

```python
def set_agent_cursor_motion(self, style: str, **kwargs: Any) -> ActionResult:
    return self._action("set_agent_cursor_motion", {"style": str(style), **kwargs})
```

Unknown styles degrade safely: cua-driver validates, and a failed start stays best-effort anonymous.

**Backward compatibility.** Additive config keys default to unset. Backends that do not implement the new action are unaffected; callers that never call it never trigger it.

**Test plan.** Unit tests on the config reader covering the string form, the nested dict form, unset, and whitespace-only values. A driver-backed test asserts the start payload carries the configured style and that `set_agent_cursor_motion` issues the right action name. Skip when no cua-driver is installed.

**What hermes-desktop-plus does today without it.** The add-on renders the computer-use timeline but sends no cursor configuration, so there is nothing to migrate. Without this PR, users cannot tune cursor motion from `config.yaml`.
