# Architecture

> Repository-wide authority/certification changes are governed by [Fail before work and anti-cucumber authority](development/FAIL_BEFORE_WORK.md). Known invalid authority fails before workload; native authority is resolved once; source-shape diagnostics do not become APP certification authority.

oh-goon is a local-first control plane with one public Python process composition/lifecycle root, one SQLite coordination boundary, one append-only Journal, and domain-specific lifecycle owners. The repository is pre-live: prefer deleting a wrong internal design and updating all callers over preserving compatibility surfaces.

Selected Agent Steps use this same execution lifecycle. The [Agent Skills guide](AGENT_SKILLS.md) describes captured resources, bounded local-model inspection, explicitly authored proposal/apply/test Steps and failed-test feedback. Autonomous fresh-source repair and retesting remain future work. The [canonical Agent execution decision](architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md) defines the ownership boundary.

[Current architecture decisions](development/ARCHITECTURE_DECISIONS.md) summarize the active invariants. Retained ADRs explain why a boundary exists; their checkpoint results and unfinished native proofs remain historical evidence. Ordinary Linux/WSL execution uses its selected workload contract. Additional containment or custody requirements apply only when the selected backend/workload requests those guarantees.

## Ownership matrix

| Concern | Canonical owner |
|---|---|
| process construction/lifecycle | `ApplicationRuntime` |
| local durable coordination | `LocalDatabase` / SQLite |
| optional Scout analytical sidecar | `ScoutAnalyticalDatabase` / adjacent SQLite; disposable derived observations only, never coordination |
| canonical operation/Fleet/recovery history | append-only hash-linked `Journal` |
| verified Journal interpretation | `JournalReadView` + disposable `JournalIndex` |
| cross-process execution ownership | `ExecutionCoordinator` + private SQLite lease store |
| Direct command identity/safety/lease admission | `DirectEffectAdmission` |
| physical repository-effect lifecycle | `RepositoryEffectExecution` |
| unresolved effect coordination | `RepositoryEffectAttemptStore` |
| Fleet target parallelism | `FleetTargetScheduler` |
| Fleet live lease + Journal lifecycle mutation | `FleetRunLifecycle` |
| Fleet orchestration execution | `FleetRunExecution` |
| Fleet admission policy | `FleetAdmissionPlanner` |
| Repository metadata observation | `engine/project.py` |
| user-selected repository path normalization | `core.path_authority.navigation_path` |
| Repository root traversal | `RepositoryDiscoveryService.git_roots` |
| Fleet command identity/query/submission | `FleetRunControlPlane` |
| Fleet lifecycle reduction | `core/run_projection.py` over structural `FleetRunState` |
| runtime safety observation/classification | `RuntimeSafetyCut` + `SystemInvariantAuditor` |
| effect admission proof | `RuntimeSafetyService` |
| Schedule admission/capability binding | `ScheduleAdmissionPlanner` |
| Schedule definition persistence | `ScheduleDefinitionStore` |
| Schedule execution state/CAS | `ScheduleExecutionStore` + core Schedule transitions |
| capability meaning for new work | `CapabilityDefinition` / `ResolvedCapabilityIntent` |
| historical capability meaning | durable `CapabilitySnapshot` |
| adapter invocation/returned-evidence validation | `CapabilityExecutor` |
| derived product state | named `core/projections/*` builders |
| frontend read authority | `ReadController` |
| frontend write admission/causality | `MutationController` |
| durable Runs review presentation state | `runReviewState.ts` + `RunsPage` |
| Overview timeline browser/query state | `OverviewPage` |
| frontend HTTP retry/timeout/cancellation | `httpTransport.ts` |

Scout's analytical sidecar is deliberately outside the canonical SQLite coordination file. It is a rebuildable observer cache, not a second durable-state authority: execution leases, Journal facts, admission, schedules and repository mutation remain exclusively in `LocalDatabase`. This separation prevents Scout materialization from contending for the canonical SQLite writer lock.

### Path clarity

