# Agent provider configuration

Oh-Goon defaults to the installed Codex CLI for managed coding tasks. Use its existing sign-in with the explicitly configured trusted local profile below; Codex retains authentication and refresh authority. No local model server is needed. OpenCode and Pi remain explicit native-agent choices. Oh-Goon also supports configured model endpoints without installing their servers or provider SDKs. Direct OpenAI connections use API keys.

## Run a native coding agent

`oh-goon ai` runs the installed Codex CLI through the existing Fleet
Run and Step lifecycle. `oh-goon-ai` is the same command with `ai` supplied.
Select a configured execution profile and start from a Git repository root, or
select the repository explicitly. The `signed-in` profile is defined in
[Use an existing Codex sign-in](#use-an-existing-codex-sign-in):

```sh
oh-goon ai --config providers.json --profile signed-in --prompt-file task.txt --headless --verify "python3 -m unittest discover" --json
oh-goon ai --agent pi --config providers.json --profile company --prompt-file task.txt --headless
oh-goon-ai --agent opencode --config providers.json --profile company --workspace /path/to/project --prompt "Review the failing test"
oh-goon ai --agent codex --config providers.json --profile company-responses --prompt-file task.txt --headless --verify "uv run pytest tests/test_example.py" --json
```

Omitting `--agent` selects `codex`; an explicit `--agent` is preserved. Agent
selection does not discover native authentication, change the selected profile or
start a model server. New native `templates init` selections also name Codex
with the `isolated-managed` profile; existing `run.yml` selections stay explicit.
Missing configuration fails before work.

Broker-backed native reasoning requires an explicit managed inference connection so qualified
Hashmarks context reaches every model request, including follow-up and compaction.
OpenCode and Pi support the selected Chat Completions, Responses or Anthropic wire
protocol; Codex requires Responses. An Ollama server may provide its compatible
OpenAI endpoint (`/v1`); its native `/api/chat` protocol is retained for existing
direct-model Steps and cannot be selected for these native adapters. The endpoint
must also support the selected agent's native tool calling and replay behavior;
a compatible API path alone does not establish coding-task compatibility.

The default execution profile remains `isolated-managed`, using the catalog's explicit
`default_model` and the existing Bubblewrap backend. A missing model selection
fails before work. Broker-backed reasoning cannot use the general `local` profile:
host networking would let native project settings bypass required context
delivery. Ordinary trusted-local Plan verification keeps its existing default.
On a POSIX terminal the default is the native TUI.
`--headless` selects native JSON events; `--json` also selects headless execution
and prints the terminal Run detail. `--interactive` requires a terminal and
conflicts with `--headless` and `--json`. Ctrl-C requests Run cancellation and
waits for its result. The command prints the Run ID and waits. Failed Runs return
a nonzero exit status and a bounded tail of transient stderr; JSON stdout remains
valid Run detail. When transient stderr is absent, the diagnostic reports recorded
failed Step exits and bounded native failure claims, including Pi stop reasons.
These claims do not replace verification results. Producer stdout is not dumped
by the failure diagnostic. Safe Codex service controller failures also enter the
existing private diagnostic JSONL, independently of live-output capture. The
record includes Run, repository, Step and attempt identities plus bounded,
redacted control frames. It excludes native exception chains, source lines,
locals and raw protocol bodies. The diagnostic sink rotates at 5 MB with three
backups; it is supporting debug evidence, not completion or recovery authority.
Completed user-visible Codex messages and command actions/results also enter this
sink incrementally as explicitly untrusted claims. Only the public command,
exit-code and aggregated-output fields are selected; reasoning items and raw RPC
bodies are excluded. Complete selected text is redacted before clipping. Retained
service text shares a 28,000-character/127-event budget, reserving one bounded
final handoff event; missing output and omissions remain explicit.

A completed or failed service conversation can retain these bounded claims in
the original Step journal observation. Cancellation, controller interruption or
unknown cleanup can occur before that Step result exists. Their canonical Run
handoff remains available, but must report journal transcript data as unavailable.
The supporting private diagnostic log may retain earlier visible output after
shutdown; rotation or storage failure can make it unavailable. No transcript,
reasoning history or completed cleanup is reconstructed from that log.

Keep exported handoffs, session inputs and stack traces in private storage outside
the source checkout. The root Git ignores cover accidental Run evidence bundles,
handoff JSON and diagnostic logs; they do not redact contents or protect a file
that is already tracked. Review an export before sharing it, even after redaction.

After a session ends, use the same Oh-Goon state root and exact Run ID:

```sh
oh-goon plan handoff FLEET_RUN_ID --json
oh-goon plan outputs FLEET_RUN_ID --json
oh-goon plan events FLEET_RUN_ID --after 0 --json
oh-goon local-state diagnostics-path
```

The first three commands read original durable Run/Game Tape facts, including
recorded source/Step identities, verification outcomes and incomplete cleanup.
The last prints the existing diagnostics path; read its bounded JSONL records
for matching Run/repository/Step IDs. `plan logs` remains transient and may be
unavailable after shutdown. A fresh agent session receives this recorded context
as untrusted data and requires its own phase admission. Codex retains ownership
of native context compaction and continuation; diagnostic readback neither
compacts a session nor grants earlier write permissions.

Every admitted native Run prints its durable `plan handoff` argv reference immediately,
so success, failure, cancellation and an interrupted controller retain the same Run
anchor. The existing journal records canonical Step status, exit, source admission
and available native observations. Visible native final summaries are retained as
redacted untrusted claims up to 4,000 characters, with explicit omission notices;
unavailable summaries and controller diagnostics are labelled separately. Native
claims never upgrade a failed or unrun independent verification check.
The reusable handoff view bounds source admissions, Steps and claim excerpts and
reports every projection omission. Full recorded facts remain in the original
Run events and Game Tape; transient raw output may be unavailable.

To seed a fresh session explicitly from one Run:

```sh
oh-goon ai --agent codex --profile SERVICE_PROFILE --workspace REPOSITORY \
  --handoff-from FLEET_RUN_ID --prompt "Continue the remaining verified task" --headless
```

The selected prior handoff enters the existing resolved Step context as
`UNTRUSTED_PRIOR_RUN_CONTEXT`. It grants no permissions. The new Run captures
current source and resolves fresh admission and native execution authority.
The complete context must fit the existing 65,536-byte phase context bound;
oversize context rejects before new admission instead of silently clipping.
No prior Run or ambient conversation is selected automatically.

### Use an existing Codex sign-in

Select this profile explicitly for trusted local coding tasks. Codex owns its
saved authentication and refresh; Oh-Goon resolves the home path without reading,
copying or exporting credentials. This requires a Codex version supporting
`exec --ignore-user-config`, native permission profiles and `model_instructions_file`
(validated with 0.163.0). No API provider entry or local model server is needed:

```json
{
  "native_agents": {"codex": {"executable": "/path/to/codex"}},
  "native_profiles": {
    "signed-in": {
      "backend": "local",
      "allowed_agents": ["codex"],
      "workspace_roots": ["/path/to/projects"],
      "codex_login": {
        "home": "/path/to/existing/.codex",
        "model": "gpt-6-luna",
        "reasoning_effort": "low"
      }
    }
  }
}
```

```sh
oh-goon ai --profile signed-in --config providers.json --workspace /path/to/projects/app --prompt-file task.txt --headless --verify "python3 -m unittest discover" --json
```

This is an original managed Run with headless report Steps. Oh-Goon configures
Codex's native session instructions file with the exact frozen task, Hashmarks
presentation and Skill; incremental tool context is disabled. Codex owns session,
follow-up and compaction behavior. Portable tests verify the emitted configuration.
The explicitly selected installed-Codex context probe checks privileged instruction
delivery through tool follow-up, native automatic compaction and continuation in
the original Run. Its bounded local fixture selects API-key and ChatGPT
authentication across Responses HTTP and WebSocket transports. Synthetic ChatGPT
workspace discovery selects a different local HTTPS backend origin using a test CA;
request hosts must match that discovered origin. Real credentials are never
needed. WebSocket warm-up must carry the exact privileged instructions;
follow-ups must reference that observed response chain, and continuation must
restore the exact privileged instructions after compaction. A duplicate user
prompt cannot satisfy this proof, and HTTP fallback cannot qualify WebSocket delivery. This proves installed client transport and
synthetic workspace routing, not live account access or model reasoning quality.
The opt-in legacy live coding baseline retains the original local Run and
immutable independent verification. Its private-home refresh checker has been
removed; the test no longer reads credential contents, forces refresh or starts
an account-server helper. It proves neither refresh/persistence nor the new
native-store exclusion. New service readiness/refresh evidence remains pending
and must come from native non-secret protocol facts, without reading stores or
exporting provider credentials.
The native sandbox permission probe remains
separate; selection commands are in [backend test guidance](../backend/tests/README.md#optional-native-qualification).
Model and reasoning effort are explicit native settings. Oh-Goon transports
bounded nonblank values without maintaining a model-effort allowlist; the
installed Codex validates whether the selected model supports the effort. User
config and exec rules are ignored for this invocation, the project is untrusted, and
hooks, plugins, Apps, web search and native delegation are disabled. Native tools
use Codex's restricted filesystem policy: platform binaries and selected runtime
inputs are readable, the working repository and execution scratch are writable,
and tool networking is disabled. Hashmarks follow-up commands retain the loaded
Python runtime's canonical paths and lexical aliases from the existing runtime
mount owner, executing the original interpreter without a system-Python fallback.
The authentication home and every native tool
root must be separate filesystem trees: neither may contain the other, including
through symlink aliases. The home cannot be part of the captured workspace.
Select a complete public Codex installation in that separate tree, retaining its
native distribution layout and companion programs such as `codex-code-mode-host`.
An executable copied alone may start successfully while native tool initialization
fails. Oh-Goon does not reconstruct an installation or copy credentials to repair
it. Native argument/config overrides are rejected.

Git hooks and separate verification Steps retain trusted local execution. This
profile does not provide whole-process Bubblewrap isolation, broker endpoint
enforcement, read-only review roles, interactive execution or resource caps.
Those guarantees remain with the explicitly selected broker/Bubblewrap profile;
there is no automatic fallback between the profiles. Authentication failures and
unsupported native settings fail through the original Step result. The native
connection's disclosure recipient is `codex`; reports remain untrusted claims.

### Native-owned authentication and configuration

Native authentication and personal configuration remain private to the native
service. Oh-Goon and agent tools must not read, write, copy, migrate, archive or
mount those stores. This applies to Codex authentication, Codex configuration,
OpenCode authentication and OpenCode configuration, including aliases and any
operator-selected locations. Oh-Goon-owned task/Skill evidence and its own provider
catalog are separate inputs; they never authorize native-store access.

The proposed protected signed-in auth-file profile is withdrawn. A
`codex_login` selection with `backend: bubblewrap` is rejected before credential
file access. There is no supported `credential_store: file` campaign recipe,
whole-home mount or credential-visible fallback. Earlier synthetic file-bind
experiments establish only their recorded historical protocol/filesystem facts;
they do not qualify the required native-store exclusion or a service adapter.
The existing local CLI profile above remains its separately scoped legacy mode;
it cannot substitute for a protected native-service workflow. These guards remove
named credential/configuration-directory routes; they do not prove that arbitrary
runtime roots, captured source or file arguments cannot alias private native
stores. Current CLI paths are unqualified for the new four-store exclusion.
Unknown separation blocks the protected workflow. Native store conventions stay
with their native owner; Oh-Goon must not add an ambient path blacklist, scan
stores or implement another native-configuration resolver to claim completion.

### Prepare a native service endpoint

`oh-goon agent service prepare` prints an offline recipe. It does not inspect the
executable, socket, native home, settings, credentials or environment secrets;
write configuration; start a process; connect to an endpoint; or submit work.
Its result is `configured_not_connected`, with `started: false` and
`execution_admitted: false`. It creates no native profile or runtime service
selection. The separate connection command below observes bounded readiness only.

Select an absolute public executable path explicitly; PATH discovery and native
version probing are not part of preparation. Codex additionally requires an
explicit absolute socket path. OpenCode rejects `--socket`. For a POSIX operator
setup, create a private directory outside the repository and native stores using
the operating system's `mktemp` (which creates the directory with mode 0700):

```sh
oh_goon_service_directory="$(mktemp -d /tmp/oh-goon-native-service.XXXXXXXX)"
oh-goon agent service prepare --agent codex --executable /opt/native-codex/bin/codex --socket "$oh_goon_service_directory/codex.sock" --json
oh-goon agent service prepare --agent opencode --executable /opt/native-opencode/bin/opencode --json
```

The helper only prints the following native setup contracts, based on the
observed public Codex 0.163.0 and OpenCode 2.1.2 interfaces. It does not establish
the version or compatibility of the executable at a supplied path:

| Agent | Prepared native recipe and remaining owner work |
| --- | --- |
| Codex | Server argv: `app-server --listen unix://PATH`; client proxy argv: `app-server proxy --sock PATH`. Preserve the exact advertised socket path and native symlink/rendezvous validation; Oh-Goon must not manually resolve, replace or repoint it. The native service owns sign-in/configuration. No server or proxy is started by preparation. |
| OpenCode 2.1.2 | Server argv: `serve --stdio --hostname 127.0.0.1 --port 0`. A future owning parent must generate a private ephemeral transport password in `OPENCODE_PASSWORD`, hold the stdin lease, parse bounded first-line JSON containing `url`, and use authenticated `GET /api/info` with Basic username `opencode`. Preparation implements none of that parent lifecycle and prints no secret value. |

OpenCode's `--stdio` is a readiness/ownership channel; the service API still uses
HTTP. Keep its transport credential out of argv, URLs, logs and agent/tool
environments. The readiness client accepts only the exact numeric-loopback HTTP
endpoint and explicit port, without redirects, ambient proxies, discovery or
fallback. Plain HTTP over a non-loopback address is not this recipe. Remote transport and host/process secrecy require
separate qualification. A bounded non-secret version/identity response establishes
only endpoint readiness, never provider sign-in, model availability, native-store
exclusion or protected execution. Do not call settings, auth, session-list or
other broad APIs as a readiness substitute.

### Connect for readiness

After the operator has established the native endpoint, connect explicitly:

```sh
oh-goon agent service connect --agent codex --socket "$oh_goon_service_directory/codex.sock" --json
oh-goon agent service connect --agent opencode --endpoint "$oh_goon_opencode_endpoint" --password-env OH_GOON_OPENCODE_TRANSPORT_PASSWORD --json
```

`oh_goon_opencode_endpoint` must contain the exact selected loopback URL, and the
named environment variable must contain its private transport password. Oh-Goon
validates the endpoint before reading that one variable, freezes the value once,
and does not read native credential or configuration stores. Do not substitute a
provider token. The optional `--timeout-seconds` defaults to 10 and accepts a
positive value up to 60. The whole readiness exchange has a deadline, including
handshake and streamed response delivery; transport cleanup has its own bounded
closure. Failed or uncertain cleanup cannot produce a successful closed report.

Codex connects to the explicitly selected native Unix service and initializes
its protocol. OpenCode performs only authenticated `GET /api/info`; the current
client requires version 2.1.2 and retains only version and PID. Neither CLI path
starts a native service, submits a task, lists sessions, calls auth/config APIs or
adopts an existing conversation. Connection transport closes before the command
returns. A successful report is `connected_not_qualified`, with
`execution_admitted: false` and `connection_closed: true`; native authentication,
phase policy, private-store exclusion and tool quiescence remain unverified.

The [installed Codex service
probe](../backend/tests/integration/services/test_native_codex_service.py) used
0.163.0 with a complete public runtime outside synthetic native stores. Native
service-known profiles denied source/Git mutations, private-store reads/writes
and subprocess escapes during review and re-review; repair changed only the
selected source directory, and scratch output remained available. The original
process owner cancelled the server and verified process-group quiescence. These
are model-free native policy observations, not admission of a service-backed Run:
the reported profile identifier lacks full effective grants/configuration
identity. An empty zero-turn thread starts, but native resume from a fresh
connection refuses it before any persisted turn history. This model-free case
uses no real authentication/configuration files or model turns. Separate
installed local-provider cases below qualify actual native follow-up and compaction;
real-account sign-in/refresh remains unverified.

The [installed OpenCode readiness
probe](../backend/tests/integration/services/test_native_opencode_service.py)
used a real 2.1.2 server with synthetic HOME/XDG state and a fresh transport
password. Its PID matched original command-owner birth evidence; closing the
client left the server available, and original owner cancellation established
process-group quiescence. No session, account or inference operation ran. This
non-stdio probe does not qualify the prepared stdin lease, continuation,
private-store/tool isolation or a real account.

### Native service adapter design

The explicit `codex_service` profile runs individual tasks and bounded
review–repair campaigns through the original native Run/Step path. Each Agent
Step owns a fresh app-server process and private Unix endpoint;
an unrelated existing socket cannot authorize protected work. The original
resolver captures source once, Hashmarks supplies mandatory qualified evidence,
and the exact frozen task/Skill/evidence becomes native developer instructions.
Only task text becomes user input. Codex owns tool follow-up and automatic
compaction; Oh-Goon does not implement another reasoning loop.

```json
{
  "native_agents": {
    "codex": {
      "executable": "/opt/native-codex/bin/codex",
      "runtime_roots": ["/opt/native-codex"]
    }
  },
  "native_profiles": {
    "native-service": {
      "backend": "local",
      "allowed_agents": ["codex"],
      "workspace_roots": ["/path/to/projects"],
      "codex_service": {
        "home": "/path/to/existing/native-codex-state",
        "model": "gpt-6.1-sol",
        "reasoning_effort": "low"
      }
    }
  }
}
```

```sh
oh-goon ai --agent codex --profile native-service --config providers.json --workspace /path/to/projects/app --prompt-file task.txt --headless --verify "python3 -m unittest discover" --json
```

Use a complete public native installation outside native state, retaining its
companion programs. Existing sign-in and personal configuration stay native-owned;
Oh-Goon neither opens their contents nor transfers credentials. The explicit
`home` is a directory boundary, not credential material. It must already exist
outside source, selected Skill and public runtime trees; metadata separation is
checked before source capture, then the native-reported home must match. Source
capture rejects native project-state contents before opening files rather than
silently omitting them from the admitted scope. Empty reserved directories have
no source members and may remain as native-owned setup artifacts; the capture
walk permits only that empty-directory metadata. It rejects any entry inside
before opening content. Oh-Goon never deletes or reclassifies native stores to
make a later capture pass. Use native-owned state outside the
captured repository. If a repository commits native configuration, prepare a
separately declared public-source export without reading or materializing those
files. Record the exact exclusions and bind evidence to that exported scope. Do
not expose the original Git object database: read-only Git can still reveal an
excluded file from history. Transfer only reviewed changes back onto the original
tree, preserving its native files. The child receives the selected home and
original owner's provider environment while parent conversation,
tool IPC and version overrides are removed. Native tools receive only the explicit
PATH/TMPDIR environment. Login/config failures remain native failures.

Before the first model turn, the original command owner verifies the selected
binary's `--version` (currently qualified: `codex-cli 0.163.0`). A user-agent version
from readiness is insufficient. Native RPC confirms disabled extension/browser/
computer/delegation features, MCP exclusion on the actual workload thread, and
the selected native profile.
Native-home directory identity, including symlink aliases, must be separate from
every readable tool root. No native file contents are inspected. A disposable
canary verifies native private/read-only denial and allowed scratch output; this
preflight never attempts mutations against real repository files. Unsupported or
inconclusive capability checks fail before model submission, without fallback.

Native homes may retain config-backed MCP registrations. Before creating the workload
thread, one threadless native status snapshot supplies at most 64 exact server IDs;
only those IDs remain in connection-private control memory. The workload thread
receives native per-ID `enabled=false` overrides without changing stored native
configuration or reading its files. A complete native status readback must match
that ID set, report every runtime as `disabled`, and contain empty tool/resource
catalogs before the first model turn. Incomplete, malformed, changed or paginated
inventories, unknown states, and unsupported extension origins fail before model
submission; missing tools alone never proves exclusion. Existing disabled
plugin/app features retain their own checks.

This profile makes no MCP-startup isolation claim. In Codex 0.163.0, the
[status collector](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/codex-mcp/src/mcp/mod.rs)
can eagerly start configured servers, so status discovery is not a names-only
or startup-free operation. Native owns that ordinary initialization and its
credentials; the workload model receives neither discovered server instructions
nor private configuration values. An empty `mcp_servers={}` override retains lower-layer registrations
under native [table merging](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/config/src/merge.rs).
The explicit per-ID overrides preserve each inherited registration and its native
authentication. Native startup metadata is distinct from workload tool authority;
the original daemon supervisor still owns final process closure and quiescence.

The selected native profile denies the filesystem by default, grants only public
runtime/frozen context and the admitted workspace, denies the private endpoint and
native project configuration contents, and disables tool networking. The reserved
`.codex` directory uses native read-only metadata protection with a subtree
content-deny rule; this supports absent directories without creating them in
source. Review is read-only;
ordinary report coding can write its admitted workspace while Git metadata stays
read-only. In the bounded campaign, review, adjudication, proposal and fresh
re-review retain that read-only native policy and the existing typed result
acceptors. The separate original batch apply Step validates the admitted write
roots, candidate identity and every preimage before applying proposal data.
Service review and adjudication pass JSON Schemas derived from their existing
typed records through native output-schema transport, bound to the exact frozen
candidate and scope. Review also binds the reviewer occurrence and Skill identity
and permits explicit reviewed paths or compact complete-scope coverage. The
original acceptors still independently validate the returned records.
Apply, regression and verification Steps retain their existing trusted-local
execution. The controller captures the changed candidate once before subsequent
reasoning and refuses changes outside the admitted repair phase. Select the
existing `repair.review_passes`, `repair.write_roots`,
`repair.timeout_seconds` and `repair.step_timeout_seconds` selections in `run.yml`; each phase remains a separately admitted Fleet Run.
This profile does not provide strict/untrusted execution. It cannot be combined
with `codex_login`, broker inference, native argument/config-directory overrides
or resource caps.

Terminal native turn evidence and process closure are separate requirements. The
original command owner stops the owned daemon and verifies group quiescence;
the actual daemon shutdown/cancellation receipt remains in Step evidence. A
completed read-only native service Step with matching original execution evidence
may carry this requested daemon cancellation; a cancelled or failed turn cannot
advance the campaign. Client closure, compaction ACK or turn completion alone
cannot report Run success.
Raw service protocol stays private, and independent verification remains separate
from native claims. Game Tape keeps bounded original-Step execution facts under
`ORIGINAL_STEP_EXECUTION` separately from the parent
`UNTRUSTED_HARNESS_CLAIM`: configuration/profile identity, observed native version,
disabled features, empty effective MCP catalog, state separation, preflight and process
cleanup. Native Codex can create an empty reserved `.codex` directory in the
workspace during setup; the agent cannot create, rename, remove or populate that
directory. This is a native setup effect, so the profile claims agent-tool
read-only protection rather than whole-process workspace immutability. Existing
CLI profiles keep their current execution path.

| Native service | Protocol to qualify |
| --- | --- |
| Codex | Its [app-server protocol](https://learn.chatgpt.com/docs/app-server), with native-managed authentication, threads/turns and a version-specific effective tool policy. Public Codex 0.163.0 includes an explicit-socket proxy; that does not establish that a usable endpoint is running here. External-token authentication would transfer credential lifecycle to the caller and does not satisfy this design. |
| OpenCode | Its [V2 CLI](https://dev.opencode.ai/v2/docs/cli/) and [V2 API](https://dev.opencode.ai/v2/docs/api) describe the shared native service and owned session operations. Installed 2.1.2 uses `--server`, `OPENCODE_PASSWORD` and `/api/info`; V1 `attach`, SDK and `/global/health` examples do not define this recipe. Qualify the selected server's exact protocol; do not start or replace a shared service implicitly. |

API transport does not enforce filesystem permissions on a server's tools. An
Oh-Goon client inside Bubblewrap cannot constrain tools executed by an unrelated
host service. Each admitted phase needs a qualified tool boundary that denies
native-store access, enforces read-only review/proposal/re-review, and grants only
separately admitted repair roots. OpenCode's
[V2 permission contract](https://dev.opencode.ai/v2/docs/permissions/) leaves
shell execution with host filesystem, process and network authority; selecting
Plan or action rules is insufficient physical protection.
Codex `externalSandbox` likewise delegates enforcement rather than creating it.
A native harness sandbox may be the selected backend mechanism when its actual
phase policy, private-read/write denials and lifecycle are verified. This does not
require another tool router or a redundant enclosing Bubblewrap layer. Qualify the
installed native policy directly first, then reject missing guarantees before
submission. Codex 0.163.0's stable
[read-only policy](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server-protocol/schema/typescript/v2/SandboxPolicy.ts)
does not itself specify private-path read denial; newer documented fields cannot
be assumed available in that installed protocol.

The adapter must bind an explicitly selected endpoint, peer/service identity,
protocol version, workspace and owned native session to the original Run. Keep
service credentials and endpoints outside model/tool environments and input mounts.
Do not scan user stores, adopt unrelated sessions, expose broad service access to
tools, or infer enrollment from a successful health check. Use authenticated local
IPC or an explicitly approved authenticated transport; reject endpoint substitution,
redirects and session/workspace mismatches.

Permit only the session operations needed by the admitted Run. Neither the adapter
nor its tools may retrieve credential/configuration values, change provider login,
use generic file readers against protected stores, install plugin/MCP resources,
or shut down the global server as a convenience. A documented non-secret readiness
response is distinct from token-bearing authentication or full configuration APIs;
agent capabilities must not receive those control-plane routes. OpenCode
publishes configuration, authentication, file and global-control APIs; its broad
service credential is not a narrow agent capability. Filter session events to the
owned session before retention and keep unrelated sessions and raw diagnostics out
of evidence.

A symbolic thread/policy identifier alone is not permission proof. The owned
Codex path binds the original executable, startup policy, private endpoint and
native capability checks to one Step. Its installed local-provider Run probe
checks actual tool follow-up and automatic compaction, retained frozen
Hashmarks/Skill instructions, repository edits, independent verification and
durable Game Tape. A separate native protocol case checks persisted detach/resume,
explicit compaction and another turn. Cold resume must supply the same original
frozen developer instructions; omitting them lost context after native compaction.
The Run profile itself owns one ephemeral task turn and does not expose cross-Run
session adoption or user follow-up. New permission phases require distinct
admission and owned process closure; a live resume cannot downgrade repair rights.

OpenCode still exposes readiness and an explicit protected-execution refusal,
with no submission or continuation methods. Owned Codex cancellation targets its
own Step process tree, never a user's shared service. An abort response or disconnected stream
alone does not establish tool quiescence. Reattachment must verify the same service,
session, workspace and original authority; missing terminal or cleanup evidence
remains unresolved. Never resubmit automatically after ambiguous acceptance, and
never fall back to a CLI file mount or unrestricted service.

Acceptance requires synthetic canaries in all four native-store classes: direct
reads/writes, rename/delete, symlink/hardlink aliases, subprocess/tool/MCP access,
file APIs and auth/config APIs must be denied to agent capabilities without
Oh-Goon inspecting the values. The native service must still authenticate, retain
its configuration and refresh internally. Test effective phase policies, native
follow-up/compaction, scoped repair, read-only re-entry, endpoint substitution,
lease loss, cancellation, crash and reattachment through original Run owners.
Installed Codex/OpenCode protocol checks and a complete real workflow are separate
from mock permission tests. Linux, supported WSL2 and advertised container
combinations require their own evidence. No current result establishes this full
service contract.

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
behavior. Codex and OpenCode reject `configuration_directory` before resolving
or exposing that path, including formerly accepted public configuration directories.
Use their managed bootstrap or qualify the native-service route; do not select a
personal configuration/authentication store through another input. Pi retains its
explicit public non-secret configuration-directory contract. That Pi directory uses
a private copy-on-write overlay; disposable native state cannot modify the host
input. User auth/configuration stores are never deployment inputs or runtime roots.

Model context limits, output budgets and thinking controls belong to the native
agent/provider configuration. Include reasoning tokens in the output budget.
A provider may reject or ignore an unsupported thinking setting; for local
Ollama models, inspect the installed artifact's `/api/show` thinking capabilities
before choosing that setting. Oh-Goon does not change the model, retry with
other settings or interpret native preferences to repair inference. See
[Ollama thinking capabilities](https://docs.ollama.com/capabilities/thinking).

The minimal Codex configuration disables its provider-hosted web search because
the managed profile admits model inference only. Explicit native configuration
remains unchanged; provider-executed tools and attachment retrieval through
remote URLs or uploaded `file_id` references conflict with the broker and are
rejected. This includes top-level `web_search_options`. The managed broker also
rejects the top-level `prompt` field on Responses requests, including null or
invalid shapes, because provider-hosted prompt templates are not admitted.
Inline file bytes, data-image URLs, and native function schemas and arguments
remain supported. Responses
`instructions`, inline `input`, `prompt_cache_key`, `previous_response_id`, and
`conversation` retain their native wire meaning. See the native [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

For Pi's retained explicit configuration surface, native inference must target
`http://127.0.0.1:18743` inside the namespace, use the selected API path/model and either no client key or the dummy
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
not deep custody or immutable runtime certification. Configure MCP with the
installed agent's native schema. For OpenCode 2.1.2, that is `mcp.servers`; a local
server uses `type: "local"` and an argv `command`. The managed session initializes
those configured servers asynchronously; their tools may join a later native
turn. Its `mcp list` diagnostic does not initialize the same
configuration plugins, so an empty list alone cannot establish that the session
has no MCP servers. Verify tool discovery and an actual call through the managed
session; see the [native MCP schema](https://dev.opencode.ai/v2/docs/mcp-servers/)
and the [installed-native probe](../backend/tests/README.md#optional-native-qualification).
Missing capabilities reject before work. A configured workspace root must contain
the selected real Git root;
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

Each backend instance retains at most eight successful namespace capability
probes, keyed by the complete workspace, mounts, write scope, resources and
backend identity. Every request still validates current workspace authority,
explicit mount structure, private-file aliases and broker state before reuse,
and rechecks them immediately before each native launch. Failed probes are never
retained; protected probes also require verified process-tree quiescence. Source
evidence, phase admission, execution and cleanup keep their existing owners and
fresh checks.

OpenCode uses its private `--standalone` server, Pi keeps its native agent loop,
and Codex uses `--no-daemon`. The original process owner provides PTY transport,
resize, cancellation and child cleanup. Native self-spawning and shebang behavior
use native executable paths; their observational receipt does not claim sealed
executable materialization. Bounded completion/tool/context events appear as
untrusted claims in Game Tape, separately from execution and verification results.

Authored workflows use `harness: native` and a `role` of `report`, `review`,
`adjudication` or `proposal`. The implementation is independent of the authored
role. Select `agent.implementation`, `agent.profile`, the repository and Skill
roots, and `agent.acceptance` in one `run.yml`. The selected target supplies the
repository workspace identity; a path alone cannot replace it. The lock binds
the native implementation/profile and effective backend configuration. Changing
a selected authority requires a new lock. No native-specific scheduler or
journal is introduced.

Review/adjudication/proposal roles require the original read-only execution and
typed result contracts. The review–repair workflow selects grouped `repair`
limits in the same config. Native proposals remain data until the original
batch apply Step checks preimages and write scope. Regression/verification
Steps retain their selected profile. Linked Git metadata outside the selected
workspace requires its supported scoped metadata binding.

OH-Goon-managed inference selects a named catalog model with
`agent.model_profile`. The provider catalog remains the sole connection/model
profile owner; native-managed settings remain native-owned. Endpoint and model
parameters freeze before admission, while credential values stay in the original
environment/header authority. A profile or configured endpoint is not proof of
connection, compatible wire protocol, model installation, native authentication,
containment qualification, or admitted permission.

## Configure once

Print the default user configuration path:

```sh
oh-goon agent providers path
```

On Linux it is `$XDG_CONFIG_HOME/oh-goon/providers.json`, falling back to `~/.config/oh-goon/providers.json`. macOS uses `~/Library/Application Support/oh-goon/providers.json`; Windows uses `%APPDATA%/oh-goon/providers.json`. `OH_GOON_PROVIDERS_FILE` selects another file; explicit `--config` takes precedence. The chosen file is the entire catalog. There is no directory discovery, layering or dependency on the database path.

For managed coding with the Codex CLI, configure the explicit
[signed-in profile](#use-an-existing-codex-sign-in). The HTTP provider commands
below configure direct model requests or broker-backed native profiles; they do
not configure native Codex sign-in.

Create a configuration for an explicitly selected OpenRouter model:

```sh
oh-goon agent providers setup --provider openrouter --model PROVIDER/MODEL_ID
```

For a running LiteLLM proxy, use `--provider litellm --model YOUR_MODEL_ALIAS`. An explicitly selected existing Ollama server uses `--provider ollama --model YOUR_INSTALLED_MODEL`. No provider is selected implicitly. `--base-url` selects another endpoint and `--api-key-env` names its credential variable. Setup writes the selected JSON file and a sibling `providers.schema.json` derived from Oh-Goon's configuration model. It creates the directory and refuses to overwrite an existing catalog. Setup does not connect, pull a model or install a server.

The resulting catalog is editable JSON with `$schema` support:

```json
{
  "$schema": "providers.schema.json",
  "version": 1,
  "default_model": "default",
  "providers": {
    "openrouter": {
      "api": "openai-chat-completions",
      "base_url": "https://openrouter.ai/api/v1",
      "api_key_env": "OPENROUTER_API_KEY"
    }
  },
  "models": {
    "default": {"provider": "openrouter", "model": "PROVIDER/MODEL_ID"}
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
