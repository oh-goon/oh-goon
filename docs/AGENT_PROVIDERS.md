# Agent provider configuration

Oh-Goon can connect to configured model endpoints without installing their servers or provider SDKs. Ollama, LiteLLM, OpenRouter and OpenAI are connection choices, not repository dependencies. OpenAI connections use API keys; ChatGPT/Codex subscription sign-in is outside this interface.

## Run a native coding agent

`oh-goon ai` runs an installed OpenCode, Pi or Codex through the existing Fleet
Run and Step lifecycle. `oh-goon-ai` is the same command with `ai` supplied.
Start from a Git repository root, or select it explicitly:

```sh
oh-goon ai --agent pi --config providers.json --profile company --prompt-file task.txt --headless
oh-goon-ai --agent opencode --config providers.json --profile company --workspace /path/to/project --prompt "Review the failing test"
oh-goon ai --agent codex --config providers.json --profile company-responses --prompt-file task.txt --headless --verify "uv run pytest tests/test_example.py" --json
```

All native reasoning requires an explicit managed inference connection so qualified
Hashmarks context reaches every model request, including follow-up and compaction.
OpenCode and Pi support the selected Chat Completions, Responses or Anthropic wire
protocol; Codex requires Responses. An Ollama server may provide its compatible
OpenAI endpoint (`/v1`); its native `/api/chat` protocol is retained for existing
direct-model Steps and cannot be selected for these native adapters.

The native default is `isolated-managed`, using the catalog's explicit
`default_model` and the existing Bubblewrap backend. A missing model selection
fails before work. Native reasoning cannot use the general `local` profile:
host networking would let native project settings bypass required context
delivery. Ordinary trusted-local Plan verification keeps its existing default.
On a POSIX terminal the default is the native TUI.
`--headless` selects native JSON events; `--json` also selects headless execution
and prints the terminal Run detail. `--interactive` requires a terminal and
conflicts with `--headless` and `--json`. Ctrl-C requests Run cancellation and
waits for its result. The command prints the Run ID and waits. Failed Runs return
a nonzero exit status and a bounded tail of transient stderr; JSON stdout remains
valid Run detail. Producer stdout is not dumped by the failure diagnostic.

Tasks accept `--prompt`, `--prompt-file PATH`, or `--prompt-file -` for stdin.
The authored task limit is 4000 characters; file input is bounded before decoding.
Private prepared task, repository and Skill context stay out of child argv.
`--skill PATH` selects a captured Skill source, including its supporting files;
omitted Skills use the task Skill. Larger task context can be supplied as Skill
resources. Repeat `--verify` for separate native commands. Each value is an
argument vector without a shell; inline programs and shell operators retain the
existing Plan compiler restrictions. Native verification determines results.

The existing provider catalog also holds native execution policy:

```json
{
  "version": 1,
  "default_model": "approved-code",
  "providers": {
    "company": {
      "api": "openai-chat-completions",
      "base_url": "https://models.example.com/v1",
      "api_key_env": "COMPANY_MODEL_KEY"
    }
  },
  "models": {
    "approved-code": {"provider": "company", "model": "company-coding-model"}
  },
  "native_agents": {
    "pi": {
      "executable": "/opt/native-tools/bin/pi",
      "runtime_roots": ["/opt/native-tools"]
    },
    "opencode": {
      "executable": "/opt/native-tools/bin/opencode",
      "runtime_roots": ["/opt/native-tools"]
    }
  },
  "native_profiles": {
    "company": {
      "backend": "bubblewrap",
      "allowed_agents": ["pi", "opencode"],
      "workspace_roots": ["/path/to/projects"],
      "inference_profile": "approved-code",
      "timeout_seconds": 7200,
      "resources": {"memory_mib": 8192, "max_processes": 256}
    }
  }
}
```

Use `--config PATH` for the catalog and `--profile company` for its execution
profile. Relative paths resolve once against the catalog directory. An omitted
executable uses native OS lookup on the supplied `PATH`; Oh-Goon installs no
native packages. `runtime_roots` names the explicit public runtime inputs needed
inside the boundary. Unrelated repositories, host credentials and service sockets
must not be included in these roots. The optional resource caps use native
`systemd-run`; availability and enforcement are checked before work. Local
execution rejects requested caps.

Without explicit native configuration/model arguments, the adapter supplies a
minimal private global native configuration for the selected broker/model.
The native agent retains project configuration precedence and context/tool
behavior. To select existing native preferences, supply
`configuration_directory` and optional native `arguments` on that agent's
catalog entry. These are opaque public native inputs; Oh-Goon does not parse
model settings or discover/copy credential stores. Isolated execution presents
this public directory through a private native copy-on-write overlay so trust,
credential-store initialization and settings writes cannot mutate the host input.
Its private state is discarded with the execution scratch.