Write paths must expose their real owner directly. Fleet admission goes `FleetRunControlPlane -> FleetAdmissionPlanner -> FleetTargetSelector`; `FleetService` never participates in write admission. `ApplicationRuntime.build()` delegates concrete wiring to private `_application_*.py` wiring modules grouped by construction ownership; Fleet/Schedule owners stay explicit rather than hiding constructors inside managers. Schedule advancement is identity-based (`schedule_run_id`) because `ScheduleExecutionStore` owns current state, while its one canonical Fleet Run ID is derived rather than persisted as a second claim. Fleet lifecycle begin/finish derive `fleet_run_id` from the exact `ExecutionSession`. Repository metadata observation is centralized in `engine/project.py`; Git-root traversal policy is centralized in `RepositoryDiscoveryService` with `require_pyproject` explicit. Do not restore pass-through services, cross-service parsing helpers, duplicate filesystem walkers, scheduler-owned execution policy state, generic Workflow owners, or duplicate owner identities.

Frontend presentation follows the same rule. Durable Runs review identity is URL-backed state owned at `RunsPage`/`runReviewState`; Game Tape surfaces consume those values rather than mirroring them. Schedule mutations invalidate canonical resources and the read owner reloads them; pages do not issue a second catalog refresh. Page-level browser state belongs to the page owner, not leaf feature components. Ephemeral interaction mechanics remain local and are not promoted into global state.

## Execution environment and admission

The selected execution contract determines its required guarantees. Standard
local work uses the existing Fleet/Step lifecycle; a deployment that selects
authenticated or strict execution supplies the corresponding native policy,
resource, and custody authority. These contracts do not add a setup gate to
unrelated local development or read-only inspection.

For configured executors, `runtime/_executor_configuration.py` owns the version
8 inventory and its explicit `execution.environment.backend` selector. Plans
request guarantees, not backend names. The parser accepts one configured
backend; it rejects obsolete `kind`, implicit selection, fallback candidates,
and post-admission backend replacement. `ResolvedExecutionEnvironment`
transports the existing backend configuration identity, capability identity,
executor generation, path authority, and resource declarations. It does not
independently resolve tools or prove physical containment.

Authenticated Fleet admission remains owned by `FleetAdmissionPlanner`, with
`FleetPreworkProvisioner` supplying explicit policy enrollment before Plan work.
The original `ExecutionCoordinator.reserve_fleet_with` transaction captures the
Run and native source, lock, tool, environment, and backend facts. Fixed native
issuers and independently configured trust roots verify those facts before the
operational and causal admission records commit, with the current signer epoch
and Fleet lease checked in the same transaction. A failed publication leaves no
committed admission or provisional lease. Callers cannot provide substitute
digests, choose an issuer, or bypass this boundary through another ingress.

