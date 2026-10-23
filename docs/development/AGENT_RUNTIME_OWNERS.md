# Agent runtime ownership

This is the current execution ownership map for [issue #4](https://github.com/oh-goon/oh-goon/issues/4).
Native coding agents consume the shared harness contract through the original
Fleet and Step lifecycle. Current operator behavior is documented in [Agent Skills](../AGENT_SKILLS.md)
and [Agent providers](../AGENT_PROVIDERS.md). The [contributor contract](../../AGENTS.md)
and [Hashmarks handoff](HASHMARKS_EXECUTION_LAYER.md) retain their authority.

## Two different loops

Oh-Goon owns the outer workflow. Findings can lead to adjudication and a
proposal/native apply, then capture of the changed candidate, regression checks
and fresh review within the caller's budget. All-CLEAN reviews select final native
verification; its successful result ends convergence. Other failures, incomplete
evidence and exhausted limits stop the invocation. A selected coding agent
owns its inner reasoning, conversation, compaction, model configuration, tool
choices and solution strategy. Its choices
remain requests or claims under the execution permissions admitted by Oh-Goon.

The implementation supports native OpenCode, Pi and Codex command/event protocols,
alongside the retained bounded model protocols and direct provider requests.
Selecting a protocol does not establish installed native capabilities, accepted
role output, successful verification or host certification.

## Current execution path

| Responsibility | Existing owner and handoff |
| --- | --- |
| Authored Plan, inputs and Skill selection | [FleetPlanResolver](../../backend/src/oh_goon/services/fleet_plan_resolver.py), [Agent authoring sources](../../backend/src/oh_goon/services/agent_authoring_sources.py), and [Skill registry](../../backend/src/oh_goon/services/agent_skill_registry.py) capture the caller-selected source closure. Locked remote Skills retain their selected commits. |
| Immutable delivery | [agent_source.py](../../backend/src/oh_goon/core/models/agent_source.py) owns `FrozenAgentSourceCapture`, `FrozenAgentStepHandoff`, identities and the delivery codec. Captured bytes travel separately from the resolved Plan. A delivery is not effect or disclosure authorization. |
| Captured-source retention and transport | [FleetPlanService._agent_target](../../backend/src/oh_goon/services/fleet_plans.py) stores exact handoff bytes through the existing [FleetContentStore](../../backend/src/oh_goon/integrations/fleet_content_store.py). [FleetPlanTarget](../../backend/src/oh_goon/core/models/target.py) binds exact delivery membership; [FleetPlanRunner](../../backend/src/oh_goon/integrations/fleet_plans.py) verifies availability before effects and supplies `agent_delivery_for`. These input objects are distinct from transient producer/model output. |
| Typed Agent resolution | [fleet_plan_step_resolution.py](../../backend/src/oh_goon/services/fleet_plan_step_resolution.py) verifies the frozen selection and constructs [ResolvedLocalSearchAgentStep](../../backend/src/oh_goon/core/models/fleet_plan.py). `harness: native` separates report/review/adjudication/proposal role from selected implementation. Review roles require a physically read-only workspace; report editing has explicit writable workspace intent. Authored tool, egress and nested-strict demands remain rejected. |
| Run admission and original source custody | [fleet_run_lifecycle.py](../../backend/src/oh_goon/services/fleet_run_lifecycle.py) owns Run admission. Native reasoning requires the selected ordinary isolated profile; retained built-in Agents use standard-local execution. The separate `reserve_original_agent_source_for_attempt` capability requires exact strict deployment/source authority; it is not an ordinary startup prerequisite. |
| Attempt and Step execution | [fleet_plan_run_execution.py](../../backend/src/oh_goon/integrations/fleet_plan_run_execution.py) supplies the existing Step runtime; [fleet_plan_step_execution.py](../../backend/src/oh_goon/integrations/fleet_plan_step_execution.py) verifies delivery digest, occurrence, harness, model and timeout before dispatch. Strict Agent Step execution is currently rejected. |
| Repository evidence before reasoning | The original Step obtains Hashmarks task evidence through [hashmarks/agent_tools.py](../../backend/src/oh_goon/integrations/hashmarks/agent_tools.py). The producer owns repository relevance, presentation and qualifications. Evidence failure stops model disclosure; retrieval bounds cannot narrow the admitted review scope. |
| Physical processes and native tools | The Step's admitted command runner uses [engine/command.py](../../backend/src/oh_goon/engine/command.py) and its existing process-tree/runtime owners. Model and tool subprocesses receive the original cancellation, supervision and timeout parameters. Native tools retain their own semantics. |
| Native invocation and terminal | [fleet_plan_native_steps.py](../../backend/src/oh_goon/integrations/fleet_plan_native_steps.py) consumes the shared input and adapts native events; [agent_native_execution.py](../../backend/src/oh_goon/integrations/agent_native_execution.py) translates the frozen selection into native argv. The original command owner provides bounded stdin and PTY transport. There is no separate native process manager. |
| Ordinary isolation and inference | [fleet_plan_process_isolation.py](../../backend/src/oh_goon/integrations/fleet_plan_process_isolation.py) transports the frozen profile into the existing Bubblewrap backend. The [broker](../../backend/src/oh_goon/integrations/agent_inference_broker.py) admits only the selected API/model/destination; its [relay](../../backend/src/oh_goon/integrations/agent_inference_relay.py) exposes namespace-local HTTP over a fixed private Unix socket. Host credentials remain outside that workload. Explicit `codex_login` profiles use the native command adapter's Codex permission policy and saved sign-in, with trusted local Git/verification; they do not claim whole-process isolation or broker endpoint enforcement. |
| Typed claims and proposal publication | [agent_review.py](../../backend/src/oh_goon/core/models/agent_review.py), [agent_review_execution.py](../../backend/src/oh_goon/integrations/agent_review_execution.py), [agent_patch_apply.py](../../backend/src/oh_goon/integrations/agent_patch_apply.py), and [fleet_plan_step_publication.py](../../backend/src/oh_goon/integrations/fleet_plan_step_publication.py) accept and publish bounded results. Agent assertions cannot grant writes or establish verification truth. |
| Outer review/repair control | [agent_review_plan.py](../../backend/src/oh_goon/core/agent_review_plan.py) validates phase membership. [fleet_plan_repair.py](../../backend/src/oh_goon/services/fleet_plan_repair.py) selects phase Plans; [FleetPlanService.repair](../../backend/src/oh_goon/services/fleet_plans.py) admits, waits for and cancels the original Fleet Runs. |
| Durable results and presentation | Original repository-effect settlement and Journal owners persist the [OperationReport](../../backend/src/oh_goon/core/models/operation.py). [Step evidence](../../backend/src/oh_goon/integrations/_fleet_plan_step_evidence.py) and [Game Tape projection](../../backend/src/oh_goon/core/game_tape_step_projection.py) retain their existing result authority and read-only presentation roles. |

Standard-local execution supervises host processes and makes no kernel-isolation or
deep-custody claim. Ordinary Bubblewrap profiles enforce shared-kernel namespaces,
explicit filesystem scope and host/external network denial. Selected strict/native qualification remains a separate workload
contract. See the [execution-first decision](../architecture/ADR_EXECUTION_FIRST_PROPORTIONAL_SECURITY.md).

## Live model and harness surfaces

The identifiers below are the current resolved choices, not a new adapter registry.
The [resolved model](../../backend/src/oh_goon/core/models/fleet_plan.py) and
[resolver](../../backend/src/oh_goon/services/fleet_plan_step_resolution.py) remain
the executable authorities.

| Surface | Current behavior | Migration classification |
| --- | --- | --- |
| `local-search` | Deterministic captured-source retrieval; no model recipient. Original preparation and Step execution own the process. | **KEEP** bounded source/tool execution. It is **NOT AN OWNER** of coding strategy or repository relevance. |
| `local-model-search` | One bounded Ollama query-selection request, followed by producer-owned repository retrieval. | **ADAPT** invocation/selection; local query prompting is a **RETIRE candidate** only for workloads replaced by a demonstrated native adapter. |
| `local-model-review`, `local-model-tools`, `local-model-files`, `local-model-skill-resources`, `local-model-skill-loop`, `local-model-proposal` | [_execute_model_agent / _run_local_model_review](../../backend/src/oh_goon/integrations/fleet_plan_step_execution.py) implement the current Ollama prompts and tool/final-decision protocol, retaining inspected paths and bounded history. The model supplies the decisions. Model availability is checked through native Ollama. | Inner protocol/history/strategy are **RETIRE candidates** for replaced modes. **KEEP** source binding, disclosure limits, supervision and result validation. Lightweight built-in model Steps may remain supported. |
| `provider-review`, `provider-adjudicate`, `provider-proposal` | [agent_review_execution.py](../../backend/src/oh_goon/integrations/agent_review_execution.py) constructs complete captured-source/Skill requests and role instructions, launches one supervised HTTP child, and accepts typed phase output. Proposal has no write tool. | **ADAPT** harness-facing transport/presentation. **KEEP** complete-scope requirements and typed result admission; output schemas are product contracts, not duplicated reasoning. |
| Direct model transport | [AgentProviderClient](../../backend/src/oh_goon/integrations/agent_provider_client.py) performs one bounded, nonstreaming text request. It owns no reasoning loop, retry/fallback strategy or repository discovery. | **KEEP** while direct-model Steps and explicit provider diagnostics have consumers. It is **NOT AN OWNER** of workflow, compaction or coding strategy. |
| Provider/model configuration | [runtime/agent_providers.py](../../backend/src/oh_goon/runtime/agent_providers.py) and [AgentConnection](../../backend/src/oh_goon/core/models/agent_providers.py) load one selected catalog, freeze execution/inference selection and resolve explicit credential references. Native configuration remains opaque. | **KEEP** the configuration owner and native configuration authority. |
| Shared harness contract | [AgentHarnessInput / AgentHarnessSubmission](../../backend/src/oh_goon/core/agent_harness_contract.py) bind frozen prework and selected intent, check declared capability support and decode bounded untrusted submissions. The native Step receiver consumes this contract before producer/model execution. | **KEEP** one passive core contract and the original typed acceptors. Its DTOs are **NOT AN OWNER** of execution or accepted phase results. |
| `native` | The resolved native selection chooses OpenCode, Pi or Codex independently of the authored role. Native reasoning, tools and compaction stay inside the selected workload. | **KEEP** the original outer workflow and native implementation ownership. Existing built-ins remain until workload-specific replacement and removal are justified. |
| Explicit provider diagnostic | [cli_agent_providers.py](../../backend/src/oh_goon/cli_agent_providers.py) sends caller-supplied text through `AgentProviderClient.complete` and reports a model reply. It does not attach repository/Skill context or admit an Agent workflow. | **KEEP** the diagnostic; it is **NOT AN OWNER** of Agent execution, verification or certification. |

These classifications apply to responsibilities, not whole-file deletion. The
concrete inner model/tool loop is in the Step integration; the inspected owners
contain no general native-style context-compaction or model-routing subsystem.
[agent_model_selection.py](../../backend/src/oh_goon/core/agent_model_selection.py)
combines replaceable prompt/protocol code with source, disclosure and untrusted-output
checks that remain necessary at the appropriate existing owner.

## Harness data contract (A2)

The existing contract now lives in
[core/agent_harness_contract.py](../../backend/src/oh_goon/core/agent_harness_contract.py),
with the `prepare_agent_harness_input` and `decode_agent_harness_submission`
entry points. It has no services-layer facade, runner, source loader or authority
issuer. Preparation consumes the original frozen delivery; it does not recapture
source or introduce another delivery codec.

The input binds the selected harness identity and capability selection, the exact
resolved occurrence, original execution policy/backend and execution identity,
and optional existing `FleetPlanRunLimits`. Repository candidate, review scope
and Skill identities derive from the same handoff. The `input_identity` binds
these selections without replacing the original source or execution identities.
Disclosure recipients must match the handoff's requested recipients exactly;
that request is not a disclosure grant.

The selected descriptor must support frozen-source input, mandatory Hashmarks
evidence and the selected result role. Required and optional capability selections
are explicit. Missing required support rejects preparation; optional support is
the requested/supported intersection, and unavailable options remain explicit.
There is no fallback implementation. Observations and artifact references are
accepted only when both supported and selected. These
checks establish compatibility of the supplied data, not the presence of an
executable adapter or actual producer evidence.

Native model settings, prompting and context behavior remain owned by the selected
agent. An optional native-configuration identity is an opaque supplied digest;
Oh-Goon does not parse or discover native configuration through this contract.
No credentials or native host paths belong in it. All input remains
`PREWORK_NOT_ADMITTED`; preparing or decoding it does not authorize a model
request, tool launch, repository write or strict execution.

The canonical v2 submission envelope is bounded to 1 MiB and distinguishes
`COMPLETED`, `FAILED` and `INCOMPLETE` reports. Its generic verdicts remain claims.
A phase payload is bounded to 65,536 UTF-8 bytes and is exposed unchanged for the
existing review, adjudication or proposal acceptor; decoding the envelope does
not accept that role result. Generic `CLEAN` cannot replace an accepted complete
review or successful native verification.

Structured observations are bounded untrusted claims. Artifact references use
the existing `PlanContentObjectEvidence` content metadata; they do not grant a
host-path/URL read, create retention authority or prove that bytes were retained.
Actual observations, artifact collection and durable promotion remain with their
existing execution/content owners.

A2 defines this passive data contract. Native invocation consumes it before a
Step starts, verifies the receiving execution intent, and retains the original
typed result acceptors. Data compatibility remains separate from actual native
capability evidence. Ordinary execution gains no strict qualification prerequisite.

## Results, limits and observations

| Fact | Current authority and practical limit |
| --- | --- |
| Review and adjudication | [AgentReviewRecord](../../backend/src/oh_goon/core/models/agent_review.py) binds occurrence, candidate, scope and Skill and accounts for complete reviewed-path membership. `AgentAdjudicationRecord` binds candidate/scope and references the admitted reviews/findings, requiring exact disposition and repair-queue accounting. These remain admitted claims. Generic `CLEAN/FINDINGS/INCONCLUSIVE` submissions cannot replace these records unchanged. |
| Repair effect | Provider and native proposal roles return a bounded batch from a read-only workspace. The separate native apply owner validates write roots, candidate and all preimages before publication; partial publication is INCOMPLETE. Current [batch bounds](../../backend/src/oh_goon/integrations/agent_patch_apply.py) are 65,536 UTF-8 bytes, 32 operations and 32,768 replacement bytes per operation. The independent native report role may edit an explicitly writable workspace; it does not replace typed proposal admission. |
| Local context and actions | [agent_model_selection.py](../../backend/src/oh_goon/core/agent_model_selection.py) separates authored-prompt, complete-context, repository-observation and reply bounds. The Step retains short excerpts and two/four-action budgets according to mode; the Skill loop allows at most four actions. This is bounded history rebuilding, not compaction. Exact captured reads retain a separate 2,048-byte delivery bound. |
| Provider transport | The selected connection owns request/response byte budgets and model parameters. Oversized complete requests fail; they are not silently truncated. The [provider child](../../backend/src/oh_goon/integrations/agent_review_execution.py) separately bounds phase text to 65,536 UTF-8 bytes. Transport has no automatic provider fallback or retry. |
| Deadlines and cancellation | The original Step runner supervises every command. Native Agent timeout includes producer preparation and the complete native conversation. Legacy built-in Agents without repair execution limits retain per-command timeouts; repair adds shared Step/campaign deadlines. Unsupported combinations with a separate admitted watchdog reject before work. |
| Failure translation | [Step dispatch](../../backend/src/oh_goon/integrations/fleet_plan_step_execution.py) propagates cancellation through the original operation lifecycle and converts timeout/output-limit exceptions to bounded failure outcomes. Local model/read failures can become contract failures; [provider execution](../../backend/src/oh_goon/integrations/agent_review_execution.py) preserves a nonzero child result and validates successful output with the typed acceptors. Malformed or misbound claims are rejected; a zero process exit alone cannot establish an accepted Agent result or successful verification. These paths do not promise identical exit codes for every failure. |
| Outer continuation | [AgentReviewRepairCampaign](../../backend/src/oh_goon/services/fleet_plan_repair.py) and [AgentRepairContext](../../backend/src/oh_goon/core/models/agent_repair.py) bound linked phase Runs and fresh-candidate capture. Each Run retains its own result. Campaign control state lives in the invocation; reopening Run evidence does not automatically resume a campaign. Rerun/readmission cannot inherit expired repair limits. |
| Durable evidence | Stream evidence retains hashes, byte counts and line counts. Typed review/adjudication records, bounded native event summaries and actual standard-isolation receipts persist separately. Initial Hashmarks packets are included in transient supervised Step output; there is no durable raw producer packet or model/tool transcript. [Live output](../../backend/src/oh_goon/runtime/game_tape_live_output.py) is bounded process-local state, and [Hashmarks preparation](../../backend/src/oh_goon/integrations/hashmarks/agent_tools.py) owns disposable scratch. |

The byte/action limits above describe this inspected implementation. They are not
portable native-agent capabilities or a second configuration owner. Source owners
remain authoritative. [Game Tape](GAME_TAPE.md) explains historical versus live
visibility; a missing model identity, internal tool event or transcript remains
unavailable rather than being reconstructed from an agent claim.

## Native integration boundaries

The frozen native selection owns its executable path and SHA-256. Tool projections
and planned Game Tape commands use that selection without resolving its name again.
Python remains a separate support dependency for producer evidence and the isolated
inference relay. Attempt and result evidence retain the actual invocation; when
Python launches the relay, its tool name and digest describe Python, while the
native selection remains in the resolved Plan. Historical invocation evidence is
shown as retained and is never rebuilt from current configuration. Strict CI/CD
rejects native Agent Steps before Run admission or physical tool capture, and the strict
host-tool issuer refuses native selections before observation or signing.

The existing owners enforce these contracts:

- **One source and result handoff.** The native receiver consumes the original
  frozen delivery once. Hashmarks operates on that captured repository; the
  selected Skill and complete source manifest are available through read-only
  inputs. Native phase text reaches the existing review, adjudication and batch
  proposal acceptors unchanged. Generic completion cannot replace their records.
- **One execution boundary.** The selected profile survives command-only
  regression and verification phases. Git observations use the same backend
  with a read-only workspace, including repository-configured fsmonitor hooks.
  External linked-worktree metadata is rejected before work until the workspace
  authority exposes a supported scoped binding.
- **Native configuration and tools.** Codex/OpenCode reject explicit
  `configuration_directory` inputs before resolving the path. Pi retains its public,
  non-secret configuration-directory contract. Personal native auth/config stores
  are never inputs or runtime roots. Disposable adapter bootstrap state belongs to
  execution scratch; native service configuration remains native-owned. Native process path semantics, shebangs, model settings,
  tool behavior and configuration precedence retain their native owners.
  Model/destination conflicts fail; there is no fallback provider or profile.
- **Mandatory producer context.** Every admitted broker model request receives
  the exact qualified initial Hashmarks presentation and selected Skill, including
  native follow-up and compaction requests. This is context transport, not
  compaction or relevance selection. Follow-up retrieval uses the existing
  Hashmarks CLI inside the boundary, with a read-only capture and disposable
  native index state. Bounded evidence never narrows a complete review scope.
  Explicit headless local Codex sign-in profiles deliver the same captured
  presentation and Skill as native session base instructions, with incremental
  context disabled. Codex owns request construction, tools and compaction;
  Oh-Goon adds no authentication or agent loop. Its typed home/model/effort
  selection is frozen with the existing profile. The home is never copied,
  parsed, captured as repository source or exposed as a native tool root.
- **Approved managed inference only.** The broker-backed isolated workload has no host/external IP
  networking. Namespace-local HTTP reaches a fixed private Unix socket; the host
  broker permits only the selected API routes and exact model at the frozen
  endpoint. It refuses generic proxying, attachment retrieval through remote URLs
  or provider-hosted `file_id` references, and provider-executed web/MCP tools
  (including top-level `web_search_options`). The broker also refuses the top-level
  Responses `prompt` field for hosted templates. Inline attachment bytes, native
  function payloads, and ordinary Responses
  instructions, cache keys and continuation fields retain their wire meaning.
  Host secrets never enter native argv or state.
- **Original lifetime and result authority.** A single native Step deadline
  includes initial producer work and native reasoning. Original Run cancellation,
  process-tree supervision and result accounting remain authoritative. Native
  conversation state does not become Run retry, recovery or campaign authority.
- **Bounded observations.** Game Tape projects recorded native event kinds,
  occurrence/source/input/implementation identities and explicit omissions.
  These are untrusted claims, distinct from supervisor exit, resource and
  isolation evidence. Prompt/response bodies and raw producer packets remain
  transient output; no durable transcript or producer-packet replay is promised.

Ordinary Bubblewrap evidence describes a shared host kernel, explicit mounts,
the actual selected network mode and native systemd resource caps. It does not establish
immutable runtime bytes, deep custody, installed-model quality or APP PASS.
Workspace, configuration and explicit input binds reject host sockets, FIFOs
and special objects before launch. System runtime links to host IPC outside all
bound system roots remain usable as links; their host endpoints are absent from
the sandbox namespace. Links into another bound runtime root still fail, and
private application file aliases remain forbidden in every bind.
Writable private configuration overlays use native Bubblewrap 0.11 or newer
capabilities; the selected workload rejects when they are unavailable.
The separately selected `sandbox_owner: execution_backend` profile retains the
same physical source owner: source is read-only by default and original
`AgentRepairContext` directory roots grant writes only to the separately admitted
native apply phase. Profile compatibility and actual mount-topology preflight
fail closed. Existing supervisor/Step/campaign owners require observed terminal
quiescence and cleanup; missing historical facts remain unknown.

Native auth/configuration stores are excluded from Oh-Goon and agent tools. The
credential-file mount prototype is withdrawn; protected `codex_login` combinations
reject before accessing a credential file. Synthetic historical file-bind proof
cannot qualify this exclusion. Named-route rejection does not establish that
runtime roots, captured source or file arguments cannot alias native stores;
existing CLI paths remain unqualified for this new guarantee. The native owner
must supply the private-store scope and effective boundary through its supported
protocol, resolved once without store scanning or a parallel path/config resolver.
Unknown separation rejects protected admission. See the
[current native-store contract](../AGENT_PROVIDERS.md#native-owned-authentication-and-configuration).

## Native service integration design

The existing Agent CLI owns offline `agent service prepare`: explicit absolute
public executable selection, a required absolute Codex socket path, and a
versioned native setup recipe. It performs no executable/socket/store/environment
secret reads, configuration writes, process launch, network request, account or
session operation. Output remains `configured_not_connected`, `started: false`
and `execution_admitted: false`; it creates no native profile or runtime consumer.
The separate `agent service connect` command owns explicit readiness connection:
Codex native protocol initialization or OpenCode authenticated `/api/info`. It
reads only the explicitly named OpenCode transport-password variable, once after
endpoint validation. It closes the client transport and reports
`connected_not_qualified` with no execution admission or native-store access. See
the [operator preparation](../AGENT_PROVIDERS.md#prepare-a-native-service-endpoint)
and [connection](../AGENT_PROVIDERS.md#connect-for-readiness) contracts.

Actual service-backed Run execution remains unsupported. It belongs at the
existing native integration boundary and must consume the same frozen harness
input and typed phase acceptors. `runtime/agent_providers.py` remains the future endpoint/protocol
resolution owner; existing Fleet admission and `AgentRepairContext` bind the
workspace, phase and execution capabilities. The native service alone owns
authentication, personal configuration, sessions, tools and compaction. No new
reasoning loop, provider credential store, scheduler or recovery manager is
introduced.

Codex app-server and OpenCode's [V2 CLI](https://dev.opencode.ai/v2/docs/cli/) and
[V2 API](https://dev.opencode.ai/v2/docs/api) are protocols to qualify. Prepared
Codex server/proxy arguments retain the exact native socket path and native
symlink/rendezvous checks. The OpenCode 2.1.2 recipe uses a parent-held stdin lease
and readiness JSON before authenticated HTTP `/api/info`; `--stdio` is not a
stdio-only API. A future parent supplies a fresh private `OPENCODE_PASSWORD`, not
a native provider credential. Its secret must never reach argv, logs, model or
tool environments. Preparation implements no parent lifecycle or native launch
supervision. The separate connection adapter observes readiness;
preparation does not establish the version at a supplied executable path.

No endpoint has been established by preparation, and an already running agent is
not automatically an admitted service. Preserve the operator's existing server;
do not start, dispose or kill it implicitly. The original execution owner must
bind service/peer identity and a single owned native session, supervise its
deadline, cancel that session and retain actual closure or uncertainty. A native
abort acknowledgement is not physical teardown proof.

[CodexServiceClient](../../backend/src/oh_goon/integrations/agent_codex_service.py)
owns its native protocol transport and owned thread/turn continuation methods;
those methods are not wired to original Run admission and do not convert a policy
identifier into permission proof.
[OpenCodeServiceClient](../../backend/src/oh_goon/integrations/agent_opencode_service.py)
owns strict numeric-loopback HTTP authority, bounded readiness, sanitized
version/PID facts and deterministic client closure. It exposes no session
submission or continuation API and refuses protected execution through existing
`CapabilityUnavailable`. No generic service request surface reaches agent tools.

The selected backend must establish the server's actual tool boundary. Sandboxing
only the client does not constrain a pre-existing host server. Native permission
configuration, an OpenCode Plan agent or Codex `externalSandbox` is not independent
proof of read denial or scoped writes. The harness's own sandbox may implement the
selected backend contract when its real phase policy and lifecycle are verified;
no new tool router or redundant sandbox layer is required. Qualify the installed
native mechanism directly before selecting another enforcement provider.
Resolve each phase once, verify its
established scope and re-admit continuation across permission changes. Unsupported
combinations fail before model submission rather than using the withdrawn file
route.

Four private-store classes stay outside every repository capture, native tool
mount, command environment, model payload and retained diagnostic: Codex auth,
Codex configuration, OpenCode auth and OpenCode configuration. The native service
may consume its own stores internally. The Oh-Goon client and agent capabilities
must not retrieve/mutate store values through native auth/config/file APIs.
Documented non-secret native readiness facts do not grant token or configuration
read authority. A broad API
credential or global event feed also needs session and route confinement; never
hand either to native tools or retain unrelated session content.

Pending conformance covers file read/write/rename/delete, aliases, subprocesses,
MCP/plugins and API-mediated access to each private store; native-managed
sign-in/refresh and configuration retention; exact Hashmarks/Skill instructions
through follow-up/compaction; endpoint substitution, workspace/session/lease
mismatch, cancellation, lost terminal evidence and reattachment; then independent
scoped apply and protected re-review. Qualification is per installed protocol,
server/tool backend and Linux/WSL2/container environment. The
[provider guide](../AGENT_PROVIDERS.md#native-service-adapter-design) links the
native protocols and lists the remaining acceptance boundaries.

The [retained workload ADR](../architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md)
continues to govern shared execution. Strict Agent execution remains unsupported;
normal startup does not acquire a strict signer/custody qualification gate.

## Existing proof and its limits

| Boundary | Current regression evidence |
| --- | --- |
| Original Step/model supervision and cancellation | [Original model turn](../../backend/tests/integration/test_agent01b3a_original_fleet_model_turn.py), [typed local Agent Step](../../backend/tests/integration/test_agent01b2a_typed_local_search_step.py) |
| Bounded inner tool protocol and proposal-as-data | [Skill loop](../../backend/tests/integration/test_agent01b3g_bounded_skill_loop.py), [source-bound proposal](../../backend/tests/integration/test_agent01b3i_source_bound_patch_proposal.py), [context bounds](../../backend/tests/unit/core/test_agent_model_selection.py) |
| Complete provider closure and typed output acceptance | [Provider execution](../../backend/tests/unit/integrations/test_agent_review_execution.py), [review/adjudication resolution](../../backend/tests/unit/services/test_agent_review_resolution.py), [provider wire boundaries](../../backend/tests/dependency_complete/test_agent_provider_http.py) |
| Frozen provider selection and credentials | [Provider configuration](../../backend/tests/unit/runtime/test_agent_providers.py), [provider connection](../../backend/tests/integration/services/test_agent_provider_connection.py) |
| Producer qualifications and evidence bounds | [Hashmarks Agent tools](../../backend/tests/unit/integrations/test_hashmarks_agent_tools.py) |
| Passive harness data contract | [Harness contract](../../backend/tests/unit/core/test_agent_harness_contract.py), [effect requests](../../backend/tests/unit/services/test_skill03b_effect_requests.py) |
| Native receiver and complete Run lifecycle | [Native Step contract](../../backend/tests/dependency_complete/test_fleet_plan_native_steps.py), [native Run](../../backend/tests/integration/services/test_native_agent_run.py) |
| Native service readiness | Both explicit clients bound the entire readiness exchange and their transport teardown; safe failures preserve cleanup uncertainty and cancellation. [Codex transport](../../backend/tests/dependency_complete/test_agent_codex_service.py) covers native protocol/continuation and profile pinning. Its opt-in [installed service case](../../backend/tests/integration/services/test_native_codex_service.py) verifies model-free review/scoped-repair/re-review denials, synthetic private stores and original owner cleanup. Native empty-thread resume is refused; persisted continuation and effective-grant identity remain unverified. [OpenCode transport](../../backend/tests/dependency_complete/test_agent_opencode_service.py) covers authority, bounds, safe failures and client closure. The opt-in [installed OpenCode case](../../backend/tests/integration/services/test_native_opencode_service.py) verifies real 2.1.2 readiness, matching original process birth, no global shutdown on client close and original owner cancellation/quiescence using synthetic private state. It does not qualify stdin lease, native continuation, user authentication or private-store/tool isolation. |
| Installed Codex instruction transport | Opt-in [native Codex context](../../backend/tests/integration/services/test_native_codex_context.py): synthetic API-key/ChatGPT authentication × HTTP/WebSocket, privileged task/Skill/producer context through native response chains, automatic compaction and continuation. ChatGPT discovery uses a local HTTPS backend; WebSocket cases reject inference HTTP fallback. Live account access and model quality remain separate. |
| Live Codex account and coding | The [legacy live coding baseline](../../backend/tests/integration/services/test_native_codex_live.py) retains the original local Run and independent verifier; its credential-reading/forced-refresh helper is removed. It does not qualify native-store exclusion. Native-service acceptance remains TODO under the [adapter design](../AGENT_PROVIDERS.md#native-service-adapter-design): non-secret readiness/refresh facts, private-store/tool-boundary qualification and owned coding with independent verification. |
| Ordinary physical boundaries and native resources | [Standard Bubblewrap](../../backend/tests/unit/integrations/test_standard_bubblewrap_isolation.py), [verification and Git observers](../../backend/tests/unit/integrations/test_native_plan_isolation.py) |
| Terminal, inference and observations | [Native terminal](../../backend/tests/unit/engine/test_command_terminal.py), [model broker](../../backend/tests/dependency_complete/test_agent_inference_broker.py), [Game Tape claims](../../backend/tests/unit/core/test_game_tape_native_agents.py) |
| Linked review/repair Runs and source recapture | [Review/repair integration](../../backend/tests/integration/services/test_agent_review_repair_loop.py), [bounded repair](../../backend/tests/integration/test_agent_bounded_repair.py), [capture boundaries](../../backend/tests/unit/services/test_agent_repair_source_capture.py) |
| Installed local model behavior | Opt-in [native model review](../../backend/tests/integration/test_agent01b3c_native_model_review.py) and [native Skill cycle](../../backend/tests/integration/test_agent01b3h_native_skill_cycle.py) |

Synthetic model/HTTP fixtures establish protocol and lifecycle behavior within
their tested scope. Installed-model tests are separate from privileged custody
proofs; they do not establish every native implementation or optional feature. This document
does not promote any test result into APP, native-host or release certification.
