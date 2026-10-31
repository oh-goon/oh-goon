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

The [harness compatibility inventory](AGENT_HARNESS_COMPATIBILITY.md) records
temporary adaptations, their removal conditions and conformance evidence by
harness and mode. Native integration upgrades reassess those entries against the
selected installed implementation; deterministic protocol fixtures alone do not
qualify that implementation.

Harness replacement uses a separately resolved and admitted Run. The explicit
prior-Run handoff supplies bounded, untrusted Journal/Game Tape context to a fresh
native invocation; it neither resumes nor converts another harness's session.
An admitted Step retains its frozen harness selection on failure. Unsupported
capabilities fail explicitly, without substituting a different harness. Native
same-session continuation remains a separate, mode-specific capability.

## Current execution path

| Responsibility | Existing owner and handoff |
| --- | --- |
| Authored Plan, inputs and Skill selection | [FleetPlanResolver](../../backend/src/oh_goon/services/fleet_plan_resolver.py), [Agent authoring sources](../../backend/src/oh_goon/services/agent_authoring_sources.py), and [Skill registry](../../backend/src/oh_goon/services/agent_skill_registry.py) capture the caller-selected source closure. Locked remote Skills retain their selected commits. |
| Immutable delivery | [agent_source.py](../../backend/src/oh_goon/core/models/agent_source.py) owns `FrozenAgentSourceCapture`, `FrozenAgentStepHandoff`, identities and the delivery codec. Captured bytes travel separately from the resolved Plan. A delivery is not effect or disclosure authorization. |
| Captured-source retention and transport | [FleetPlanService._agent_target](../../backend/src/oh_goon/services/fleet_plans.py) stores exact handoff bytes through the existing [FleetContentStore](../../backend/src/oh_goon/integrations/fleet_content_store.py). [FleetPlanTarget](../../backend/src/oh_goon/core/models/target.py) binds exact delivery membership; [FleetPlanRunner](../../backend/src/oh_goon/integrations/fleet_plans.py) verifies availability before effects and supplies `agent_delivery_for`. These input objects are distinct from transient producer/model output. |
| Typed Agent resolution | [fleet_plan_step_resolution.py](../../backend/src/oh_goon/services/fleet_plan_step_resolution.py) verifies the frozen selection and constructs [ResolvedAgentStep](../../backend/src/oh_goon/core/models/fleet_plan.py). Its workload discriminant separates search, builtin model, provider and native intent; each variant carries only its relevant selection. Native report/review/adjudication/proposal roles remain independent of implementation. Review roles require a physically read-only workspace; report editing has explicit writable workspace intent. Authored tool, egress and nested-strict demands remain rejected. |
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

## Bounded authority census

The implementation investigation started at commit
`4e05311913bd1f465968c4ba644989309ef5f964`. This inventory covers the production
entrypoints below and their direct Fleet/Step, native, persistence and projection
consumers. It distinguishes supported reachability from interfaces that reject
the selection. It is a source-level census, not evidence that every native
protocol, process crash cut or installed host has been exercised.

