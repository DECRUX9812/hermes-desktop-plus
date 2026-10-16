# Langfuse Observability Plugin

This plugin ships bundled with Hermes but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

```bash
# Interactive: credentials + PM preparation of the langfuse extra + enable
hermes tools  # → Langfuse Observability
```

Restart Hermes after setup. If dependency preparation fails, retry through
`hermes tools`; do not inject the SDK into the selected environment with pip.
For manual source-checkout setup, see the
[plugin guide](../../../website/docs/user-guide/features/built-in-plugins.md#observabilitylangfuse).

## Required credentials

Set these in **Settings → Plugins → Langfuse** (the gear on the plugin row;
values are stored in `.env`), or in `~/.hermes/.env` directly:

```bash
HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-...
HERMES_LANGFUSE_SECRET_KEY=sk-lf-...
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
hermes plugins list                 # langfuse should show "enabled"
hermes chat -q "hello"              # then check Langfuse for a "Hermes turn" trace
```

Generation observations include the Hermes system prompt when the provider
uses a separate `system` param (Anthropic Messages API). Open an **LLM call**
child span to inspect `role: system` (truncated via the `max_chars` setting).

## Optional tuning

Tuning knobs live in **Settings → Plugins → Langfuse** (or `hermes config set
plugins.entries.langfuse.settings.<key> <value>`): `base_url`,
`environment`, `release`, `sample_rate`, `max_chars`, `max_depth`,
`capture`, `debug` — each with the type, description and default shown in the
settings form.

| setting | default | replaces (deprecated env var) |
|---------|---------|-------------------------------|
| `base_url` | `https://cloud.langfuse.com` | `HERMES_LANGFUSE_BASE_URL` |
| `environment` | (empty) | `HERMES_LANGFUSE_ENV` |
| `release` | (empty) | `HERMES_LANGFUSE_RELEASE` |
| `sample_rate` | `1.0` | `HERMES_LANGFUSE_SAMPLE_RATE` |
| `max_chars` | `12000` | `HERMES_LANGFUSE_MAX_CHARS` |
| `max_depth` | `4` | `HERMES_LANGFUSE_MAX_DEPTH` |
| `capture` | `sanitized` | `HERMES_LANGFUSE_CAPTURE` |
| `debug` | `false` | `HERMES_LANGFUSE_DEBUG` |

The `HERMES_LANGFUSE_*` env vars above are **deprecated** but still honoured
for homes configured before the settings form existed: the plugin setting wins
when set, then the env var, then the default.

`max_depth` controls nested payload capture in both `sanitized`
and `full` modes, including tool arguments and JSON tool results. The root is
depth 0; each dictionary value or array element adds one level. Values beyond
the limit become `<max-depth>`, including scalars. For deeper MCP responses,
set it to a higher non-negative integer (for example, `10`) in the settings
form. Unset or blank values default to `4`; invalid or negative
values log a warning and fall back to `4`. `0` keeps only the root level.
Increasing the depth exports more content and may produce larger traces;
secret redaction, string-length limits, and the 50-item collection limit remain
unchanged. `metadata` mode still omits content.

## Capture modes

The `capture` setting controls how much *content* (prompts, responses,
tool arguments/results) is exported. Structural metadata — IDs, roles, tool
names, token usage, cost, timing — is always captured in every mode.

| mode | behavior |
|------|----------|
| `metadata` | No content. Each content field is replaced by a shape/size stub (`{"omitted": true, "type": "text", "chars": N}`). |
| `sanitized` | **(default)** Content is exported after secret-pattern redaction (API keys, tokens, JWTs, private keys, `password=`-style assignments) and truncation. Redaction runs *before* truncation. |
| `full` | Raw content, truncated only. Explicit opt-in — traces will contain whatever passed through the conversation, including injected memory and file contents. |

The active mode is recorded on every trace as `metadata.capture_mode`.

Note: `sanitized` is pattern-based defense in depth, not a DLP guarantee.
For personal sessions or shared Langfuse projects, prefer `metadata`.

## Error + shutdown coverage

- Failed model requests (`api_request_error` hook) close their generation
  with `level=ERROR`, status code, retry counters, and a capture-mode-scrubbed
  error message. Non-retryable failures also finish the turn trace.
- Session end/finalize closes any still-open traces for that session and
  flushes queued events, so interrupted or tool-only turns don't dangle.

## Disable

```bash
hermes plugins disable langfuse
```
