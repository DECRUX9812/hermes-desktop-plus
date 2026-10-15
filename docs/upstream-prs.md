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
| 11 | `feat(plugins): let plugins attach display metadata to tool events` | hook | no | high |
| 12 | `fix(session-lifecycle): guard the deferred active-session lease registries` | bugfix | no | medium |
| 13 | `fix(installer): resolve --branch as a git ref on an existing checkout` | bugfix | no | medium |

Numbers are stable once published; 11 to 13 were added after the first ten and are
listed here in the order they were written, not in priority order. PR 11 is the
second-highest-value item after PR 4: it is what lets the bridge attach display
metadata without the sidecar deriving it from event text.


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

---

## 11. `feat(plugins): let plugins attach display metadata to tool events`

**Problem.** `transform_tool_result` is the only hook that sees a finished tool result, and it can do exactly one thing: replace the result string (`model_tools.py`, `_apply_transform_tool_result_hook`, around line 849: the first `str` return wins and is what enters context). Display-only metadata has no seam at all. The `tool.complete` payload is assembled in `tui_gateway/tool_progress.py::_prepare_tool_result_metadata` (around line 305) from data the gateway itself knows, and the same payload is what history rehydration replays. A plugin therefore cannot attach a thumbnail, a retry badge or a risk label to a tool card, and it cannot do so by replacing the result: that string is model-visible context, so changing it for the UI's benefit would alter what the model sees and break the prompt cache.

**Proposed change.** Add `tool_display_metadata` to `VALID_HOOKS` and merge its returns into the payload that `_prepare_tool_result_metadata` already builds:

```python
# in tui_gateway/tool_progress.py, beside the inline_diff / todo merging
with contextlib.suppress(Exception):
    from hermes_cli.lifecycle import has_hook, invoke_hook
    if has_hook("tool_display_metadata"):
        for extra in invoke_hook(
            "tool_display_metadata", tool_name=name, args=args, result=result,
            tool_call_id=tool_call_id, session_id=sid,
        ):
            if isinstance(extra, dict):
                metadata.update({key: value for key, value in extra.items() if key not in metadata})
```

Run-all with first-wins per key, matching how `transform_tool_result` already arbitrates between plugins, and fail-open: a listener that raises must not cost the event. The keys land under the existing payload, so no contract model changes beyond declaring the merge.

**Backward compatibility.** Purely additive. With no listener, `has_hook` is false and the path costs one dict lookup. Existing payload fields keep their current producers' precedence.

**Test plan.** A directory plugin registers `tool_display_metadata` and returns a stable key. A scripted tool call asserts the emitted `tool.complete` payload carries it, that a built-in field (`inline_diff`) still wins for its own key, that a listener raising an exception leaves the event intact, and that the model-visible result string is byte-identical with and without the listener.

**What hermes-desktop-plus does today without it.** The bridge plugin derives display metadata from the events it already taps and stores it in `plus.db`, and the sidecar serves it back to the UI out of band. That works, but it means the desktop reads tool-card metadata from two sources with two lifetimes. PR 1 would let us emit our own event instead, but the renderer and history rehydration both read `tool.complete`, so a parallel event would need a parallel renderer path. This hook is the narrow version: one extra field on the event that already carries the card. It is the second-highest-value item here after PR 4.

---

## 12. `fix(session-lifecycle): guard the deferred active-session lease registries`

**Problem.** `tui_gateway/session_lifecycle.py` keeps two module-level dicts, `_deferred_active_session_leases` (line 629) and `_deferred_active_session_lease_ages` (line 664), holding session leases whose isolated child turn has not settled. They are plain dicts with no lock, and at least four call sites touch them from different threads:

- `_settle_isolated_turn_before_close` (line 649) inserts, on the close RPC path;
- `_reap_stale_deferred_leases` (lines 671 to 677) iterates and pops, called from the reaper tick;
- `_release_deferred_active_session_lease` (lines 696 to 697) pops, from the compute-host turn completion callback;
- `_own_live_lease_ids` (line 279) builds a set from the first dict during the orphan sweep.

`_schedule_ws_orphan_reap` starts its reap on a `threading.Timer` daemon thread (line 1029), so the reaper genuinely runs off the caller's thread. Two consequences follow. `list(_deferred_active_session_lease_ages.items())` at line 671 can raise `RuntimeError: dictionary changed size during iteration` when another thread inserts, which aborts the whole reaper tick. And an unsynchronized read-then-pop at lines 676 to 677 can drop a lease without releasing it (or release it twice), which is exactly the zombie concurrency slot the file's own `#62823` comment describes.

**Proposed change.** One module-level `threading.Lock` guarding every read and write of both dicts:

```python
_deferred_active_session_lease_lock = threading.Lock()
```

