# Agent provider configuration

Oh-Goon can connect to configured model endpoints without installing their servers or provider SDKs. Ollama, LiteLLM, OpenRouter and OpenAI are connection choices, not repository dependencies. OpenAI connections use API keys; ChatGPT/Codex subscription sign-in is outside this interface.

The native [Agentic Review–Repair Loop](AGENT_SKILLS.md#native-agentic-reviewrepair-loop)
uses these profiles through the existing supervised Agent Step. Select a profile
with `--agent-model-profile` on `lock` and `plan run`, or keep `default_model`.
`--agent-providers-file` selects an explicit configuration; otherwise the normal
configuration path applies. Endpoint and model parameters are frozen before
admission; credential values remain in process environment/header authority.
Legacy local-model harnesses retain their explicit Ollama contract. Merely
configuring a provider grants no Step effects or verification authority.

## Configure once

Print the default user configuration path:

```sh
oh-goon agent providers path
```

On Linux it is `$XDG_CONFIG_HOME/oh-goon/providers.json`, falling back to `~/.config/oh-goon/providers.json`. macOS uses `~/Library/Application Support/oh-goon/providers.json`; Windows uses `%APPDATA%/oh-goon/providers.json`. `OH_GOON_PROVIDERS_FILE` selects another file; explicit `--config` takes precedence. The chosen file is the entire catalog. There is no directory discovery, layering or dependency on the database path.

Create a configuration for a model already installed in Ollama:

```sh
oh-goon agent providers setup --provider ollama --model YOUR_INSTALLED_MODEL
```

For a running LiteLLM proxy, use `--provider litellm --model YOUR_MODEL_ALIAS`. `--base-url` selects another endpoint and `--api-key-env` names its credential variable. Setup writes the selected JSON file and a sibling `providers.schema.json` derived from Oh-Goon's configuration model. It creates the directory and refuses to overwrite an existing catalog. Setup does not connect, pull a model or install a server.

The resulting catalog is editable JSON with `$schema` support:

```json
{
  "$schema": "providers.schema.json",
  "version": 1,
  "default_model": "default",
  "providers": {
    "ollama": {"api": "ollama", "base_url": "http://127.0.0.1:11434"}
  },
  "models": {
    "default": {"provider": "ollama", "model": "YOUR_INSTALLED_MODEL"}
  }
}
```

`oh-goon agent providers schema` prints that same model-derived schema. JSON files use the `.json` extension. Existing TOML catalogs remain supported when selected explicitly; the [example catalog](../examples/agent-providers/providers.toml) includes Ollama, LiteLLM, OpenRouter, OpenAI and Anthropic. Provider names and model profile names are user-defined. Model IDs pass unchanged, including slashes or version suffixes. `default_model` is optional when selecting a profile explicitly.

## Validate offline, connect explicitly

```sh
oh-goon agent providers validate
oh-goon agent providers list --json
```

Both commands parse the selected file and validate references, protocols and field types. They do not check for running servers, installed programs, available models or credential values. Their status is `configured_not_connected`, which establishes configuration validity only. Missing or malformed configuration is an error; there is no inferred localhost fallback.

Only `request` connects, using the chosen profile. Replace model placeholders before sending:

```sh
oh-goon agent providers request --model review --prompt "Reply with hello."
printf 'Reply with hello.' | oh-goon agent providers request --model review --prompt-file -
oh-goon agent providers request --model review --prompt-file request.txt --json
```

The client sends only the explicit prompt. It does not discover or attach checkout files, Skills, history or environment values. It reads credentials referenced by the selected provider just before creating an immutable connection snapshot. The HTTP consumer uses that snapshot without rereading configuration or environment defaults.

## Choose a protocol

| `api` | Base URL example | Request appended to base URL |
| --- | --- | --- |
| `ollama` | `http://127.0.0.1:11434` | `/api/chat` |
| `openai-chat-completions` | `http://127.0.0.1:4000/v1` for LiteLLM, or `https://openrouter.ai/api/v1` | `/chat/completions` |
| `openai-responses` | `https://api.openai.com/v1` | `/responses` |
| `anthropic-messages` | `https://api.anthropic.com/v1` | `/messages` |

Select the protocol actually served by the endpoint. Any custom gateway supporting one of these protocols can use its own provider name and base URL. Other protocols require an adapter or a compatible proxy; changing the provider's name does not change its wire protocol. Native providers own model availability, resolution and generation behavior.

For example, an external OpenAI-compatible server needs no Python package in Oh-Goon:

```toml
[providers.company]
api = "openai-chat-completions"
base_url = "https://models.example.com/team/v1"
api_key_env = "COMPANY_MODEL_KEY"
headers_from_env = { "X-Tenant-Token" = "COMPANY_TENANT_TOKEN" }
timeout_seconds = 90

[models.team-review]
provider = "company"
model = "published-model-alias"
max_output_tokens = 2048
parameters = { temperature = 0.2 }
```

`api_key_env` names a variable containing a raw API key. The client sends it as Bearer authentication, or as `x-api-key` for Anthropic. Omit it when your endpoint needs no authentication. `headers_from_env` supports explicitly named additional credential headers. `headers` holds static non-secret metadata; credential headers cannot be pasted there. Configuration errors and transport failures omit credential values and upstream error bodies.

`timeout_seconds` controls native HTTP timeouts and defaults to 60 seconds. `max_request_bytes` and `max_response_bytes` each default to 1 MiB and can be configured up to 16 MiB. These are transfer limits, not model context estimates. TLS verification stays enabled. `ca_file` selects a trusted CA bundle; relative paths are relative to the configuration file. `proxy_url` explicitly selects a proxy. Ambient proxy, netrc and certificate environment variables are not reread by the transport.

## Native generation parameters

`parameters` is an optional mapping of additional native request fields. It does not replace the model, messages, input, stream flag or output-token limit. For Ollama, native options remain under `parameters.options`:

```toml
[models.local-code]
provider = "local"
model = "YOUR_INSTALLED_MODEL"
max_output_tokens = 2048
parameters = { options = { temperature = 0.2, num_ctx = 8192 } }
```

`max_output_tokens` maps to Ollama's `options.num_predict`, Chat Completions' `max_tokens`, Responses' `max_output_tokens`, or Anthropic's `max_tokens`. For a Chat Completions model requiring `max_completion_tokens`, set `chat_token_parameter = "max_completion_tokens"` on its profile. Unknown native parameters remain the provider's responsibility; Oh-Goon does not retry with altered settings.

The current client performs one non-streaming text request and requests identity content encoding so response bytes can be bounded before decoding. Tool-call responses, missing text, truncated or incomplete output, malformed JSON, redirects and HTTP errors fail explicitly. There are no automatic retries, model pulls or fallback providers. JSON output includes the selected profile, provider, requested model, returned model identity when present, text and finish reason. It is model output, not a verification result or Game Tape qualification.

## Use from Python

Resolve once from an explicitly provided environment, then send messages through the connection client:

```python
import os
from pathlib import Path

from oh_goon.core.models.agent_providers import AgentMessage
from oh_goon.integrations.agent_provider_client import AgentProviderClient
from oh_goon.runtime.agent_providers import load_agent_providers

catalog = load_agent_providers(Path("/path/to/providers.toml"))
connection = catalog.resolve("team-review", environment=os.environ)
reply = AgentProviderClient().complete(
    connection, [AgentMessage("user", "Reply with hello.")]
)
print(reply.text)
```

The native Agent Step consumes this same resolved connection through the existing Fleet owners. Its captured source, permitted writes and native verification remain governed by the selected Plan.

These profiles configure the current direct-model transport. The
[Agent runtime inventory](development/AGENT_RUNTIME_OWNERS.md) distinguishes that
transport from a future selected coding agent's native model/configuration owner.

## Protocol references

Configuration is inspired by [OpenCode providers](https://opencode.ai/docs/providers/) and [Pi](https://pi.dev/); their configuration files are not imported or merged. Wire contracts follow [Ollama chat](https://docs.ollama.com/api/chat), [LiteLLM proxy usage](https://docs.litellm.ai/docs/proxy/user_keys), [OpenRouter](https://openrouter.ai/docs/api_reference/overview), [OpenAI Responses and text generation](https://developers.openai.com/api/docs/guides/text), and [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create).
