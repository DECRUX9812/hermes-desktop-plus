# Holographic Memory Provider

Local SQLite fact store with FTS5 search, trust scoring, entity resolution, and HRR-based compositional retrieval.

## Requirements

None — uses SQLite (always available). NumPy optional for HRR algebra.

## Setup

```bash
hermes memory setup    # select "holographic"
```

Or manually:
```bash
hermes config set memory.provider holographic
```

## Config

Holographic is configured through stock Hermes's **memory-provider settings
flow**, which is the sanctioned settings surface for memory providers — the
same contract every bundled provider follows:

- `hermes memory setup` prompts for the fields declared in
  `get_config_schema()` (the plugin overrides it), and persists non-secret
  values through the plugin's `save_config()` override, which writes via the
  canonical `hermes_cli.config.save_config` writer.
- The provider contract reserves exactly this pair:
  `agent/memory_provider.py` (`get_config_schema` — "Setup fields for `hermes
  memory setup`"; `save_config` — "Write non-secret setup values to the
  provider's native config. Plugins MUST either override this or use only env
  vars"). The Desktop's memory config panel is driven by the same schema.

The plugin deliberately declares **no** `config_schema` in `plugin.yaml`:
settings for memory providers live under this flow, not the generic plugin
settings form.

Config in `config.yaml` under `plugins.hermes-memory-store`:

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `$HERMES_HOME/memory_store.db` | SQLite database path |
| `auto_extract` | `false` | Auto-extract facts at session end |
| `default_trust` | `0.5` | Default trust score for new facts |
| `hrr_dim` | `1024` | HRR vector dimensions |

## Tools

| Tool | Description |
|------|-------------|
| `fact_store` | 9 actions: add, search, probe, related, reason, contradict, update, remove, list |
| `fact_feedback` | Rate facts as helpful/unhelpful (trains trust scores) |
