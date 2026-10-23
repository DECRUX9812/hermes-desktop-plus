# vscode — the VS Code pane plugin

The fork's Code pane (`apps/desktop/src/store/code-pane.ts` + the Electron
`openVsCode` preload) as a browser-capable add-on plugin. The fork's Electron
main process spawns the user's own VS Code and serves it on loopback; the
browser add-on has no preload, so the equivalent here never spawns anything:

- **Embed**: a configured `server_url` (a running code-server / openvscode-server)
  renders in a sandboxed iframe beside the chat, opened on the session's
  workspace folder via the server's `?folder=` parameter.
- **Deep link**: a `vscode://file/...` link hands the folder to the desktop's
  installed VS Code — the pane's ONLY door when `server_url` is empty
  (`deep_link_only`).

## Workspace resolution (the fence)

One folder per session: `<workspace_root>/<session_id>` when that directory
exists, otherwise `workspace_root` itself; an empty `workspace_root` falls back
to the profile home. Every folder — explicit (`folder` param) or session-derived —
is resolved (`..`, symlinks included) and must land under the root; anything
that escapes is an `INVALID_PARAMS` fault. The plugin spawns no processes and
performs no reads outside the fence.

## RPC (`plugin.vscode.*`)

- `resolve` `{session_id?, folder?}` → `{ok, folder, server_url, embed_url,
  deep_link_url, deep_link_only, side, width, cli_enabled}` (`ok: false,
  reason: "workspace-missing"` for an explicitly requested folder that isn't there).
- `config` → the effective UI-relevant knobs.

All settings are read only through `ctx.get_config` at call time — config.yaml
edits apply without a restart.