The minimal Codex configuration disables its provider-hosted web search because
the managed profile admits model inference only. Explicit native configuration
remains unchanged; remote provider tools or attachments conflict with the broker
and are rejected. See the native [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

Explicit native configuration must target `http://127.0.0.1:18743` inside the
namespace, use the selected API path/model and either no client key or the dummy
key `oh-goon-local` (also supplied as `OH_GOON_NATIVE_BROKER_KEY`). The broker
alone attaches the host credential. Model or
endpoint conflicts fail; execution never falls back to a different model or host
networking. Opaque native configuration requires the isolated boundary. A catalog
without managed inference, including the bare `isolated-offline` profile, cannot
start native reasoning. Offline inference is supported through a configured
approved local model endpoint on the host side of the private broker socket.

Bubblewrap isolates the whole agent and its descendants, including plugins,
MCP servers and native tools. Host/external IP networking, DNS and host-local
services are unavailable; the namespace's own loopback remains available to the
native TUI and inference relay. Selected filesystem inputs are explicit. Requested
read-only roles use a read-only workspace. These are shared-kernel controls,
not deep custody or immutable runtime certification. Missing capabilities reject
before work. A configured workspace root must contain the selected real Git root;
the launcher adds only that repository location and refuses excluded repositories.

A writable target must be separate from the running Oh-Goon source and Python
environment. Use an installed Oh-Goon runtime outside the project being edited.
Overlapping writable targets fail before admission; physically read-only review
can use the same checkout.

Preflight rejects workspace, runtime and native-configuration mount paths that
overlap the active Journal, launch capabilities or private control/evidence
storage. Exact private SQLite artifacts also reject hardlink aliases. Writable
workspaces cannot overlap Oh-Goon's managed extension and tool stores. These
checks use resolved application-owned paths, without discovering unrelated host
state or enumerating private directory contents. A sibling project remains
admissible when its database shares a parent directory.

OpenCode uses its private `--standalone` server, Pi keeps its native agent loop,
and Codex uses `--no-daemon`. The original process owner provides PTY transport,
resize, cancellation and child cleanup. Native self-spawning and shebang behavior
use native executable paths; their observational receipt does not claim sealed
executable materialization. Bounded completion/tool/context events appear as
untrusted claims in Game Tape, separately from execution and verification results.

Authored workflows use `harness: native` and a `role` of `report`, `review`,
`adjudication` or `proposal`. The implementation is independent of the authored
role. `oh-goon lock` and `oh-goon plan run` accept `--native-agent`,
`--native-profile` and `--native-workspace`; use `--agent-providers-file` for the
same catalog. Retain the explicit repository/Skill roots and acceptance options.
The selected native workspace must equal the captured Plan repository. The lock
binds executable, profile and backend configuration; changing them requires a new
lock. There is no native-specific workflow scheduler or journal.

Review/adjudication/proposal roles require headless read-only isolated execution
and the existing typed result contracts. The complete review/repair workflow
uses `--review-passes`, `--repair-write-root`, `--repair-timeout` and
`--repair-step-timeout`. Legacy `--repair-attempts` retains its local single-file
proposal contract. Native proposals remain data until the original batch apply
Step checks preimages and write scope. Regression and verification Steps retain
the selected profile in phases without an Agent. Git observations run in that
profile with a read-only workspace. Linked Git metadata outside the selected
workspace is rejected until it has a supported scoped metadata binding.

The existing direct-model [Agentic Review–Repair Loop](AGENT_SKILLS.md#native-agentic-reviewrepair-loop)
uses model profiles through the supervised Agent Step. Select a profile
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

The `providers` and `models` entries configure direct-model transport and selected
managed inference connections. Native coding agents retain their model and
context machinery. The [Agent runtime inventory](development/AGENT_RUNTIME_OWNERS.md)
describes the separate ownership boundaries.

## Protocol references

Configuration is inspired by [OpenCode providers](https://opencode.ai/docs/providers/) and [Pi](https://pi.dev/); their configuration files are not imported or merged. Wire contracts follow [Ollama chat](https://docs.ollama.com/api/chat), [LiteLLM proxy usage](https://docs.litellm.ai/docs/proxy/user_keys), [OpenRouter](https://openrouter.ai/docs/api_reference/overview), [OpenAI Responses and text generation](https://developers.openai.com/api/docs/guides/text), and [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create).
