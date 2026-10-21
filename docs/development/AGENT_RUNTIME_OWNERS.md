# Agent runtime ownership

This is the source inventory and design input for [issue #4](https://github.com/oh-goon/oh-goon/issues/4).
It describes the implementation inspected at [26c1ed0](https://github.com/oh-goon/oh-goon/commit/26c1ed0bc5e75f2a3959760dd8c3a5ffa63320e4).
The contract questions below are proposed work, not implemented native-agent support or
qualification evidence. Current operator behavior is documented in [Agent Skills](../AGENT_SKILLS.md)
and [Agent providers](../AGENT_PROVIDERS.md). The [contributor contract](../../AGENTS.md)
and [Hashmarks handoff](HASHMARKS_EXECUTION_LAYER.md) retain their authority.

## Two different loops

Oh-Goon owns the outer workflow. Findings can lead to adjudication and a
proposal/native apply, then capture of the changed candidate, regression checks
and fresh review within the caller's budget. All-CLEAN reviews select final native
verification; its successful result ends convergence. Other failures, incomplete
evidence and exhausted limits stop the invocation. A selected coding agent would
own its inner reasoning, conversation, compaction, model configuration, tool
choices and solution strategy. Its choices
remain requests or claims under the execution permissions admitted by Oh-Goon.

The current implementation contains built-in bounded model protocols and direct
provider requests. It has no runtime Codex, OpenCode or Pi adapter. Example authoring
that names another harness does not establish an executable adapter.

## Current execution path

| Responsibility | Existing owner and handoff |
| --- | --- |
| Authored Plan, inputs and Skill selection | [FleetPlanResolver](../../backend/src/oh_goon/services/fleet_plan_resolver.py), [Agent authoring sources](../../backend/src/oh_goon/services/agent_authoring_sources.py), and [Skill registry](../../backend/src/oh_goon/services/agent_skill_registry.py) capture the caller-selected source closure. Locked remote Skills retain their selected commits. |
| Immutable delivery | [agent_source.py](../../backend/src/oh_goon/core/models/agent_source.py) owns `FrozenAgentSourceCapture`, `FrozenAgentStepHandoff`, identities and the delivery codec. Captured bytes travel separately from the resolved Plan. A delivery is not effect or disclosure authorization. |
| Captured-source retention and transport | [FleetPlanService._agent_target](../../backend/src/oh_goon/services/fleet_plans.py) stores exact handoff bytes through the existing [FleetContentStore](../../backend/src/oh_goon/integrations/fleet_content_store.py). [FleetPlanTarget](../../backend/src/oh_goon/core/models/target.py) binds exact delivery membership; [FleetPlanRunner](../../backend/src/oh_goon/integrations/fleet_plans.py) verifies availability before effects and supplies `agent_delivery_for`. These input objects are distinct from transient producer/model output. |
| Typed Agent resolution | [fleet_plan_step_resolution.py](../../backend/src/oh_goon/services/fleet_plan_step_resolution.py) verifies the frozen selection and constructs [ResolvedLocalSearchAgentStep](../../backend/src/oh_goon/core/models/fleet_plan.py). The current contract is read-only, uses a fixed harness set and rejects authored tool, egress and nested-strict demands. |
| Run admission and original source custody | [fleet_run_lifecycle.py](../../backend/src/oh_goon/services/fleet_run_lifecycle.py) owns Run admission. Ordinary Agent execution currently requires the standard-local path. Its separate `reserve_original_agent_source_for_attempt` capability requires exact strict deployment/source authority; it is not an ordinary startup prerequisite. |
| Attempt and Step execution | [fleet_plan_run_execution.py](../../backend/src/oh_goon/integrations/fleet_plan_run_execution.py) supplies the existing Step runtime; [fleet_plan_step_execution.py](../../backend/src/oh_goon/integrations/fleet_plan_step_execution.py) verifies delivery digest, occurrence, harness, model and timeout before dispatch. Strict Agent Step execution is currently rejected. |
| Repository evidence before reasoning | The original Step obtains Hashmarks task evidence through [hashmarks/agent_tools.py](../../backend/src/oh_goon/integrations/hashmarks/agent_tools.py). The producer owns repository relevance, presentation and qualifications. Evidence failure stops model disclosure; retrieval bounds cannot narrow the admitted review scope. |
| Physical processes and native tools | The Step's admitted command runner uses [engine/command.py](../../backend/src/oh_goon/engine/command.py) and its existing process-tree/runtime owners. Model and tool subprocesses receive the original cancellation, supervision and timeout parameters. Native tools retain their own semantics. |
| Typed claims and proposal publication | [agent_review.py](../../backend/src/oh_goon/core/models/agent_review.py), [agent_review_execution.py](../../backend/src/oh_goon/integrations/agent_review_execution.py), [agent_patch_apply.py](../../backend/src/oh_goon/integrations/agent_patch_apply.py), and [fleet_plan_step_publication.py](../../backend/src/oh_goon/integrations/fleet_plan_step_publication.py) accept and publish bounded results. Agent assertions cannot grant writes or establish verification truth. |
| Outer review/repair control | [agent_review_plan.py](../../backend/src/oh_goon/core/agent_review_plan.py) validates phase membership. [fleet_plan_repair.py](../../backend/src/oh_goon/services/fleet_plan_repair.py) selects phase Plans; [FleetPlanService.repair](../../backend/src/oh_goon/services/fleet_plans.py) admits, waits for and cancels the original Fleet Runs. |
| Durable results and presentation | Original repository-effect settlement and Journal owners persist the [OperationReport](../../backend/src/oh_goon/core/models/operation.py). [Step evidence](../../backend/src/oh_goon/integrations/_fleet_plan_step_evidence.py) and [Game Tape projection](../../backend/src/oh_goon/core/game_tape_step_projection.py) retain their existing result authority and read-only presentation roles. |

Standard-local execution supervises host processes and makes no kernel-isolation or
deep-custody claim. Selected strict/native qualification remains a separate workload
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
| Provider/model configuration | [runtime/agent_providers.py](../../backend/src/oh_goon/runtime/agent_providers.py) and [AgentConnection](../../backend/src/oh_goon/core/models/agent_providers.py) load one selected catalog, freeze public selection and resolve explicit credential references. | **KEEP** the current direct-model configuration. **ADAPT** future native-agent selection without importing or reinterpreting the agent's native configuration. |
| Pure generic harness contract | [AgentHarnessInput / AgentHarnessSubmission](../../backend/src/oh_goon/services/agent_harness_contract.py) project prework bytes and parse a generic untrusted report. Repository runtime call sites do not invoke its prepare/decode functions; the Step currently decodes the original handoff directly. | **ADAPT** this existing contract in place before runtime use. Its DTOs are **NOT AN OWNER** of execution or accepted phase results. Do not introduce another canonical protocol beside it and the live typed records. |
| Explicit provider diagnostic | [cli_agent_providers.py](../../backend/src/oh_goon/cli_agent_providers.py) sends caller-supplied text through `AgentProviderClient.complete` and reports a model reply. It does not attach repository/Skill context or admit an Agent workflow. | **KEEP** the diagnostic; it is **NOT AN OWNER** of Agent execution, verification or certification. |

These classifications apply to responsibilities, not whole-file deletion. The
concrete inner model/tool loop is in the Step integration; the inspected owners
contain no general native-style context-compaction or model-routing subsystem.
[agent_model_selection.py](../../backend/src/oh_goon/core/agent_model_selection.py)
combines replaceable prompt/protocol code with source, disclosure and untrusted-output
checks that remain necessary at the appropriate existing owner.

## Results, limits and observations

| Fact | Current authority and practical limit |
| --- | --- |
| Review and adjudication | [AgentReviewRecord](../../backend/src/oh_goon/core/models/agent_review.py) binds occurrence, candidate, scope and Skill and accounts for complete reviewed-path membership. `AgentAdjudicationRecord` binds candidate/scope and references the admitted reviews/findings, requiring exact disposition and repair-queue accounting. These remain admitted claims. Generic `CLEAN/FINDINGS/INCONCLUSIVE` submissions cannot replace these records unchanged. |
| Repair effect | The provider proposal is a bounded batch. The separate native apply owner validates write roots, candidate and all preimages before publication; partial publication is INCOMPLETE. Current [batch bounds](../../backend/src/oh_goon/integrations/agent_patch_apply.py) are 65,536 UTF-8 bytes, 32 operations and 32,768 replacement bytes per operation. Direct native-agent editing would change this effect contract. |
| Local context and actions | [agent_model_selection.py](../../backend/src/oh_goon/core/agent_model_selection.py) separates authored-prompt, complete-context, repository-observation and reply bounds. The Step retains short excerpts and two/four-action budgets according to mode; the Skill loop allows at most four actions. This is bounded history rebuilding, not compaction. Exact captured reads retain a separate 2,048-byte delivery bound. |
| Provider transport | The selected connection owns request/response byte budgets and model parameters. Oversized complete requests fail; they are not silently truncated. The [provider child](../../backend/src/oh_goon/integrations/agent_review_execution.py) separately bounds phase text to 65,536 UTF-8 bytes. Transport has no automatic provider fallback or retry. |
| Deadlines and cancellation | The original Step runner supervises every native command. A standalone Agent without repair execution limits currently applies its authored timeout per native command, including evidence/model/tool subprocesses. Repair execution additionally installs shared elapsed Step/campaign deadlines. A native adapter must not silently assume these are already identical whole-Agent deadline semantics. |
| Failure translation | [Step dispatch](../../backend/src/oh_goon/integrations/fleet_plan_step_execution.py) propagates cancellation through the original operation lifecycle and converts timeout/output-limit exceptions to bounded failure outcomes. Local model/read failures can become contract failures; [provider execution](../../backend/src/oh_goon/integrations/agent_review_execution.py) preserves a nonzero child result and validates successful output with the typed acceptors. Malformed or misbound claims are rejected; a zero process exit alone cannot establish an accepted Agent result or successful verification. These paths do not promise identical exit codes for every failure. |
| Outer continuation | [AgentReviewRepairCampaign](../../backend/src/oh_goon/services/fleet_plan_repair.py) and [AgentRepairContext](../../backend/src/oh_goon/core/models/agent_repair.py) bound linked phase Runs and fresh-candidate capture. Each Run retains its own result. Campaign control state lives in the invocation; reopening Run evidence does not automatically resume a campaign. Rerun/readmission cannot inherit expired repair limits. |
| Durable evidence | Stream evidence retains hashes, byte counts and line counts. Typed review/adjudication observations persist separately. Initial Hashmarks packets are included in transient supervised Step output; the current path does not retain a durable raw producer packet or model/tool transcript. [Live output](../../backend/src/oh_goon/runtime/game_tape_live_output.py) is bounded process-local state, and [Hashmarks preparation](../../backend/src/oh_goon/integrations/hashmarks/agent_tools.py) owns disposable scratch. |

The byte/action limits above describe this inspected implementation. They are not
portable native-agent capabilities or a second configuration owner. Source owners
remain authoritative. [Game Tape](GAME_TAPE.md) explains historical versus live
visibility; a missing model identity, internal tool event or transcript remains
unavailable rather than being reconstructed from an agent claim.

## Contract questions before a native adapter

The next contract change should answer these questions in the existing owners:

1. **Role versus implementation selection.** The current outer validator requires
   exact `provider-review`, `provider-adjudicate` and `provider-proposal` harness
   names. Separate phase/output capability from selected implementation while
   retaining phase ordering, exact accounting and original admission. Adding two
   launch commands alone cannot establish swapping the same workflow.
2. **One source and result handoff.** Connect the existing generic harness input
   to the real frozen delivery without a second capture/codec/identity. Reconcile
   its submission with the live review, adjudication and proposal records; keep
   the existing acceptors and effect owner authoritative.
3. **Required capabilities and effects.** Specify supported read-only roles,
   source/Skill delivery, observations and optional native features. Unsupported
   requests reject before the Step. Read-only proposal and direct editing need
   distinct demonstrated effect contracts; selecting a native agent grants no
   additional tool, write, recipient or network authority.
4. **Native configuration.** Let the selected agent own its model settings,
   prompting and context machinery. Freeze explicit execution/disclosure intent
   once and verify its binding at the receiver. Decide which actual native
   identity/configuration facts can be observed, without copying secrets into
   argv, journals or Game Tape or adding an Oh-Goon native-config resolver.
5. **Hashmarks through inner reasoning.** Demonstrate mandatory qualified
   producer evidence across follow-ups, compaction, model routing and permitted
   native session resume. Initial context delivery alone does not prove retention
   across those boundaries. Preserve full scope and qualifications; do not
   rebuild compaction or add a local CodeMap/relevance/verification selector.
6. **Deadline and session semantics.** Decide the whole-Agent versus per-command
   deadline at the original Step owner. Distinguish native conversation/session
   state from Run retry/recovery and campaign continuation. No new agent store,
   scheduler or independent retry route follows from native session support.
7. **Bounded observable evidence.** Decide which available native events and
   producer identities need typed observations or bounded artifact promotion
   through existing evidence/content owners. Keep raw prompt/credential privacy
   and scratch cleanup explicit. Do not advertise durable transcript or producer
   packet replay before an owner actually retains it.
8. **Migration and removal proof.** Demonstrate one supported native adapter,
   then a second independent implementation on the same admitted workflow.
   Compare permissions, source, cancellation, failure, verification and Game Tape
   accounting; their solutions may differ. Retire only demonstrated overlaps.

This inventory does not freeze a universal agent protocol, select a native agent
version or authorize replacing current execution. The [retained workload ADR](../architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md)
records the shared-execution decision and its original assumptions. Current
normal-local behavior does not gain a strict signer/custody qualification gate.

## Existing proof and its limits

| Boundary | Current regression evidence |
| --- | --- |
| Original Step/model supervision and cancellation | [Original model turn](../../backend/tests/integration/test_agent01b3a_original_fleet_model_turn.py), [typed local Agent Step](../../backend/tests/integration/test_agent01b2a_typed_local_search_step.py) |
| Bounded inner tool protocol and proposal-as-data | [Skill loop](../../backend/tests/integration/test_agent01b3g_bounded_skill_loop.py), [source-bound proposal](../../backend/tests/integration/test_agent01b3i_source_bound_patch_proposal.py), [context bounds](../../backend/tests/unit/core/test_agent_model_selection.py) |
| Complete provider closure and typed output acceptance | [Provider execution](../../backend/tests/unit/integrations/test_agent_review_execution.py), [review/adjudication resolution](../../backend/tests/unit/services/test_agent_review_resolution.py), [provider wire boundaries](../../backend/tests/dependency_complete/test_agent_provider_http.py) |
| Frozen provider selection and credentials | [Provider configuration](../../backend/tests/unit/runtime/test_agent_providers.py), [provider connection](../../backend/tests/integration/services/test_agent_provider_connection.py) |
| Producer qualifications and evidence bounds | [Hashmarks Agent tools](../../backend/tests/unit/integrations/test_hashmarks_agent_tools.py) |
| Pure generic prework contract | [Harness interface](../../backend/tests/unit/services/test_skill03a_harness_interface.py), [effect requests](../../backend/tests/unit/services/test_skill03b_effect_requests.py) |
| Linked review/repair Runs and source recapture | [Review/repair integration](../../backend/tests/integration/services/test_agent_review_repair_loop.py), [bounded repair](../../backend/tests/integration/test_agent_bounded_repair.py), [capture boundaries](../../backend/tests/unit/services/test_agent_repair_source_capture.py) |
| Installed local model behavior | Opt-in [native model review](../../backend/tests/integration/test_agent01b3c_native_model_review.py) and [native Skill cycle](../../backend/tests/integration/test_agent01b3h_native_skill_cycle.py) |

Synthetic model/HTTP fixtures establish protocol and lifecycle behavior within
their tested scope. Installed-model tests are separate from privileged custody
proofs; neither establishes a replaceable external native harness. This document
does not promote any test result into APP, native-host or release certification.
