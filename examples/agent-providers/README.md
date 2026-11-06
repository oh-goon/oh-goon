# Agent provider connections

Classification: configuration-example

`oh-goon ai` selects Codex by default; configure its existing sign-in and
`default_native_profile` using the [native profile example](../../docs/AGENT_PROVIDERS.md#use-an-existing-codex-sign-in).
This sample catalog contains HTTP provider connections and named model profiles.
It declares neither `default_model` nor native execution profiles.

Copy [providers.toml](providers.toml) to the path printed by `oh-goon agent providers path`, or select it directly with `--config`. Replace model placeholders with IDs from your provider. This example needs no provider server, credentials or provider SDK to validate:

```sh
oh-goon agent providers validate --config examples/agent-providers/providers.toml
oh-goon agent providers list --config examples/agent-providers/providers.toml --json
```

When a selected endpoint is available, send explicit text:

```sh
oh-goon agent providers request --config examples/agent-providers/providers.toml \
  --model router --prompt "Reply with hello."
```

The selected model profile determines which credential environment variables are
required. Configure only the providers you use; `default_model` is optional when
`--model` selects an existing entry. Here `router` names `models.router`, which
references `providers.openrouter`; it is not a native `ai --profile` selection.
Ollama and LiteLLM run separately and are never installed, launched or searched
for by these commands.

See [Agent provider configuration](../../docs/AGENT_PROVIDERS.md) for configuration, native parameters, custom gateways, Python usage and failure behavior. Provider catalog inspection proves configuration only. Endpoint availability, compatible native profiles and admitted Agent execution remain separate states; normal workflows select their profile in `run.yml`.