Every mutation and the snapshot in `_own_live_lease_ids` take it; the lease object's own `release()` call stays outside the critical section, so the lock never wraps I/O:

```python
with _deferred_active_session_lease_lock:
    return live | set(_deferred_active_session_leases)
```

**Backward compatibility.** Lock only. No signature, payload or user-visible behaviour changes, and the lock is uncontended on the single-threaded paths.

**Test plan.** A behaviour contract, not a source check: two threads, one inserting a lease through `_settle_isolated_turn_before_close`'s deferred path and one running `_reap_stale_deferred_leases` past the TTL, repeated enough times to lose a race on the base. Assert no `RuntimeError` and that the fake lease's `release` was called exactly once per lease. Proven red on the base before the lock.

**What hermes-desktop-plus does today without it.** The sidecar runs the same gateway module in the same process, so it inherits the race; a wedged reaper tick shows up as a session that never frees its concurrency slot. The fix is the one the fork carries (`_deferred_active_session_lease_lock` at `feat/hermes-next @ f13e8fd3ae`), so it is already exercised in a real desktop workload.

---

## 13. `fix(installer): resolve --branch as a git ref on an existing checkout`

**Problem.** `scripts/install.sh` treats `--branch` as a branch on one path and as any ref on another. The usage text says `--branch NAME` (line 68). A fresh install clones with `git clone --branch "$BRANCH"` (lines 733 and 745), which accepts a tag, and that is how a release tag gets pinned. Over an **existing** checkout the same flag goes to `git fetch origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"` (lines 635 and 647), which only accepts a branch: `--branch v0.22.1` fails with a fetch error, and the install stops. Anyone re-running the installer against a checkout it already created, or pinning a tag on a machine that has one, hits it.

**Proposed change.** Resolve the ref before fetching, so a tag and a branch behave the same:

```sh
# resolve "$BRANCH" against the remote once, then use the resolved ref
ref="refs/heads/$BRANCH"
git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1 || ref="refs/tags/$BRANCH"
run_logged "Fetching origin/$BRANCH" git -C "$INSTALL_DIR" fetch origin "+$ref:refs/remotes/origin/$BRANCH"
```

The same resolution feeds the treeless refetch at line 635. A branch name still resolves to `refs/heads/`, so nothing changes for the default `main` path.

**Backward compatibility.** Identical behaviour for a branch name, and the previously broken tag case starts working. No new flag, no new environment variable.

**Test plan.** Two integration cases against a scratch clone: `--branch <release tag>` over an existing checkout lands on that tag's commit, and `--branch main` still tracks the branch tip. Assert the installer exits 0 in both, where the tag case exits non-zero on the base.

**What hermes-desktop-plus does today without it.** `install.sh` pins stock Hermes with `HERMES_PLUS_INSTALL_HERMES=1`, which runs upstream's installer with `--branch "$HERMES_KNOWN_GOOD_TAG"`. On a machine that already has a Hermes checkout, that step fails and the whole add-on install aborts. We work around it by only passing the tag on the path where we know the checkout is fresh, which is a guess we would rather not make.

---

## Candidates considered and not filed

Recorded so the next person does not re-open them. Each was checked against stock `main` before being dropped.

- **Prompt-attachment upload tickets.** The plan made this conditional: file it only if upstream refuses a path outside the session workspace. It does not. `tui_gateway/prompt_attachments.py::_stage_session_file_attachment` (line 199) copies a gateway-visible file outside the workspace into `attachments/`, honours `attachments.storage: workspace` (line 100), and decodes a `data_url` when the file is not on the gateway. The sidecar's upload route already produces a gateway-visible path, so nothing is blocked.
- **`session.create` not writing a `state.db` row.** Deliberate, not a bug. `tui_gateway/methods_session.py` line 772 (line 792 in the fork) says the stored key is minted "intentionally writes no state.db row", and `_ensure_session_db_row` is the documented seeding path. Filing this would be a "fix" that destroys the lazy-row design.
- **A `kanban.task.create` gateway method.** There is no `kanban.*` JSON-RPC method anywhere: `grep -rn '@method("kanban'` matches nothing in stock `main` or in the fork. The kanban dispatcher is internal, so there is no contract to fix and no seam to widen. This gap is ours to close in the sidecar.
- **Profile lifecycle fencing.** The plan's note said a behaviour difference here becomes an upstream PR. There is no behaviour difference to file: the sidecar is the only writer and serializes profile operations through the `hermes profile` CLI, so stock Hermes keeps its own semantics. The fork's larger generation and incarnation rework (`hermes_cli/profile_lifecycle.py`, `hermes_cli/profile_incarnation.py`) is a design change with no single failing repro on `main`, which is below the bar the contribution rubric sets for a bug fix.