`CausalRunJournal` owns committed sequence and authenticated closure for that
protocol; `core/enforcer_lace_semantics.py` owns its pure causal interpretation.
Committed order determines which execution or teardown envelope applies.
Signature validity alone proves neither permission nor containment, and a
workload exit alone proves neither teardown nor terminal sealing. Lost control
or unproven cleanup must retain unresolved evidence rather than manufacture
success. Current admission and replay limits are in
[architecture decisions](development/ARCHITECTURE_DECISIONS.md#authenticated-replay-requires-native-parent-authority).
Use [Enforcer receipts](development/ENFORCER_RECEIPTS.md) and
[frozen tool authority](development/FROZEN_TOOL_AUTHORITY.md) for their concrete
formats and execution boundaries.

## Repository write path

Direct and Fleet share one physical effect state machine. They differ only in admission/provenance and in when the outer execution lease closes.

```text
Direct command(operation_id)
  -> DirectEffectAdmission.run
  -> resolve intent + replay/rebinding check
  -> one revision-bound Runtime Safety Cut + quarantine check
  -> atomic direct lease + RepositoryEffectAttempt(CLAIMED)
  -> RepositoryEffectExecution.execute_direct_claimed

FleetRun target
  -> FleetTargetScheduler
  -> RepositoryEffectExecution.execute_fleet_target
  -> deterministic operation_id + RepositoryEffectAttempt(CLAIMED)

shared effect path
  -> repository fingerprint/cancellation fence
  -> ExecutionSession.mutate: exact owner + EFFECT_STARTED
  -> CapabilityExecutor -> concrete runner-kind adapter
  -> canonical settlement:
       Fleet target: ExecutionSession.mutate
       Direct:       ExecutionSession.finish
     transaction contains Journal OperationReport + attempt retirement
     Direct finish also releases its execution lease
```

`CapabilityExecutor` has no durable authority. It performs a concrete runner-kind lookup, invokes the concrete runner through the admitted-request contract, and validates that returned evidence still matches the complete admitted execution envelope: operation ID, Fleet owner, repository, capability, action, and target. There is no `CapabilityAdapterResolver` Protocol or default resolver registry. The canonical `OperationReport` includes `returncode`; `OperationExecution.returncode` is derived from that report so replay can reproduce the exact committed result.

A successful Fleet target keeps the Fleet lease because more targets/terminalization remain. `FleetRunExecution` delegates terminal settlement to `FleetRunLifecycle`, which uses `ExecutionSession.finish` to atomically couple the terminal Fleet fact and lease deletion. The same lifecycle owner coordinates lease + Journal admission/start/cancel/submission-rejection; orchestration services do not duplicate that transaction policy. Worker begin performs the cancellation-vs-`RUN_STARTED` decision inside the exact-owner transaction so cancellation cannot commit between a pre-check and the start fact.

### Exact-owner closure rule

Once durable work is admitted, normal control flow cannot silently release its lease. `ExecutionCoordinator.activate()` creates an `ExecutionSession` with no context-manager cleanup and there is no `abandon()` API. Ownership disappears only through an exact-owner `finish` transaction, or because the process died and restart recovery observes the durable lease/attempt state.

This is deliberately fail-closed: an exception before a required durable resolution leaves evidence for recovery rather than manufacturing an “idle” system with unexplained active work.

### Ambiguous-settlement rule

The canonical Journal result dominates transport/connection ambiguity. If repository settlement raises after the transaction may have committed, the effect owner checks the exact `operation_id`; a matching canonical report is returned as success. Only absence of canonical evidence permits interruption/unknown classification. Recovery command races use the same principle: concurrent retries replay the winning reconciliation/acknowledgement fact.

## FleetRun lifecycle

FleetRun is orchestration-only. It owns admission, immutable targets/capability, start, cancellation intent, references to canonical repository completions, and terminal outcome. Claim/effect/reconciliation state belongs to `RepositoryEffectAttempt`.

`FleetRunState` is a six-variant discriminated union:

```text
QueuedFleetRun
RunningFleetRun
CancellingFleetRun
CompletedFleetRun
CancelledFleetRun
FailedFleetRun
```

`core/run_projection.py` transitions directly between these concrete states. It does not maintain a second `status + nullable timestamps/failure` fold and there is no separate `run_transitions.py` table. Every intermediate reducer state is therefore structurally valid.

## Journal and projections

The Journal is immutable history; projection state is disposable.

```text
SQLite transaction
  -> JournalFactStore verifies hash chain
  -> JournalReadView
  -> JournalIndex
       operation lookup
       repository timelines/freshness
       Fleet event streams
       FleetRunDetail reductions
       latest/active/history indexes
  -> named product projections
```

Write-time validation uses that same verified `JournalReadView`/`JournalIndex` cut. `Journal` itself constructs and validates semantic Fleet facts; `JournalFactStore` owns only shared raw append/hash-chain/read mechanics. Journal mutation APIs are caller-transaction-bound `*_in(connection, ...)` operations so lifecycle owners can atomically combine lease/effect state with facts; production has no transaction-owning commit/admit/start/terminalize convenience writer. Do not recreate `FleetRunFactWriter`, `journal_projection.py`, persisted projection tables, mutable run summaries, or standalone Journal write transactions.

### Immutability boundary

Published typed state is immutable after validation. Fleet/Schedule intent and lifecycle states, canonical typed evidence, completed product projections, registry bindings, shared policy tables, application composition, and verified Journal indexes use frozen models/dataclasses, tuple-backed collections, and read-only mappings. Transitions construct new domain values rather than editing an admitted/published value in place.

Mutation remains local where it is the real implementation mechanism: reducer folds/builders, aggregation dictionaries, execution sessions, locks, subprocess/resource ownership, and other construction state. Those mutable objects do not become canonical facts or shared read projections. Opaque third-party JSON is the explicit exception to deep structural freezing; Journal commit/read boundaries isolate those leaves with defensive copies instead of introducing a custom frozen-JSON object model.

## Runtime safety and crash consistency

A Runtime Safety Cut is one SQLite-consistent observation of durable revision, Journal, execution lease, unresolved effect attempts, and active Schedules. Admission carries its durable revision into the write CAS. Filesystem drift is checked again at the repository-effect fence. The revision advances only when a revisioned write transaction actually changes durable rows, so an empty transactional control path does not create false staleness. Repository include/exclude mutations likewise CAS the revision of the managed-scope view that proved the repository was eligible.

SQLite cannot transact Git, uv, subprocess, or filesystem effects. The policy is therefore at-most-once automatic replay:

- failure before `EFFECT_STARTED`: retire the unstarted attempt under exact ownership; safe to retry;
- failure after `EFFECT_STARTED` without canonical evidence: preserve `OUTCOME_UNKNOWN`; reconcile from the captured baseline;
- canonical report committed: operation evidence is authoritative and the mutable attempt row is gone. Canonical settlement is the final declared fallible effect boundary; there is no `AFTER_OPERATION_COMMIT` fault point.

Recovery classifications/acknowledgements are immutable Journal evidence. Mutable attempt rows represent unresolved coordination only. Recovery may mutate those rows only while execution is idle, and it rechecks that fact inside each write transaction so another live process remains authoritative over its own attempt. Startup automatically processes only `CLAIMED`, `EFFECT_STARTED`, and `OUTCOME_UNKNOWN`; `PARTIALLY_APPLIED` and `INDETERMINATE` are stable operator-required quarantine across restarts. Explicit terminal recovery retries resolve from Journal evidence when the mutable row has already been retired.

`JournalReadCache` is concurrency-safe by publication rather than lock choreography: cache key and verified `JournalReadView` are one immutable entry. A lookup therefore cannot validate one revision key and then return a view replaced by another thread.

`SystemInvariantAuditor` treats live exact ownership as part of the invariant. `CLAIMED`/`EFFECT_STARTED` is only an error when no matching live lease owns that attempt; a Direct lease must own exactly one live Direct attempt. Normal in-flight effects therefore do not degrade runtime safety.

## Repository Schedule ownership

A repository Schedule is a durable trigger definition, not a workflow definition. `ScheduleDefinitionStore` owns one canonical explicit repository-ID set, one resolved `FleetPlanTarget`, interval/state, immutable generation identity, and monotonic record version. Replace/delete mutations are generation/version bound so stale commands cannot mutate a deleted-and-recreated Schedule, and deletion is rejected while a Schedule execution is active.

Admission is `ScheduleAdmissionPlanner`: it reads the exact Schedule definition and Runtime Safety Cut from one SQLite observation, resolves the current `EXECUTE_PLAN` capability policy, and returns one immutable `ScheduledFleetRunIntent` containing the bound capability snapshot, resolved Plan target, and `ExplicitRepositoryScope` with the exact repository target set. The occurrence insert is CAS-bound to the durable revision observed during admission; repository targets are then admitted one at a time through the existing Fleet executor with stable per-repository Run IDs.

`ScheduleExecutionStore` persists structural Schedule lifecycle state, the immutable intent, exact attention/failure evidence, timestamps, and `record_version`. There is no ordered step list, cursor, `step_index`, or hidden workflow graph. The canonical Fleet Run identity is derived from `schedule_run_id` alone. `ScheduleExecutionEngine` therefore has only one job: start or observe that one Fleet Run and transition the Schedule from the same canonical evidence.

`ScheduleScheduler` owns periodic invocation only. A new Schedule waits one full interval from `created_at`; subsequent due times are anchored to the latest terminal `finished_at`; process startup is never a trigger. Runtime safety is checked at admission/tick boundaries, while already-observed terminal Fleet evidence may still be settled into durable Schedule state.

Shared Runs and Schedules may both target many repositories, but they own different trigger semantics. Shared Runs pass operator-selected `repository_ids` / `all_repositories` scope directly into Fleet admission. A Schedule persists one explicit repository target set and one resolved Plan; each occurrence admits those targets independently through Fleet and cannot express executable recipe steps. Both paths converge on the same Fleet admission, Enforcer, event, history, and Game Tape owners. See `docs/AUTOMATION_MODEL.md`.

## Diagnostic evidence: source-owned, sink-passive

Runtime diagnostics are a separate non-authoritative evidence plane. The component where a diagnostic fact occurs owns its semantic `DiagnosticEvent`; `DiagnosticRouter` only fans that immutable fact out to passive sinks. Production uses concise stderr plus private bounded rotating JSONL history. A sink may format or persist evidence but cannot change lifecycle, retry, cancellation, settlement, admission or scheduling. Sink failure is fail-open and cannot alter the execution outcome. Exact Run/repository/Step/request identities are carried when the source owns them, so degraded observability and unexpected HTTP failures remain causally searchable without turning diagnostics into a second Run model. See `docs/development/DIAGNOSTICS.md`.

## Game Tape: read-only execution observability

Game Tape is a read-only projection of the exact Fleet plan already admitted for execution. Failure recovery preserves that boundary: Game Tape may explain and navigate to a canonical recovery action, but it never starts work. Runs owns exact-target rerun and executor-placement readmission; Recovery owns uncertain or partially committed repository effects. Live UI refresh advances from backend-owned output cursors and preserves already-visible evidence monotonically, while one bounded canonical run/repository/step failure focus rolls exact errors upward for direct step-evidence drill-down without introducing severity, root-cause, scheduling, or persistence semantics. `goon.yml` resolves to one immutable `ResolvedFleetPlan`, that plan is embedded in `FleetPlanTarget`, the executor consumes it, and one canonical chronological execution-ledger projection orders the durable run lifecycle, admitted-plan, operation-report, and step-execution facts. `FleetRunDetail.game_tape` exposes those same facts through Summary, Flow, Timeline, Play-by-Play, Logs/evidence, Search, and a shared inspector. The ledger is not another persisted event store and cannot write to the journal. Lifecycle meaning, repository/step execution identity, and timing bounds each have one direct owner shared across the views. Summary additionally exposes plan-vs-actual, an execution receipt, safe immutable environment fingerprints, and deterministic bottleneck arithmetic. Historical Run Diff is produced by `compare_game_tapes()` over two canonical Game Tape projections; it compares only debugging facts such as status, duration, commit/branch, command and provenance-source changes rather than introducing a generic diff framework. Search filters the authoritative Game Tape payload directly and creates no index/store. Flow is intentionally graph-like only as a visualization: nodes are not schedulable, edges are not dependencies, and no DAG scheduler/topological semantics exist. Step wall-clock timing is recorded by the execution sequence, while raw stdout/stderr remains transient and historical Game Tape exposes only durable hash/size/line evidence; failed-step navigation uses those durable evidence anchors without reconstructing raw logs. Missing source lines, historical raw logs and tool-version strings remain explicitly unavailable instead of being inferred. Secret or literal values are never copied into the Game Tape projection. See `docs/development/GAME_TAPE.md`.

## The Scout: optional cross-run observability

The Scout is a read-only projection over canonical Journal/Fleet evidence across many executions. It owns aggregates, trends, peer comparisons, workload/resource findings, and the navigation path from league-level signals toward Repository → Plan → Goon → Step. It never owns one-run logs or debugging; that boundary ends with an explicit Game Tape handoff. Scout has no runtime dependency from admission, executor, scheduler, retry, cancellation, placement, or recovery, and its API is GET-only. Missing telemetry remains unavailable rather than becoming zero. `backend/src/oh_goon/repository_tooling/architecture/scout_observer_contract.py` enforces the dependency boundary from explicit `oh_goon.scout` package membership. See `docs/development/THE_SCOUT.md`.

## Frontend concurrency

Read and write semantics are intentionally different.

```text
read
  -> ReadController(scope, identity, generation, minimum causality)
  -> AbortSignal reaches every HTTP attempt and retry wait
  -> older same-scope work may be superseded

write
  -> MutationController global mutation admission
  -> dispatch command
  -> if server confirms success: causality always advances
  -> dependent ReadResources invalidate
  -> UI may ignore obsolete presentation, but may not erase the side effect
```

A successful dispatched write is never converted to a `superseded` outcome because navigation or selection changed. Fleet/workflow start requests carry caller-generated stable run IDs so an ambiguous retry cannot create a second logical run.

`httpTransport` checks an already-aborted signal before dispatch and makes retry backoff abortable; a cancelled read must not issue a later retry.

## Composition rule

`ApplicationRuntime` is the one public process composition/lifecycle root. Concrete constructor wiring may live in private `_application_*.py` modules grouped by construction ownership, but those modules are not alternate runtimes and own no lifecycle or durable state. Do not replace explicit construction with a DI registry/service locator or pass-through factory layer.

## Fleet plan authority: YAML + TOML + Python

Fleet plans intentionally use three formats with non-overlapping authority. YAML is the portable plan/template and GitLab-migration representation. TOML declares imported Python/uv extensions, pins exact Git commits, and explicitly allowlists selected `[project.scripts]`. Python is the only semantic authority: `FleetPlanResolver` recursively flattens YAML template modules and binds already-synced Fleet-owned extension checkouts into an immutable `ResolvedFleetPlan` before admission.

Extension/tool acquisition remains explicit and occurs before execution. The Plan composer edits one Plan YAML roster through the revision-bound authoring service and shares the same resolver as execution; it supports only add/remove/replace/move Goon operations. Plan/Goon inputs are resolved before admission, imported Goon requirements retain provenance, and the complete Goon graph is read/resolved once into the immutable sequential `ResolvedFleetPlan`. Runtime step/isolation/retry/evidence/recovery owners remain unchanged; there is no Plan-module refactor/promotion path or second execution engine.

## Native tool authority

Oh-Goon owns orchestration and execution authority, not duplicate implementations of native tools. When a native tool already owns an operation, Oh-Goon invokes that semantic owner directly and limits itself to Oh-Goon-owned sequencing, isolation, bounded execution, authority transport, identity/evidence binding, and `PASS`/`FAIL`/`INCOMPLETE` reporting. It must not create an alternate resolver, cache semantics, lifecycle model, retry algorithm, rollout controller, package manager, test runner, formatter, type checker, Git implementation, container engine, cluster scheduler, or substitute proof of the same native fact.

This boundary applies to uv/npm/pytest/Ruff/mypy, Docker/BuildKit, Kubernetes, Git, operating-system process facilities, and future native tools alike. See [Native tool authority](development/NATIVE_TOOL_AUTHORITY.md) for the normative rule, allowed orchestration, forbidden shadow authority, retry boundary, and design test.

## Validation ownership

Runtime safety is proved by behavioral backend and integration tests that exercise real admission, filesystem isolation, Journal settlement, recovery, and Scout behavior. Repository source checks are intentionally limited to cross-owner dependency direction, local-only frontend networking, exact shipped-template grammar, production-template safety, Scout's observer boundary, release identity, and native package-manager validation of the authoritative dependency locks.

Repository checks live only under `scripts/`; the installed product has no architecture-rule registry or architecture-inspection CLI. Private names, call chains, documentation sentences, and historical file splits are not compatibility contracts.

Architecture checks therefore certify public protocol/schema identities and cross-owner package/import structure, not private local/helper names or source-expression spelling. Behavioral outcomes remain executable-test authority. Scout is owned by package membership; League Team identity is owned by descendant Git roots; canonical source exclusions are exact current artifact classes rather than historical name prefixes.

## Responsibility-first names and product vocabulary

Module boundaries follow responsibility and authority, not line-count targets. A long cohesive owner stays cohesive; extraction requires an independently understandable responsibility, a stable responsibility name, and a dependency direction that does not scatter one invariant across private forwarding modules.

Cross-module names must reveal their decision owner. Hashmarks vocabulary is repository intelligence (`evidence`, `repository impact`, `repository qualification`, `consumer conformance`); Oh-Goon vocabulary is execution authority (`admission`, `authority`, `execution`, `certification`, `sandbox`, `supply`, `Game Tape`). Interoperability names explicitly identify Hashmarks evidence and remain consumer-side binding/conformance concepts, never shared authority abstractions.

Bare generic contract names such as `Identity`, `Context`, `Receipt`, `Verifier`, `Result`, `Manifest`, or `Provenance` are acceptable only in a genuinely local scope. Cross-module contracts add a responsibility noun. Repository intelligence is observed; execution source is admitted. Naming should make a cross-product authority mistake look linguistically wrong before code is opened.
