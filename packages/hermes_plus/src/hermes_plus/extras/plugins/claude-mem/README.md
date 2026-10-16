# claude-mem Memory Provider

Local-first persistent memory via the claude-mem worker service
(SQLite + FTS5 + Chroma vector search). No cloud, no API keys, no paid tier.

## Setup

Start the worker, then select the provider:

```bash
npx claude-mem start              # default port 37700
hermes memory setup               # select "claude-mem"
```

## Config

claude-mem is configured through stock Hermes's **memory-provider settings
flow**, which is the sanctioned settings surface for memory providers — the
same contract every bundled provider follows:

- `hermes memory setup` prompts for the fields declared in
  `get_config_schema()` (`host`, `port`).
- The provider contract reserves exactly this pair: `agent/memory_provider.py`
  (`get_config_schema` — "Setup fields for `hermes memory setup`";
  `save_config` — "Plugins MUST either override this or use only env vars
  (every schema field carrying `env_var`)"). claude-mem uses the env-var
  branch: both schema fields carry `env_var` (`CLAUDE_MEM_WORKER_HOST`,
  `CLAUDE_MEM_WORKER_PORT`), and the provider reads them via those env vars.

The plugin deliberately declares **no** `config_schema` in `plugin.yaml`:
settings for memory providers live under this flow, not the generic plugin
settings form.

| setting | env var | default |
|---------|---------|---------|
| `host` | `CLAUDE_MEM_WORKER_HOST` | `127.0.0.1` |
| `port` | `CLAUDE_MEM_WORKER_PORT` | `37700` |

Values can also live in `$HERMES_HOME/claude-mem.json` (`{"host": ..., "port": ...}`),
which wins over the env defaults.

## Tools

| Tool | Description |
|------|-------------|
| `claudemem_search` | Search persistent memory for past observations, decisions, and context |
| `claudemem_remember` | Store a fact worth remembering across sessions |

## Disable

```bash
hermes config set memory.provider ""   # or select another provider
```