The [requirements baseline below](#native-harness-requirements-baseline) rechecks
these owners at source `82900fce` on 2026-10-31. Its new portable fixture result
is recorded separately; this source review does not rerun or relabel the earlier
installed-harness observations.

| Entry and selected workload | Traced route and boundary | Conclusion |
| --- | --- | --- |
| CLI ordinary Plan commands and builtin Agent Steps | [CLI Plan ingress](../../backend/src/oh_goon/cli_runtime_plans.py) → `FleetPlanService.run` → resolver/frozen source when selected → `start_prepared_plan` → original Fleet lifecycle → `FleetPlanRunner` → `FleetPlanRunExecutor` → shared Step sequence and typed dispatch. Agent source/delivery is mandatory; generic resolution without its original capture rejects. | **CLEAN**, within these call edges: the workload adapters share the admitted Run/Step/attempt chain. |
| CLI native CLI and Codex service | [CLI native selection](../../backend/src/oh_goon/cli_ai.py) resolves one profile/backend; `ai` calls `run_native_agent`, and selected Plan execution calls `run`. Both freeze native intent and enter the same Fleet path. Native dispatch chooses CLI transport or owned Codex service. OpenCode service readiness has no executable session submission route; strict Agent execution rejects. | **CLEAN** for shared admission and dispatch; **INSUFFICIENT EVIDENCE** for universal installed native capability/isolation parity. |
| CLI bounded test/propose/apply/retest repair | Plan ingress requires fresh repair authorization and budget, then `FleetPlanService.repair` validates the exact failure-driven shape and admits each bounded attempt with the same `start_prepared_plan`/wait/cancel methods. Native selection with this failure-driven shape rejects; native repair requires the complete review campaign. | **LEAVE ALONE**: this distinct retained policy does not own another executor. |
| CLI phased review/repair | The same repair driver asks `AgentReviewRepairCampaign` for one phase Plan, then admits a distinct linked Fleet Run. Review, adjudication, proposal, separately admitted apply, regression and verification retain original Step execution. A successful apply authorizes fresh candidate capture while locked non-source configuration remains frozen. | **CLEAN** for the shared effect chain. Campaign advancement is separate domain authority from child execution truth. |
| API and UI Shared Plans | [Shared Plan route](../../backend/src/oh_goon/api/routes/league.py) → `SharedPlanService.run` → `FleetPlanService.run_reviewed` → `start_prepared_plan`. Explicit Agent selection binds one exact Team instance. Repair requires fresh caller authorization and limits, then `SharedPlanService.repair` delegates to the same `FleetPlanService.repair` campaign owner. Both verify the saved revision and reviewed Plan identity before delivery/admission. [UI actions](../../frontend/src/app/FleetRunProvider.tsx) use generated operations through the central client; [Fleet request validation](../../backend/src/oh_goon/api/schemas/fleet.py) still rejects raw `EXECUTE_PLAN`/`FleetPlanTarget` submission. Team execution retains its ordinary Team/Run ingress. | **CLEAN** within these shared admission/capture paths. A pre-intent invocation observation is not a durable campaign decision; campaign reads use the original Journal and never launch work. |
| Schedule | `FleetPlanService._prepare_scheduled_target` freezes ordinary Plan intent; [Schedule admission](../../backend/src/oh_goon/services/schedule_admission.py) binds the explicit target set/capability. [Schedule execution](../../backend/src/oh_goon/services/schedule_execution.py) calls `FleetRunControlPlane.start_bound`, which atomically binds each scheduled repository child in original admission. Preparation has no frozen Agent delivery/campaign authorization route. | **LEAVE ALONE**: scheduling ordinary Plans is required, while scheduled Agent campaigns are unsupported. |
| Step retry | [Existing retry owner](../../backend/src/oh_goon/integrations/_fleet_plan_step_retry.py) calls the same direct/strict Step functions, carries the original runtime/deadline and retains separate attempt evidence. It refuses retry/publication on unsettled process boundaries. | **CLEAN** within this owner; retry is not campaign policy. |
| Run rerun/readmission | [Control plane](../../backend/src/oh_goon/services/fleet_run_control_plane.py) consumes captured durable authority and re-enters `_start_intent`; the lifecycle rejects inherited `agent_repair` limits. Placement-loss readmission has its existing strict CI/CD preconditions. Authenticated replay remains unavailable unless its original replay authority exists. | **CLEAN** for pre-effect refusal of unsupported campaign replay. A new campaign needs fresh caller authority, capture and limits. |
| Recovery, handoff and historical read | [Original recovery](../../backend/src/oh_goon/services/recovery.py) and lifecycle own quarantine/reconciliation; local LACE takeover delegates to the original execution/recovery backend. CLI `--handoff-from` transports untrusted durable context into a fresh native invocation. Journal/Game Tape/campaign reads do not launch phases or resume a campaign. | **LEAVE ALONE** for distinct recovery and read-only projection responsibilities; **INSUFFICIENT EVIDENCE** for every controller-crash/process-isolation combination. |

The fact boundaries are per source generation and operation, rather than per
Python class. Transport and verification do not create another semantic producer.
Declared regression/verification phases require native verification workloads.
The process-free `dependency_inventory` observer remains supported in ordinary
Plans but rejects in either campaign check phase before Skill/source capture,
including when mixed with native commands; it cannot supply native process proof.

| Fact and scope/generation | Canonical producer → accepting consumers | Other touchpoints |
| --- | --- | --- |
| Execution intent, occurrence, configuration and limits for a selected candidate | Resolver plus immutable `ResolvedAgentStep`/`FleetPlanTarget` → original Fleet admission and receiving Step | Frozen delivery/content storage transports exact bytes; execution-lock and receiver checks verify them. Fresh capture changes candidate bytes, not locked execution policy. |
| Run identity, admitted permission, cancellation and terminal result for one Run | `FleetRunLifecycle` and repository-effect transactions → worker, Journal and read views | Driver requests admission/wait/cancel. Schedule policy binds its occurrence in the same admission transaction. Neither adapter nor campaign policy receives admission or terminal-publication capability. |
| Process/service completion and cleanup for one attempt | `engine/command.py` and selected backend; native adapter translates its owned protocol → canonical Step acceptance | Native events remain claims until accepted. Codex daemon teardown and caller cancellation are distinct facts. Broker/relay and native service/helper descendants keep supplied command/runtime authority. |
| Accepted Step outcome and exact membership for one Plan execution | Shared Step settlement/publication → Plan result, operation settlement and campaign policy | Campaign consumes typed settlement and exact Step membership. Game Tape presents original outcomes; an earlier failed test remains failed after later repair. |
| Review/adjudication/proposal claim for one candidate/scope/occurrence | Typed role acceptors → campaign policy or separately admitted apply | Generic native completion/CLEAN does not establish verification truth. Hashmarks owns repository evidence/selection, not child execution or campaign grading. |
| Next phase or terminal controller decision for one campaign revision | Campaign policy → original driver and Journal semantic append boundary | Child Runs retain execution truth; durable publication retains the policy decision and references. Read views verify/project these facts without rerunning policy. |

Compound Steps accept every helper's causal completion and cleanup before another
model/tool launch or derived publication. A zero native exit with accepted
cancellation, timeout, authority loss or output limit cannot authorize the next
helper. Missing supervision or unverified cleanup retains the original physical
receipt and stops the sequence. Step acceptance rejects unknown supervisor reasons
before publication and exhaustively handles the command owner's current variants;
verified `output_cleanup` remains an ordinary completed process boundary.

Cancellation retains the original typed cleanup receipt through `STEP_CANCELLED`
and Game Tape even when no OperationReport exists. The existing Step evidence
owner accepts the supervisor's receipt; the Journal binds its physical execution
to the same Step. Reads project that accepted settlement without creating attempts
or claiming source state after cancellation. Missing or invalid evidence remains
incomplete, and cancellation still stops retries, publication and later helpers.

The inspected effect cuts are original Run admission (identity/lease/Journal),
Step launch through the supplied command/backend owner, separately admitted patch
publication, operation settlement, original Run terminalization plus lease release,
and campaign decision publication. Native service protocol clients own bounded
transport closure, while the admitted daemon's command owner proves physical
quiescence. A protocol acknowledgement cannot replace that proof. The inference
relay uses the original engine command runner; native internal tools remain
children of the selected native workload. These legitimate backend mechanisms
are not standalone admission paths.

Three intentional replacements remove concrete sources of future drift:

| Before → current owner | Consumer cutover and bounded finding |
| --- | --- |
| One local-search-named Step with overloaded native/provider/model fields → neutral discriminated Agent workload | All resolver, delivery, execution and evidence consumers use one variant. Old serialized models/locks must be re-authored rather than interpreted through an alias. **FINDING**: the former representation allowed irrelevant/contradictory selections; replacement is an explicit design improvement, not an allegation of duplicated process launch. |
| Campaign reconstructs successful Codex daemon shutdown from raw native observation/profile → adapter normalization and canonical Step settlement | Campaign completion consumes typed settlement and exact Run/Step evidence. **FINDING**: the old `_completed_service_shutdown` branch made campaign policy reinterpret native transport. That route is removed, while completed service teardown remains distinct from caller cancellation. |
| Invocation-only controller receipt → campaign policy decision retained through canonical Journal | Campaign readback consumes committed controller facts independently of stdout; missing decisions remain unavailable even if a child verification Run succeeded. **FINDING**: the former documented historical readback limitation lost controller authority after invocation output was discarded. Durable decisions do not authorize automatic resume. |

The existing [X0 architecture rule](../../backend/src/oh_goon/repository_tooling/architecture/x0_single_execution_contract.py)
rejects campaign dependencies on raw native observations/protocol, process control,
Run admission and database/Journal writers. Agent adapters cannot acquire Run
admission/publication or launch subprocesses directly. It resolves static imports,
module/callable aliases and literal `getattr` targets through the existing Python
semantic projection. [Adversarial fixtures](../../scripts/tests/test_ag00_canonical_agent_architecture.py)
start from valid source, exercise violations, retain legitimate backend and pure
proposal-validation counterexamples, and allow private helper renaming.
Arbitrary reflection/dynamic data flow and crash ordering require behavioral proof;
the structural rule does not claim their absence.

## Live model and harness surfaces

The identifiers below are the current resolved choices, not a new adapter registry.
The [resolved model](../../backend/src/oh_goon/core/models/fleet_plan.py) and
[resolver](../../backend/src/oh_goon/services/fleet_plan_step_resolution.py) remain
the executable authorities.

| Surface | Current behavior | Migration classification |
| --- | --- | --- |
| `local-search` | Deterministic captured-source retrieval; no model recipient. Original preparation and Step execution own the process. | **KEEP** bounded source/tool execution. It is **NOT AN OWNER** of coding strategy or repository relevance. |
| `local-model-search` | One bounded Ollama query-selection request, followed by producer-owned repository retrieval. | **ADAPT** invocation/selection; local query prompting is a **RETIRE candidate** only for workloads replaced by a demonstrated native adapter. |
| `local-model-review`, `local-model-tools`, `local-model-files`, `local-model-skill-resources`, `local-model-proposal` | [_execute_model_agent / _run_local_model_review](../../backend/src/oh_goon/integrations/fleet_plan_step_execution.py) retain short bounded Ollama request/tool protocols. The model supplies the decisions; native Ollama establishes model availability. Proposal remains a distinct source-bound data result for explicit apply. | **KEEP** these bounded capabilities and their original source binding, disclosure limits, supervision and result acceptance. The former `local-model-skill-loop` conversation/history mode is **REMOVE**; native Agents own its reasoning/tool-loop capability. Old input is rejected, with no alias or fallback. |
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
| Local context and actions | [agent_model_selection.py](../../backend/src/oh_goon/core/agent_model_selection.py) separates authored-prompt, complete-context, repository-observation and reply bounds for retained short builtin requests. The former multi-turn Skill-loop/history mode is removed; native Agents own conversation, compaction and reasoning strategy. Exact captured reads retain a separate 2,048-byte delivery bound. |
| Provider transport | The selected connection owns request/response byte budgets and model parameters. Oversized complete requests fail; they are not silently truncated. The [provider child](../../backend/src/oh_goon/integrations/agent_review_execution.py) separately bounds phase text to 65,536 UTF-8 bytes. Transport has no automatic provider fallback or retry. |
| Deadlines and cancellation | The original Step runner supervises every command. Native Agent timeout includes producer preparation and the complete native conversation. Bounded built-in Agents without repair execution limits retain per-command timeouts; repair adds shared Step/campaign deadlines. Unsupported combinations with a separate admitted watchdog reject before work. |
| Failure translation | [Step dispatch](../../backend/src/oh_goon/integrations/fleet_plan_step_execution.py) propagates cancellation through the original operation lifecycle and converts timeout/output-limit exceptions to bounded failure outcomes. Canonical `PlanStepSettlementEvidence` retains `SUCCESS`, `FAILED`, `CANCELLED`, `TIMED_OUT`, `OUTPUT_LIMITED`, `AUTHORITY_LOST`, `INCOMPLETE` or `SKIPPED`, separately from `VERIFIED`, `UNVERIFIED` or `UNAVAILABLE` cleanup and the observed return code. Local model/read failures can become contract failures; [provider execution](../../backend/src/oh_goon/integrations/agent_review_execution.py) preserves nonzero child results and validates output with typed acceptors. Malformed or misbound claims are rejected; zero process exit alone cannot establish successful verification. |
| Outer continuation | [AgentReviewRepairCampaign](../../backend/src/oh_goon/services/fleet_plan_repair.py) and [AgentRepairContext](../../backend/src/oh_goon/core/models/agent_repair.py) bound linked phase Runs and fresh-candidate capture. Each Run retains its own result. The invocation selects work; canonical Journal campaign facts retain intent, exact phase linkage and advancement/terminal decisions. Reopening evidence never automatically resumes a campaign, and rerun/readmission cannot inherit repair limits. |
| Durable evidence | Stream evidence retains hashes, byte counts and line counts. Typed review/adjudication records, bounded native event summaries and actual standard-isolation receipts persist separately. Initial Hashmarks packets are included in transient supervised Step output; there is no durable raw producer packet or model/tool transcript. [Live output](../../backend/src/oh_goon/runtime/game_tape_live_output.py) is bounded process-local state, and [Hashmarks preparation](../../backend/src/oh_goon/integrations/hashmarks/agent_tools.py) owns disposable scratch. Safe native control failures also use the existing private rotating [diagnostic sink](../../backend/src/oh_goon/runtime/diagnostics.py), with Run/repository/Step/attempt correlation and frame-only redacted owner tracebacks; this supporting evidence survives process shutdown without retaining private protocol or replacing journal truth. |

The byte/action limits above describe this inspected implementation. They are not
portable native-agent capabilities or a second configuration owner. Source owners
remain authoritative. [Game Tape](GAME_TAPE.md) explains historical versus live
visibility; a missing model identity, internal tool event or transcript remains
unavailable rather than being reconstructed from an agent claim.

A native launch refusal is a distinct `FAILED_TO_START` result: no process
was launched, no supervisor or physical exit is invented, and fresh authorized
admission remains possible. A bare failure code with missing supervision cannot
establish that meaning. Process-free inventory is valid ordinary work; declared
regression/verification phases reject it before Skill capture because those
phases require native verification.

## Durable campaign decisions and read cuts

The [existing repair models](../../backend/src/oh_goon/core/models/agent_repair.py)
define `AgentCampaignIntent`, `AgentCampaignPhase`, `AgentCampaignDecision` and
bounded child-result references. The canonical Journal appends `CAMPAIGN` facts
to its existing hash-chained entries, using the existing LocalDatabase
transaction/session. There is no campaign table, second journal or mutable
controller mirror.

The intent binds original execution digest, repository/candidate/scope, write
roots, absolute deadline and budget before phase admission. Each phase fact binds
the exact Run, predecessor campaign revision, execution digest, context and
selected Step membership in the same transaction as original Run admission.
An advancement or terminal decision binds the consumed Run terminal sequence and
operation references, candidate/scope, remaining work and next phase or stop
reason. Exact repeated publication is idempotent; a conflicting/stale predecessor
cannot select another successor or overwrite a terminal decision. Mandatory
publication failure stops successor effects.

`JournalReadView.campaign(campaign_id)` returns the verified read cut and complete
fact sequence. Campaign report, CLI and API/Game Tape consumers use committed
facts and original child results. A child can have successful final verification
while the controller decision is unavailable after a crash; that is incomplete
campaign evidence, not convergence. Unknown/unresolved Run IDs remain explicit.
An admission exception is reconciled against canonical phase linkage before
child readback or source release. If the verified cut contains no phase link,
`unstarted_selection` retains the requested identity, phase ordinal/name and exact
selected Steps as `not_admitted`; it does not invent a child Run. A linked child
whose result is unavailable remains admitted/unresolved. If Journal readback is
unavailable, no terminal controller receipt is fabricated.
Read-only reopening reproduces committed decisions after invocation stdout is
discarded; it neither re-evaluates policy nor starts work. A newer live projection
must identify its newer cut rather than silently revising an older receipt.

Retained facts are bounded operational summaries and evidence references. Decision
messages use canonical diagnostic redaction plus the selected Steps' protected
secret values; over-budget messages carry an explicit truncation marker. They do
not include private reasoning, credentials, native configuration or a reconstructed
model/tool transcript. Optional presentation cannot rewrite a committed decision.
Individual optional facts which the original execution contract did not retain
remain unavailable; campaign readback must not manufacture them.

## Execution state cutover and recovery

`OperationReport.resource_settlement` is the pure read-only aggregation of the
original Step cleanup, attempt and teardown facts; it adds no persisted flag and
does not change Run or Step outcomes. When it is `UNRESOLVED`, the original
repository-effect settlement commits the report while retaining its existing
coordination row as `OUTCOME_UNKNOWN`. The original runtime safety owner blocks
fresh mutating work through that fence, including after restart. The committed
report recovery fast path retains the fence rather than treating report presence
as proof that its processes or resources are quiescent.

Reset, repository filesystem equality and an operator acknowledgement note cannot
replace the original process/resource quiescence evidence. Reconciliation and
acknowledgement refuse to release this fence without that evidence; unavailable
physical recovery remains `INCOMPLETE` with the original identities. Verified
failed commands and process-free verified work remain settled; missing cleanup
for a never-started or skipped Step creates no synthetic writer obligation.


The neutral Agent model uses resolved Plan contract version 47, including explicit
native UV lock mode and independently captured application sources. Previous
serialized Agent Steps/locks are not converted or accepted through a compatibility
alias: resolve and lock the authored Plan with the current runtime before new
admission. Retired `local-model-skill-loop` input requires an explicit supported
replacement and fresh caller authorization. Frozen legacy bytes remain historical
evidence, never a new permission source.

Database schema 89 added the Journal `CAMPAIGN` entry kind. Schema 90 marked the
incompatible resolved Plan contract 46 and explicit native UV lock-mode payloads.
Schema 91 narrows cancellation receipts to original physical evidence and makes
unobserved duration unavailable; schema 90 receipts are incompatible. Schema 92
binds contract-47 captured application sources and rejects contract-46 history
before decoding. Older
schemas must be refused before their Journal payloads are decoded. The original database
owner rejects incompatible schema generations before stateful work; it does not
migrate/reset the store or silently choose another directory. Before an
incompatible deployment, stop admission and use the matching old lifecycle and
recovery owner to settle active work or preserve its unresolved fence. An
unresolved writer/cleanup obligation blocks a fresh mutating generation for that
workspace; moving its lease into an archive does not resolve it.

The existing `reset_local_state` owner validates a compatible store read-only
under its SQLite gate and refuses incompatible, corrupt, active or unresolved
authority before deletion. It checks canonical Journal/campaign state and original
lease/effect/workspace ownership; `--yes` cannot override the refusal. Merely
choosing an unrelated state root cannot establish an older writer's quiescence,
so cross-generation drain/fencing remains an operational precondition rather than
a claimed global workspace registry. See [operator recovery](../TROUBLESHOOTING.md#local-database-schema-mismatch).

After ownership is resolved, archive the old state and matching schema metadata
read-only outside source, then explicitly select a fresh state generation.
Historical bytes may be inspected only by a matching compatible reader outside
active execution. Rollback likewise drains/fences first and pairs reviewed source
with its matching schema. Old/new runtimes must not operate on one active store,
and an old source must refuse a newer incompatible store. No automatic campaign
resume, effect replay, repository reset, credential reuse or privileged cleanup
follows from an archive or source revert; partially applied changes and residual
processes stay under original recovery authority.

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
  The native-service profile keeps its existing trusted-local command/Git
  authority; native tool read-only policy protects its reasoning phases.
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
Capped standard execution binds native `systemd-run` and `systemctl` and uses
the same captured user-manager environment for admission and launch. The original
resource owner verifies the caller's process and thread cgroup membership and
the manager's actual target slice. A new scope is refused before application
work if it would leave a finite private caller CPU quota, PID maximum or
supported memory maximum/high threshold. This covers `cpu.max`, `pids.max` and
the memory max/high/swap/zswap controls observed by the resource owner.
CPU placement, scheduler weights and I/O policies remain outside this check.
Shared ancestor limits remain enforced by the kernel. Unknown hierarchy,
threaded layouts or unreadable controls cannot establish safe scope migration.
The caller check runs before a cached availability result is accepted and again
before launch; an empty resource policy keeps its existing execution path.

The aggregate limits of a private caller group cover its other members too.
Copying a numeric limit into a sibling scope does not preserve that accounting.
An incompatible caller context therefore remains unavailable for capped standard
execution. This changes resource admission, while the native harness protocols
and their settlement adaptations retain their existing owners.

Ordinary waited CLI Plans can select one aggregate budget through
[`run.yml` resources](TEMPLATE_INPUT_INSPECTION.md#limit-a-local-run). The same
CLI enters a fresh native scope before source acquisition and runtime setup;
its original Fleet workers and parallel targets inherit that boundary. The
bounded outer launcher transports captured configuration and observes native
settlement without running another workflow or reconstructing global history.
The private-caller preservation rule above also applies to this launch.
Agent/repair, strict/remote and deployment execution remain incompatible with
this selection: independently scoped workloads do not yet carry a shared
aggregate placement contract. Their per-workload limits do not establish a
whole-Run guarantee. Native harness adaptations retain their existing
qualification limits.
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

`NativeAgentProfile.codex_service` selects owned Codex service execution inside
the existing `FleetPlanService.run_native_agent` or bounded `repair` → resolver →
native Agent Step path. `AgentReviewRepairCampaign` admits the selected service
for read-only review, adjudication, proposal and re-review; the original batch
apply and regression/verification commands retain trusted-local authority.
`runtime/agent_providers.py` resolves the public executable/runtime and
mutually exclusive profile once. Explicit service-home directory metadata must
be separate from workspace, Skill and runtime inputs before source capture;
`agent_authoring_sources.py` rejects private/native-state contents before
file reads without reducing the admitted scope. Empty native reserved-directory
metadata contributes no captured members and is permitted; any contained entry
is rejected before content access. `fleet_plan_native_steps.py` retains frozen input,
mandatory Hashmarks evidence, observations and typed phase acceptance.
[Owned service execution](../../backend/src/oh_goon/integrations/agent_codex_service_execution.py)
materializes explicit native phase policy and uses the supplied original command
owner for version preflight, daemon birth, deadline, cancellation and quiescence.
It consumes a native terminal turn and retains the actual daemon shutdown receipt;
it never fabricates a successful command receipt from an RPC acknowledgement.
The native adapter normalizes normal daemon shutdown only after a completed
read-only service turn with matching native identity and original execution
evidence. Canonical Step settlement accepts that result; campaign policy consumes
its typed outcome and cleanup without inspecting native events/profile or
reclassifying physical cancellation. Missing closure, failed turns and caller
cancellation cannot advance it.
`NativeAgentObservation.execution` stores original-Step preflight, native policy
and process-closure facts under `ORIGINAL_STEP_EXECUTION`; conversation events
retain `UNTRUSTED_HARNESS_CLAIM`. Empty native `.codex` setup directories may
persist under native ownership; neither controller cleanup nor an agent is
allowed to turn them into writable configuration. This supports tool-scope
read-only policy, not a whole-process no-write claim for protected source.
The native service alone owns authentication, personal configuration, reasoning,
tools and compaction. No new credential store, scheduler or recovery manager is
introduced. Readiness remains non-authorizing for arbitrary shared endpoints.

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
the original owned Step consumes its narrow protocol. Non-secret native feature
booleans, bounded connection-private MCP IDs, effective workload-thread MCP
exclusion and connection-private native-home path separation are checked before
model work; no auth/config read or generic API route is exposed. Native threadless
metadata discovery may initialize registered servers under ordinary startup
semantics. Native per-ID disabled overrides leave stored configuration intact;
the actual workload must report disabled runtimes and empty effective catalogs.
The original daemon owner retains final cleanup authority. Cold resume requires
the original frozen instructions explicitly.
A profile identifier alone is never permission proof.
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
| Neutral workload variants and old-input refusal | [Agent workload contracts](../../backend/tests/unit/core/test_agent_native_provider_contract.py), [repair admission](../../backend/tests/unit/services/test_agent_repair_admission.py) |
| Canonical Step settlement and service teardown | [Typed settlement](../../backend/tests/unit/integrations/test_fleet_step_settlement.py), [service execution](../../backend/tests/dependency_complete/test_agent_codex_service_execution.py) |
| Durable campaign intent, admission linkage, CAS and readback | [Real SQLite Journal boundaries](../../backend/tests/unit/runtime/test_agent_campaign_journal.py): admission rollback, exact publication retry, restarted readback, competing decisions, missing controller commit, accepted stop, privacy and full read-cut identity. The real review/repair integration also exits the controller immediately before and after terminal publication, discards stdout and reopens the same Journal; other physical crash cuts need their own native proof. |
| Structural absence proof and allowed counterexamples | [Existing X0 regressions](../../scripts/tests/test_ag00_canonical_agent_architecture.py): aliased process/admission/publication bypass, raw native campaign dependency, pure/read-only projections, private renames and legitimate backend launch mechanisms. A private class name alone grants no authority and is not a rejection condition. |
| Bounded builtin protocol and proposal-as-data | [Bounded resource/proposal behavior](../../backend/tests/integration/test_agent01b3g_bounded_skill_loop.py), [source-bound proposal](../../backend/tests/integration/test_agent01b3i_source_bound_patch_proposal.py), [context bounds](../../backend/tests/unit/core/test_agent_model_selection.py); native Run/service cases below cover the replacement reasoning loop. Historical test filenames do not retain the removed harness selection. |
| Complete provider closure and typed output acceptance | [Provider execution](../../backend/tests/unit/integrations/test_agent_review_execution.py), [review/adjudication resolution](../../backend/tests/unit/services/test_agent_review_resolution.py), [provider wire boundaries](../../backend/tests/dependency_complete/test_agent_provider_http.py) |
| Frozen provider selection and credentials | [Provider configuration](../../backend/tests/unit/runtime/test_agent_providers.py), [provider connection](../../backend/tests/integration/services/test_agent_provider_connection.py) |
| Producer qualifications and evidence bounds | [Hashmarks Agent tools](../../backend/tests/unit/integrations/test_hashmarks_agent_tools.py) |
| Passive harness data contract | [Harness contract](../../backend/tests/unit/core/test_agent_harness_contract.py), [effect requests](../../backend/tests/unit/services/test_skill03b_effect_requests.py) |
| Native receiver and complete Run lifecycle | [Native Step contract](../../backend/tests/dependency_complete/test_fleet_plan_native_steps.py), [native Run](../../backend/tests/integration/services/test_native_agent_run.py) |
| Native service readiness | [Codex transport](../../backend/tests/dependency_complete/test_agent_codex_service.py) covers protocol continuation and profile pinning; [installed service cases](../../backend/tests/integration/services/test_native_codex_service.py) cover native policy, follow-up, persisted resume and compaction, while [service Runs](../../backend/tests/integration/services/test_native_codex_service_run.py) cover independent verification. [OpenCode transport](../../backend/tests/dependency_complete/test_agent_opencode_service.py) covers bounded authority and closure; [installed readiness/lease cases](../../backend/tests/integration/services/test_native_opencode_service.py) verify PID-bound readiness, client closure preserving the server, owner cancellation, stdio discovery and native shutdown after stdin closes. OpenCode service-session execution remains unavailable. The [compatibility inventory](AGENT_HARNESS_COMPATIBILITY.md) records precise installed scopes and remaining qualification limits. |
| Installed Codex instruction transport | Opt-in [native Codex context](../../backend/tests/integration/services/test_native_codex_context.py): synthetic API-key/ChatGPT authentication × HTTP/WebSocket, privileged task/Skill/producer context through native response chains, automatic compaction and continuation. ChatGPT discovery uses a local HTTPS backend; WebSocket cases reject inference HTTP fallback. Live account access and model quality remain separate. |
| Live Codex account and coding | The [legacy live coding baseline](../../backend/tests/integration/services/test_native_codex_live.py) retains the original local Run and independent verifier; its credential-reading/forced-refresh helper is removed. It does not qualify native-store exclusion. The [implemented native-service adapter](../AGENT_PROVIDERS.md#native-service-adapter-design) has separate synthetic installed qualification; real-account refresh/persistence qualification remains unverified and requires non-secret native lifecycle facts. |
| Ordinary physical boundaries and native resources | [Standard Bubblewrap](../../backend/tests/unit/integrations/test_standard_bubblewrap_isolation.py), [verification and Git observers](../../backend/tests/unit/integrations/test_native_plan_isolation.py) |
| Terminal, inference and observations | [Native terminal](../../backend/tests/unit/engine/test_command_terminal.py), [model broker](../../backend/tests/dependency_complete/test_agent_inference_broker.py), [Game Tape claims](../../backend/tests/unit/core/test_game_tape_native_agents.py) |
| Linked review/repair Runs and source recapture | [Review/repair integration](../../backend/tests/integration/services/test_agent_review_repair_loop.py), installed [Codex service campaign](../../backend/tests/integration/services/test_native_codex_service_run.py) cases for CLEAN, one/multiple independent defects and cancellation, [bounded repair](../../backend/tests/integration/test_agent_bounded_repair.py), [capture boundaries](../../backend/tests/unit/services/test_agent_repair_source_capture.py) |
| Installed local model behavior | Opt-in [native model review](../../backend/tests/integration/test_agent01b3c_native_model_review.py) and [installed bounded Skill resources](../../backend/tests/integration/test_agent01b3h_native_skill_cycle.py); installed native Run/service cases establish their separately selected protocol capabilities. |

Synthetic model/HTTP fixtures establish protocol and lifecycle behavior within
their tested scope. Installed-model tests are separate from privileged custody
proofs; they do not establish every native implementation or optional feature. This document
does not promote any test result into APP, native-host or release certification.

### Native harness requirements baseline

The [HUG-21 source census](https://linear.app/hugloss/issue/HUG-21/reconcile-current-baseline-compatibility-inventory-51-requirements-and)
reviewed [product source `82900fcedac678671cb4896b082e472caba8cf4d`](https://github.com/oh-goon/oh-goon/commit/82900fcedac678671cb4896b082e472caba8cf4d)
on **2026-10-31** against the
[canonical full requirements](https://linear.app/hugloss/document/oh-goon-native-harness-interchangeability-and-compatibility-removal-2a497c31c54b),
document `28808050-b6e6-4bcc-bd27-ab1c74887c6f`, updated
2026-10-27 15:46:03 UTC. The crosswalk covers its 51 OWN/REG/SWAP/LIFE/SEC/EVI
requirements, G-01–G-11, rollback rules, D01–D15 and **Harness T01–T44**,
including SP-01–SP-08. Harness T42–T44 are not Pi persistence's separate
numbered test namespace.

**Implemented** is a current source finding. **A evidence** locates deterministic
fixtures, sometimes with real supervised children; a named test is not an
execution result. **Partial**, **missing qualification**, **unsupported** and
**N/A to this change** retain different meanings. Only the exact portable corpus
below is a fresh result. Existing dated installed observations retain
their recorded implementation, mode and environment; they are not new B/C PASS
results for this source. No installed Codex/Pi/OpenCode implementation, real
account or personal native credential store was exercised by this census.

This extends the existing owner map. The [compatibility inventory][census-registry]
remains the sole adaptation/removal catalog; runtime selection and original
result owners do not consume this prose. No new runtime, version database,
support bit, scheduler or qualification gate is added.

#### Requirements and actual owners

| Requirement | Current owner / evidence | Disposition and limit |
| --- | --- | --- |
| OWN-01 | Native CLI/service adapters; passive harness contract | Implemented. Native session/reasoning/authentication stay native; no transcript migration. [Contract fixtures][census-contract] and [receiver][census-receiver]. |
| OWN-02 | Fleet lifecycle, supplied command runner, shared Step settlement | Implemented. [Settlement fixtures][census-settlement] retain physical facts; [owned service execution][census-service-execution] cannot publish success without terminal/cleanup proof. |
| OWN-03 | Native provider resolver; frozen `ResolvedNativeAgent` and `AgentHarnessInput` | Implemented selection/binding. [Selection fixtures][census-selection] and contract identity mutants; effective native configuration-generation drift remains unqualified. |
| OWN-04 | Typed review/proposal acceptance; independent verification | Implemented. [Role acceptance][census-role] rejects false CLEAN after failed native exit and untyped CLEAN after zero exit. A claim is not verification. |
| OWN-05 | Native claims, command supervisor, Journal, campaign policy, read projections | Implemented distinct facts. [Journal replay tests][census-journal] and [campaign evidence][census-campaign]; transport clients are not lifecycle duplicates. |
| OWN-06 | Passive core contract and narrow native adapters | Implemented; LEAVE ALONE. [X0 counterexamples/mutants][census-x0] protect one execution chain without banning legitimate clients or read models. |
| REG-01 | [Canonical compatibility inventory][census-registry] | Implemented classifications: two temporary adaptations, four versioned constraints, permanent mappings/policy, retired routes and unqualified modes. |
| REG-02 | Existing temporary-adaptation records and current caller source | Partial maintenance record. Gates/reasons/invariants/tests are recorded; explicit live-caller fields are not. The live callers are `execute_native_agent_step` → `NativeAgentEventParser.finish`, and `execute_codex_service_run` → `service_arguments`; the batch parser helper is not a live caller. |
| REG-03 | Behavioral removal conditions in the canonical records | Implemented maintenance rule. Neither removal condition has new installed replacement proof; retain both adaptations. |
| REG-04 | Existing upgrade/subtraction review | Implemented review obligation; no vendor upgrade is selected by this census. No fresh upgrade qualification is claimed. |
| REG-05 | Existing removal gates and retained negative tests | N/A to removal in this change: zero adaptations, production branches or tests are removed. A green fixture run cannot authorize removal. |
| REG-06 | Existing compatibility document; runtime resolver remains executable authority | Implemented; LEAVE ALONE. This crosswalk is an evidence index, not another adaptation catalog, version selector or admission table. |
| REG-07 | Mode/environment-specific requalification of affected entries | Implemented review obligation; current installed upgrade combinations remain NOT_RUN. |
| REG-08 | Existing source-change/test-removal review and X0 proof | Implemented maintenance safeguards. This census retains all existing protective tests; it runs no test-disable experiment. |
| REG-09 | Source-only maintenance through existing docs/check owners | Implemented. No mandatory runtime command, serialized registry or prose gate is introduced. |
| REG-10 | OpenCode completed-text and Codex reserved-directory candidates | RETAIN both: native terminal replacement and absent/populated reserved-directory replacement gates remain unsatisfied. Permanent mappings are not temporary workarounds. |
| SWAP-01 | `cli_ai.py`; native provider resolver | Implemented explicit/default profile selection. [CLI selection tests][census-cli] do not grant another harness on failure. |
| SWAP-02 | Existing conformance matrix; declared harness capabilities | Implemented independent dimensions; declaration compatibility is not installed execution/security proof. See [mode limits][census-registry]. |
| SWAP-03 | Resolver, compatibility checks, OpenCode protected-execution refusal | Implemented pre-effect refusal. [Selection][census-selection], [service execution][census-service-execution] and [OpenCode tests][census-opencode] cover selected negative boundaries. |
| SWAP-04 | Frozen executable/profile/thread selection | Implemented binding; [Codex continuation tests][census-codex] reject wrong/replaced identities. Whole native package and effective configuration-generation drift remain unqualified. |
| SWAP-05 | `--handoff-from`; original Run readback and fresh admission | Implemented bounded untrusted prior-Run data, not native transcript continuation. [CLI handoff tests][census-cli] and [handoff projection][census-handoff]. |
| SWAP-06 | Native mode-specific capability refusal | Implemented honest asymmetry. OpenCode service submission and cross-harness native-session migration are unsupported, not missing fallback implementations. |
| SWAP-07 | `runtime/agent_providers.py` public selection snapshot | Implemented native settings/auth ownership. Selection tests reject native overrides without reading credential stores or duplicating native model inventories. |
| SWAP-08 | Original Fleet Runs and independent verifier | Existing real-child [three-harness fixture][census-native-run] re-captures source and reopens Journal. It is A-level protocol-fixture proof, not a combined installed-harness chain. |
| LIFE-01 | Parser → original command/Step settlement → Journal | Implemented orthogonal claim/exit/cleanup/result facts. Settlement tests preserve observed exit beside rejected cleanup. |
| LIFE-02 | `NativeAgentEventParser.finish`; receiver; typed acceptors | Implemented terminal/error precedence. [Event tests][census-events] and receiver failure/unsettled-claim tests protect acceptance. |
| LIFE-03 | Shared command quiescence and native producer cleanup | Implemented outer boundary; native detached-feature breadth remains unqualified. [Command tests][census-command] and receiver cleanup-before-launch tests are scoped evidence. |
| LIFE-04 | Service transport closure versus original owned-daemon cancellation | Implemented distinction. OpenCode transport closure does not terminate/adopt an external server; owned daemon cleanup stays with the command owner. |
| LIFE-05 | Frozen selection and connection-local Codex identity | Partial qualification. Lost-response/restart refusal exists; arbitrary live generation replacement/revocation remains unqualified. |
| LIFE-06 | Codex request/thread/profile/turn correlation | Implemented correlation; wrong-resume tests exist. Two-live-Run interference and every concurrency combination remain unqualified. |
| LIFE-07 | Original cancellation, command supervision and Step/Journal evidence | Implemented with selected race coverage. Complete prompt/compaction/publication/cleanup race combinations are not all qualified. |
| LIFE-08 | Original recovery and durable campaign decision publication | Selected real controller-death fixtures exist in [review/repair tests][census-repair]. Exhaustive per-mode physical crash cuts remain unqualified. |
| LIFE-09 | Native-owned session operations; ephemeral Pi product mode | Implemented no transcript repair. Installed persistence probes characterize native behavior without enabling product resume. |
| LIFE-10 | Codex connection-local continuation; fresh-Run handoff | Cross-Run native-session adoption is unsupported/unqualified. It is not a baseline requirement to implement another session manager. |
| LIFE-11 | Native configuration/store noninterference | Current ownership/refusal boundary implemented. Upgrade-preserves-personal-data and model-disappearance matrix remains unqualified. |
| LIFE-12 | Original lifecycle transitions and uncertainty handling | Implemented transition rules with partial coverage. Terminal/refusal rows have A fixtures; reload/history/model-loss and broader physical cuts retain explicit gaps. |
| SEC-01 | Authoring source capture and selected native-home separation | Scoped implementation. [Source privacy tests][census-privacy] reject private source before bytes/Run effects; [native-home capability tests][census-capabilities] cover aliases. Ordinary CLI has no universal store-exclusion claim. |
| SEC-02 | Selected Codex service effective capability/canary checks | Scoped implementation and fixtures. Profile IDs/configured booleans alone do not attest effective grants; installed/environment qualification remains separate. |
| SEC-03 | Selected store canaries, native MCP exclusion and source guards | Partial. Historical installed direct/symlink/subprocess canaries do not complete hardlink/traversal/sibling-CWD/API/plugin/global-event store denial. |
| SEC-04 | Private endpoint paths, original daemon process, native thread binding | Partial implementation; transport-peer attestation and post-readiness rebind qualification are missing. Codex has no expected-peer/SO_PEERCRED enforcement. OpenCode's client validates version/positive PID; historical installed probes compare metadata PID with the original process and physical execution identity, not the actual transport peer. It admits no model work. |
| SEC-05 | Original phase re-admission; immutable native thread/profile | Implemented outer effect boundary. Native effective-grant/configuration mutation during a retained session remains unqualified. |
| SEC-06 | Existing ordinary/privileged profiles and qualification markers | Implemented separation. Strict Agent execution is unsupported; whole-Run CLI budgets exclude Agent/repair. No new privileged startup prerequisite. |
| SEC-07 | Frozen delivery, provider/context and native continuation owners | Implemented context delivery with fixture and historical scoped installed evidence. Current live-provider, cross-environment and credential-separation qualification is NOT_RUN. |
| SEC-08 | Bounded, sanitized native metadata and visible output | Implemented limits/redaction, not a generic native metadata proxy. OpenCode readiness is capped at 64 KiB; Codex visible output excludes reasoning and redacts before clipping. |
| EVI-01 | Existing A/B/C and mode/environment distinctions | Implemented documentation boundary. Recorded installed results retain their original dates and scopes. |
| EVI-02 | Frozen selection, original receipts, Step/attempt identity and result accounting | Evidence owners implemented. Only the exact selected portable corpus below is a fresh result; installed suites and mere linked test names are not. |
| EVI-03 | Native byte/observation bounds and durable omissions | Implemented; event/contract/settlement fixtures preserve limits and unknown output. Bounded text is not complete-stream or verification authority. |
| EVI-04 | Original Run/Step/attempt and fresh source/occurrence binding | Implemented; contract mutants and real-child handoff retain separate identities and earlier failed verification. |
| EVI-05 | Original Journal/Game Tape plus CLI/API projections | Implemented shared read owners; complete same-native-Run cross-surface comparison is partial. Current public readback is `runs outputs/events/handoff`; retired `plan` command spelling in the requirements is historical. |
| EVI-06 | Safe version/capability/endpoint refusal | Implemented selected safe diagnostics; complete harness/mode/actionable-next-step diagnostic acceptance remains unqualified. |
| EVI-07 | Existing subtraction policy and retained negative tests | Implemented maintenance obligation. No adaptation/test deletion occurs here, so replacement A+B evidence is N/A to this change. |

#### Adversarial vectors

| Harness vector | Current evidence / review target | Qualification boundary |
| --- | --- | --- |
| T01 | [Events][census-events]: `test_pi_settled_is_required_after_agent_end_and_tool_errors_can_recover`, compaction nonterminal cases | A fixtures; Pi `agent_end` alone is not completion. |
| T02 | Events: failed assistant/aborted loop with native exit zero | A fixtures for error/aborted/length; no native success promotion. |
| T03 | Events: reconciled OpenCode completed text only after EOF/zero exit | A fixtures; temporary adaptation retained. |
| T04 | Events: incomplete text/tools, stale cycle, truncated/malformed stream | A fixtures; missing physical exit cannot complete. |
| T05 | Events: recovered Codex diagnostic then terminal success | A fixtures; diagnostic is not terminal failure by itself. |
| T06 | Events: completion cannot erase failed turn/process | A fixtures; earlier text cannot establish success. |
| T07 | [Journal][census-journal]: duplicate lifecycle no-op and conflicting replay refusal | A publication proof; complete post-commit native-frame race remains partial. |
| T08 | Events and [passive contract][census-contract] byte/UTF-8/observation/role bounds | A fixtures; omission remains explicit. |
| T09 | [Selection][census-selection]: copied public snapshot; frozen inference choice | Partial A; full two-live-Run profile/generation-change proof missing. |
| T10 | [Codex transport][census-codex]: lost response/restart refusal | Partial A; arbitrary stale callback after concurrent replacement unqualified. |
| T11 | Codex selected thread/profile, invalid exchange, wrong resume identity | A transport coverage; cross-Run native adoption unsupported. |
| T12 | Codex restart rejection | Active prompt/compaction/approval/tool reload matrix remains unqualified. |
| T13 | [OpenCode transport][census-opencode]: client closes only its own transport | A; installed server-preservation case is historical/current B NOT_RUN. |
| T14 | [Owned service execution][census-service-execution]: dead-before-readiness/interrupted-preflight cases | Available A cuts, not selected here; the current corpus instead runs version and missing-terminal/cleanup cases. Late readiness and peer replacement breadth remain partial. |
| T15 | Codex lost-turn-response test | A uncertain-acceptance refusal; no blind replay after restart. |
| T16 | Command cancellation-wins and selected service/Step cancellation cases | Existing A race cases are mapped, but command-race/service-cancellation tests were NOT_RUN in this corpus; only the selected settlement file ran. Complete phase qualification remains open. |
| T17 | [Review/repair][census-repair]: controller death during admission/process/apply | Available real-process A cases; not selected in this census; full native crash matrix incomplete. |
| T18 | Review/repair: controller death around final publication | Available A cases, not selected here; child history does not invent campaign convergence. |
| T19 | Crash fixtures discard stdout/reopen Journal; readback does not recover work | Available A evidence; lost transient text stays unavailable. |
| T20 | [Pi persistence probe][census-pi-probe] and its recorded disposition | Missing/empty/torn-tail behavior is characterized historically. Product exact-open/persistent Pi and broad model-loss qualification remain unsupported/unqualified. |
| T21 | [Source privacy][census-privacy]; selected historical installed synthetic-store canaries | Partial. No current B run or universal direct/alias/hardlink/traversal/CWD claim. |
| T22 | [Capability checks][census-capabilities] and native MCP exclusion tests | Partial A; installed configured-MCP case is separate. Generic API/plugin/global-event private-store matrix remains unqualified. |
| T23 | Private socket paths, original daemon ownership, bounded readiness and historical OpenCode metadata-to-owner PID comparisons | Transport-peer attestation and post-readiness rebind proof are missing. The historical PID/physical-execution observations and private path do not establish that peer boundary. |
| T24 | Events/selection: owned transport/task/session/config argument refusal | A fixtures, including Pi refusal before prompt/native-resource access. |
| T25 | [Installed context][census-context] and CLI/service compaction suites | Scoped historical B; current installed, other-mode and cross-environment proof NOT_RUN. |
| T26 | [Real-child handoff][census-native-run]; [CLI selection/handoff][census-cli] | A sequential chain. First fixture Run is ATTENTION_REQUIRED with failed independent verification; fully FAILED prior-Run handling is unit coverage, not an installed three-harness chain. |
| T27 | CLI bounds/malformed/recursive handoff and [handoff omissions][census-handoff] | Partial A. Wrapping is explicitly untrusted/non-authorizing; a distinct malicious permission/success-instruction injection case was not identified. |
| T28 | [Typed role acceptance][census-role] and protected real-apply campaign fixture | Available A source/scope/preimage/regression protections; installed campaign qualification remains separate. |
| T29 | OpenCode `test_protected_work_is_unavailable_before_any_submission` | Unsupported execution with A negative proof before/after readiness. |
| T30 | Owned service execution version refusal; existing registry | Constraint/review evidence exists; no installed upgrade experiment selected. |
| T31 | Existing removal gates and negative tests | N/A to this documentation change; no deliberate test-disable experiment or adaptation removal is claimed. |
| T32 | Backend installed-suite selection guards; original native outcome accounting | An all-skipped/empty installed invocation remains unqualified even if raw pytest exits zero; selection guards are not an installed-result gate. Current B/C NOT_RUN; this corpus has zero skipped/deselected/resumed cases. |
| T33 | Original CLI readback, API/Game Tape projections and lifecycle tests | Component A evidence; complete same-native-Run comparison across all surfaces remains partial. |
| T34 | [X0][census-x0], retired-input refusal and registry retirement records | Retained source/refusal evidence; no removal in this change, so replacement proof N/A. |
| T35 | Frozen provider snapshot; command stream and service diagnostic invariance tests | The frozen-provider subset ran. Command-stream and service diagnostic-invariance cases are mapped existing A cases, NOT_RUN here; no universal formatting-invariance claim. |
| T36 | Executable-byte verification; contract source/execution identity mutants | A fixtures. Entrypoint hashing does not seal an entire imported native package. |
| T37 | [Broker history][census-broker]: provider-owned references across broker lifetimes | Partial A, same synthetic provider/configuration and three reference shapes; live Conversations API and different-credential separation NOT_RUN. |
| T38 | Shared command cleanup and selected native feature exclusion | Partial A outer boundary; optional detached native subagent/extension lifetime qualification remains open (HUG-107). |
| T39 | Parser bounds/latching, physical exit precedence and typed acceptance | Protocol-origin REVIEW TARGET (HUG-108); no installed tool/plugin root-frame spoof demonstration or vulnerability claim. |
| T40 | Opt-in live coding evidence | C refresh/expiry/logout/persistence NOT_RUN (HUG-109); no credential-store inspection. |
| T41 | Historical Linux installed checks | WSL2/container/non-Linux parity NOT_RUN (HUG-110); no new ordinary privileged prerequisite. |
| T42 | Existing three-harness fixture is sequential | Actual overlapping mixed-harness resource/cancel/Journal noninterference remains unqualified (HUG-122). |
| T43 | Wheel/package smoke and source-checkout native tests are distinct | Exact installed-wheel native-Agent Run NOT_QUALIFIED (HUG-123/HUG-96). |
| T44 | Codex MCP snapshot tests; historical configured-MCP campaign case | Partial A. Threadless discovery may start native MCP processes before thread disable; it is not effect-free prework (HUG-124). Current installed case NOT_RUN. |

#### Baseline gaps and follow-up ownership

| Gap | Current classification | Existing next owner |
| --- | --- | --- |
| G-01 | Partial A mixed-harness chain; installed combined proof missing | HUG-8 / HUG-48; existing original Run fixture. |
| G-02 | OpenCode service execution UNSUPPORTED; readiness retained | Existing OpenCode capability refusal; no fallback project. |
| G-03 | Frozen selection implemented; effective generation/restart breadth unqualified | HUG-28; original resolver and service clients. |
| G-04 | Selected boundaries/canaries exist; complete native-store matrix unqualified | HUG-38; existing native policy/capability owners. |
| G-05 | Real account refresh/expiry/persistence C NOT_RUN | HUG-109; opt-in non-secret native facts only. |
| G-06 | Selected command/controller crash cuts; broad physical matrix incomplete | HUG-30 / HUG-34 / HUG-107; original process and recovery owners. |
| G-07 | Historical Linux scope only; other hosts/strict contexts not inherited | HUG-110; original profiles and separately selected qualification. |
| G-08 | Canonical registry/removal gates exist; LEAVE ALONE | HUG-9 / HUG-49; maintenance review, not runtime governance. |
| G-09 | Distinct version/readiness/profile/capability accepting owners; no demonstrated new divergence | LEAVE ALONE unless a reachable counterexample is established; T23 remains a qualification gap. |
| G-10 | X0/refusal/retirement protection exists; no removal justified | HUG-49; preserve source and test-removal review. |
| G-11 | Bounds, omissions and original results implemented; non-vacuity remains scoped | HUG-52; empty/skipped/diagnostic evidence cannot qualify installed execution. |

| Addendum | Harness vector | Existing issue | Preserved scope |
| --- | --- | --- | --- |
| SP-01 | T37 | HUG-106 | Provider-owned history; partial same-synthetic-provider A, no real/different-credential isolation claim. |
| SP-02 | T38 | HUG-107 | Detached resources; shared process-tree proof is not every native extension lifetime. |
| SP-03 | T39 | HUG-108 | Protocol-origin review target; do not treat shape-valid root JSONL as origin attestation. |
| SP-04 | T40 | HUG-109 | Optional real-account lifecycle C NOT_RUN; no private-store reads. |
| SP-05 | T41 | HUG-110 | Host-specific installed qualification remains NOT_RUN outside recorded Linux scope. |
| SP-06 | Harness T42 | HUG-122 | Mixed-harness overlapping Runs remain unqualified; sequential handoff is different. |
| SP-07 | Harness T43 | HUG-123 / HUG-96 | Installed-wheel native-Agent Run parity remains NOT_QUALIFIED. |
| SP-08 | Harness T44 | HUG-124 / HUG-38 | Native MCP discovery has possible startup effects before thread exclusion. |

#### Rollback and acceptance crosswalk

| Rollback rule | Disposition | Boundary |
| --- | --- | --- |
| RB-01 | Implemented no-fallback boundary | Unknown selections and unqualified Codex service versions refuse before corresponding model work; no universal CLI-version compatibility promise. |
| RB-02 | Partial qualification | Frozen executable/profile checks and restart refusal exist; arbitrary live package/configuration upgrades remain unqualified. |
| RB-03 | N/A to removal here | Both temporary adaptations and their negative tests are retained; no native data is rewritten. |
| RB-04 | Implemented selected uncertainty boundaries | Lost acceptance is nonretryable; original cleanup/Journal evidence persists. Broader native crash combinations remain unqualified. |

| Acceptance | Current evidence / disposition | Remaining limit |
| --- | --- | --- |
| D01 | Owner map and X0 evidence; no second authority identified by this bounded review | Source classification plus selected A proof; not universal absence proof. |
| D02 | Canonical compatibility registry retained | Existing dispositions remain scoped; REG-02 explicit-caller record completeness is partial. |
| D03 | No workaround deleted | N/A to this census; both behavioral removal gates remain open. |
| D04 | No runtime/parser/alias/version database introduced | Documentation-only change; selected X0 negatives pass at the reviewed baseline. |
| D05 | Exact baseline/corpus/provenance recorded below | Only 551 selected portable instances are a fresh PASS; installed B/C NOT_RUN. |
| D06 | Native claim/process/Step/cleanup distinctions preserved | Selected A fixtures pass; no blanket installed terminal qualification. |
| D07 | Frozen identity, replay refusal and selected crash cases mapped | Full live generation/reload/crash matrix remains partial. |
| D08 | Scoped canaries and explicit unsupported-mode refusals | Universal native-store/tool/peer qualification not complete. |
| D09 | Fresh separate-Run handoff; no transcript migration | Current real-child A case passes; installed combined chain remains unqualified. |
| D10 | Original Journal/Game Tape/read projections | Component evidence exists; complete native cross-surface matrix remains partial. |
| D11 | Protective tests retained | No deletion/disable experiment; replacement proof N/A to this change. |
| D12 | Focused meaningful boundary checks; complete portfolios daily | Current root policy and merge marker include necessary affected process proof. The test-guide sentence saying the fast loop excludes all process/scale work is stale; HUG-93 owns that guidance gap. |
| D13 | Behavioral source review/portable proof bound to 82900fce; documentation checks apply after this edit | The baseline result is not relabeled as installed or whole-final-source qualification. Final PR checks are reported in the PR. |
| D14 | Unsupported/unqualified modes remain explicit | OpenCode service execution, cross-Run native adoption, persistent Pi product mode and unselected environments are not promoted. |
| D15 | Zero adaptations removed; two retained | This census supplies the requirement/vector/risk map; existing follow-up issues retain unresolved qualification. |

#### Fresh portable evidence and subtraction

The original resume-safe batch runner executed the selected corpus once, with
one worker under the existing native **two-CPU test budget**, 90-second batch
limits and a 300-second suite limit. Native collection admitted **551 instances
in 13 files**; all 551 completed and passed in 18 batches, with **zero skipped,
deselected, resumed, missing, duplicate or extra instances**, in 60.0 seconds.
No certification/installed/live-account suite was selected. These are fixture
results, including one real Bubblewrap-supervised three-harness chain, not
installed Codex/Pi/OpenCode or production resource-budget qualification.

The complete selected files were [native events][census-events],
[passive harness contract][census-contract], [Step settlement][census-settlement],
[native selection][census-selection], [service capabilities][census-capabilities],
[OpenCode transport][census-opencode], [private source refusal][census-privacy]
and [X0 mutations/counterexamples][census-x0]. Additional exact selectors covered
Codex selected-thread/profile, wrong-resume, lost-response/restart and one-snapshot
MCP exclusion; unqualified service version and missing terminal/cleanup;
three provider-history reference shapes; duplicate/conflicting Journal replay;
and `test_cross_harness_handoff_keeps_separate_runs_and_original_verification`.
Other mapped tests, including controller-death, installed and live-account
cases, were not silently added to this result.

The original report and membership receipt bind source
`sha256:caff6c640ce8ad1c1ca8ae984ed1f211231b4aa3003e40c45b2d27224e666228`,
test plan `sha256:c868c4e307929fdde1c641e06234905de21dc294805d6a55b1c8aec364f6649c`
and selected membership
`sha256:84f8173140451cd0ad2d6948a7f4bc34a0766898eef73b54b8fc681e2b3cb383`.
Scope: Linux 7.0.0-38-generic, CPython 3.14.8, uv 0.13.3. The native report's
effective outcomes and collection partition were read back through the existing
verification-result owner. The source and test files were unchanged during
execution; subsequent edits are this documentation census. No APP, installed-host,
release, WSL2/container or representative performance qualification follows.

**Subtraction result:** zero production adaptations, branches, fixtures or tests
removed. Retain OpenCode's completed-text/physical-exit adaptation and Codex's
reserved-directory mapping until their existing falsifiable removal gates are
proved with the selected installed replacement. OpenCode service readiness,
transport closure, Journal, typed acceptance, source/privacy checks and
independent verification remain legitimate separate responsibilities.

[census-registry]: AGENT_HARNESS_COMPATIBILITY.md
[census-contract]: ../../backend/tests/unit/core/test_agent_harness_contract.py
[census-events]: ../../backend/tests/unit/integrations/test_agent_native_execution.py
[census-selection]: ../../backend/tests/unit/runtime/test_agent_native_providers.py
[census-settlement]: ../../backend/tests/unit/integrations/test_fleet_step_settlement.py
[census-receiver]: ../../backend/tests/dependency_complete/test_fleet_plan_native_steps.py
[census-service-execution]: ../../backend/tests/dependency_complete/test_agent_codex_service_execution.py
[census-codex]: ../../backend/tests/dependency_complete/test_agent_codex_service.py
[census-opencode]: ../../backend/tests/dependency_complete/test_agent_opencode_service.py
[census-capabilities]: ../../backend/tests/dependency_complete/test_agent_codex_service_capabilities.py
[census-role]: ../../backend/tests/unit/integrations/test_agent_review_execution.py
[census-journal]: ../../backend/tests/unit/runtime/test_journal.py
[census-campaign]: ../../backend/tests/unit/runtime/test_agent_campaign_journal.py
[census-command]: ../../backend/tests/unit/engine/test_commands.py
[census-cli]: ../../backend/tests/unit/entrypoints/test_cli_ai.py
[census-handoff]: ../../backend/tests/unit/entrypoints/test_run5_original_handoff.py
[census-native-run]: ../../backend/tests/integration/services/test_native_agent_run.py
[census-repair]: ../../backend/tests/integration/services/test_agent_review_repair_loop.py
[census-privacy]: ../../backend/tests/integration/services/test_codex_service_run_source_privacy.py
[census-context]: ../../backend/tests/integration/services/test_native_codex_context.py
[census-pi-probe]: ../../backend/tests/integration/services/test_native_pi_session_protocol.py
[census-broker]: ../../backend/tests/dependency_complete/test_agent_inference_broker.py
[census-x0]: ../../scripts/tests/test_ag00_canonical_agent_architecture.py
