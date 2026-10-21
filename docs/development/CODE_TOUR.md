# Code tour

oh-goon separates **composition**, **admission**, **physical repository effects**, **Fleet orchestration**, **facts/projections**, and **frontend causality**.

`backend/src/oh_goon/application.py::ApplicationRuntime` is the only public process composition/lifecycle root. Its private `_application_*.py` builders wire concrete ownership clusters; API/CLI code uses the resulting product services and must not bypass them to manipulate stores/leases directly.

## Fleet write path

```text
api/routes/fleet_runs.py
  -> services/fleet_run_control_plane.py
       caller-stable FleetRun identity, admission, cancellation, query, submission
  -> services/fleet_admission.py
       one Runtime Safety Cut + direct target/prerequisite selection
  -> services/fleet_run_lifecycle.py
       one owner for Fleet lease + Journal admission/start/cancel/finish
  -> runtime/execution/coordinator.py
       exact lease primitives under caller-owned lifecycle transactions
  -> services/fleet_run_execution.py
       orchestration only; delegates lifecycle mutations
  -> services/fleet_target_scheduler.py
       target parallelism only
  -> services/repository_effect_execution.py
       the single Direct/Fleet repository-effect lifecycle
  -> services/capability_executor.py
       concrete runner dispatch + evidence validation only
  -> engine / integrations
  -> runtime/journal.py + runtime/effect_attempts.py
       owner-atomic canonical evidence / unresolved coordination
```

`FleetService` is read-only snapshot projection and is deliberately absent from this write path. There is no `FleetCapabilityAdmission` pass-through. Repository metadata used by selectors comes from `engine/project.py`.

`FleetTargetScheduler` must not grow effect-state logic. `RepositoryEffectExecution` is the one owner of claim → fence → effect-start → runner → settlement/interruption.

## Direct write path

```text
CLI / direct service caller
  -> DirectEffectAdmission.run
       resolve intent + stable operation-id replay/rebinding check
       one safety cut + quarantine check
       baseline
       atomic direct lease + CLAIMED attempt
  -> RepositoryEffectExecution.execute_direct_claimed
       shared physical-effect state machine
  -> Direct successful settlement uses ExecutionSession.finish
       OperationReport + attempt retirement + lease release atomically
```

`DirectEffectAdmission` is a real boundary because command identity/safety/lease admission differs from physical effect execution. Do not turn it into a pass-through wrapper, and do not move fence/start/runner/settlement logic into it. Also do not restore `DirectOperationService` merely to preserve the former file boundary.

## Execution ownership

`ExecutionCoordinator.activate(ticket)` returns an `ExecutionSession`; it has no hidden context-manager cleanup. Once durable work is admitted, normal lease deletion occurs through `ExecutionSession.finish` only. `mutate` retains the lease and is used for intermediate owner-checked state such as `EFFECT_STARTED` and Fleet target settlement.

## Capability ownership

- `core/capability_*_definitions.py` — immutable domain definitions for new work.
- `core/capability_registry.py` — validates/indexes definitions and resolves one `ResolvedCapabilityIntent` at admission.
- `CapabilitySnapshot` — durable historical meaning; readers do not reinterpret through today's registry.
- capability runners implement the common admitted-request preparation contract directly; `application.py` registers them by runner kind.
- `services/capability_executor.py` — concrete runner-kind dispatch; no pass-through adapter, resolver Protocol/registry, or persistence.

## Journal/read path

```text
SQLite
  -> JournalFactStore        hash-chain decode/verify + append
  -> JournalReadView
  -> JournalIndex            operations, timelines, Fleet event/run indexes
  -> core/run_projection.py  structural Fleet reducer
  -> named core/projections
  -> FastAPI
  -> generated OpenAPI/TypeScript contracts
  -> React
```

Write-time Journal validation also calls `JournalFactStore.read_view(connection)`. There is one semantic projection path for a Journal revision.

## Fleet lifecycle ownership

- `services/fleet_run_lifecycle.py` — the one live owner of lease+Journal admission, start, cancellation, terminal settlement, and submission rejection.
- `core/models/run.py` — six concrete `FleetRunState` variants plus concrete events.
- `core/run_projection.py` — direct structural state/event reduction and operation-lineage validation.
- `runtime/journal.py` — canonical semantic Journal writer; constructs/validates Fleet lifecycle facts and operation evidence under caller-owned transactions.
- `runtime/journal_cache.py` — publishes verified Journal cache key + view as one immutable concurrent snapshot.
- `core/models/base.py::ImmutableModel` — common boundary for admitted/published typed domain values (`extra="forbid"`, `frozen=True`); published ordered collections use tuples.
- `runtime/journal_index.py` — immutable verified read index; published lookup maps are `MappingProxyType` rather than caller-mutable dictionaries.

There is no `core/run_transitions.py`; do not recreate a status table beside the structural reducer.

## Durable revision and managed-scope ownership

- `runtime/database.py` — transaction boundary; every durable write advances `durable_revision` once when rows actually changed, with no revision-bypass mode.
- `runtime/state.py` — location/exclusion persistence; include/exclude require the revision observed by the caller.
- `services/repositories.py` — proves a repository is managed from one `LocalStateReadView` and carries that cut's revision into exclusion mutation.

## Repository discovery/traversal ownership

- `engine/project.py` — one pyproject/TOML/Python/dependency metadata observation implementation shared by selectors and product projections.
- `services/repository_discovery.py::RepositoryDiscoveryService.git_roots` — one Git-root/depth/pruning traversal policy; `require_pyproject` makes discovery-vs-migration intent explicit.
- `services/migration.py` — migration classification only; no second `os.walk`.

## Repository-effect recovery ownership

- `runtime/effect_attempts.py` — unresolved generic attempt persistence/CAS.
- `services/effect_quarantine.py` — quarantine projection from unresolved attempts.
- `services/effect_recovery.py` — execution-fenced restart/operator reconciliation; startup owns only automatic states, while partial/indeterminate quarantine is restart-stable, repeated reconcile replays its exact Journal classification, and terminal explicit commands replay Journal evidence.
- Journal reconciliation/acknowledgement facts — immutable history after mutable coordination is retired.

Direct/Fleet origin is provenance only.

- `runtime/execution/run_supervisor.py` — process-local live Fleet submission set + executor lifetime + one bounded typed last-failure diagnostic; finished per-run status/futures/history belong to the Journal and are not retained here.

## Schedule product ownership and durable execution

- `runtime/schedule_definitions.py` — caller-owned `schedule_id`, immutable definition `generation_id`, version-bound replay-safe replace/delete, and active-execution deletion guard.
- `services/schedule_admission.py` — manual/scheduled one-cut definition + latest execution + due decision + Runtime Safety Cut observation, then one immutable `ScheduledFleetRunIntent`.
- `runtime/schedule_executions.py` — structural Schedule execution persistence + record-version CAS.
- `services/schedule_execution.py` — starts/observes exactly one deterministic Fleet Run per `schedule_run_id` and terminalizes the Schedule from canonical Fleet evidence.
- `services/schedule_timing.py` — wall-clock interval ownership only.
- `runtime/schedule_scheduler.py::ScheduleScheduler` — process-local periodic invocation/lifetime only.

The product owner is `services/schedules.py::ScheduleService`: one Schedule selects an explicit repository target set, one resolved Plan, and one interval. There is no retained `Workflow*` model/store/engine, no scheduler recipe language, and no compatibility decoder for the removed representation. Manual Schedule start receives a caller-stable `schedule_run_id`; clock-triggered starts generate a fresh identity because they are new logical commands. `ScheduleExecutionEngine.advance(schedule_run_id)` reloads current structural state from SQLite; callers never pass stale execution state as authority. Process startup is not a Schedule trigger. Shared Runs remain the separate operator-triggered multi-repository path; Schedules are the time-triggered path.

## Frontend boundaries

- `domain/concurrency/ReadController.ts` — read identity/generation/abort authority.
- `domain/concurrency/MutationController.ts` — write admission + causal revision only; no write supersession identity.
- `app/api/httpTransport.ts` — raw fetch, timeout, GET retry and abortable retry waits.
- `app/api/mutationTransport.ts` — write diagnostics/result classification.
- `domain/api/generated/operations.ts` + `validators.ts` — stable OpenAPI-owned facades for operation facts and runtime decoding; large generated type/schema/validator bodies live in bounded deterministic generated shards while component validators stay internal.
- `app/createApiClient.ts` — readable frontend API mapping and argument shaping only; no route/method/schema declarations.
- `domain/identity/commandIdentity.ts` — caller-generated Fleet/Schedule run IDs.
- `app/pages/runReviewState.ts` + `RunsPage.tsx` — one owner for durable Run-review URL/presentation identity; Game Tape, Logs, Flow and Run History are controlled consumers.
- `app/pages/OverviewPage.tsx` — owner of Overview timeline URL/query state; the timeline grid only emits view-state changes.
- Schedule mutation invalidation + `ReadController` — one Schedule catalog refresh path; page components do not manually refresh the same catalog after writes.

A confirmed write always advances causality. UI context may suppress presentation, never the fact that the side effect happened.

## Where should my change go?

- New capability → owning capability-definition module + existing/new concrete runner, registered once in `application.py`.
- New Direct command/admission rule → `DirectEffectAdmission`; new physical-effect rule → `RepositoryEffectExecution`, once.
- New target scheduling rule → `FleetTargetScheduler` only if it is actually scheduling policy.
- New Fleet lifecycle rule → structural reducer/model, not a second table.
- New historical fact → Journal model/write contract.
- New derived read model → named `core/projections/` builder.
- New HTTP shape → API schema/route then regenerate OpenAPI/TypeScript.
- New frontend read/write rule → the existing read or mutation owner, not a feature-local request lifecycle.

## Fleet plans and extensions

- `core/models/fleet_plan.py` — immutable authored/resolved executable plan vocabulary: typed conditions/retry, step outputs/finalizers, Git/command/pre-commit/extension steps, and contract-versioned flat `ResolvedFleetPlan`.
- `core/models/fleet_plan_composition.py` — YAML authoring-only composition values for declared inputs/bindings and bounded matrix axes; these compile away during Python resolution.
- `core/models/fleet_plan_resources.py` — managed extension/tool identity models; no scheduler or execution ownership.
- `core/models/fleet_plan_evidence.py` — immutable aggregate execution/preview evidence: completed/skipped/failed steps, per-attempt provenance, artifact hashes/missing facts, published-output hashes, result consistency, and non-simulating preview dispositions.
- `core/models/fleet_plan_release_evidence.py` — immutable release-boundary facts for OCI publication, Kubernetes image mutation, and bounded rollout verification.
- `engine/fleet_extensions.py` — Fleet-owned immutable extension checkout + external uv-runtime resource owner for exact TOML-declared revisions.
- `integrations/fleet_plan_compilation.py` — YAML/TOML serialization and contained declarative include boundary; never executes plans.
- `services/fleet_plan_source_graph.py` — one read/link interpretation of the declared root/module source tree; caches unique source nodes and derives static runtime-acquisition facts.
- `services/fleet_plan_resolver.py` — semantic Python authority that binds one analyzed source graph into a contract-versioned `ResolvedFleetPlan`; it owns neither Git nor format parsing.
- `services/fleet_plans.py` — public validate/run/schedule/migrate facade; Shared Runs and Schedules both accept explicit repository sets, while trigger semantics remain distinct.
- `integrations/fleet_plan_run_execution.py` — one admitted plan/run orchestrator; executes only resolved typed steps and never parses declarative files.
- `integrations/fleet_plan_run.py` — one CI/CD run context: frozen run inputs, workspace lineage/publication, and attempt resource lifetime.
- [integrations/fleet_plan_step_execution.py](../../backend/src/oh_goon/integrations/fleet_plan_step_execution.py) — physical dispatch of one direct/strict step attempt; consumes resolved typed steps and admitted runtime authority.
- [integrations/_fleet_plan_step_sequence.py](../../backend/src/oh_goon/integrations/_fleet_plan_step_sequence.py) — ordered step state, conditions, failure/finalizer progression and result accumulation within one run.
- [integrations/_fleet_plan_step_retry.py](../../backend/src/oh_goon/integrations/_fleet_plan_step_retry.py) — direct/strict retry coordination using the pure decision in [core/fleet_plan_retry.py](../../backend/src/oh_goon/core/fleet_plan_retry.py).
- `integrations/fleet_plan_process_isolation.py` — one process-isolation boundary (Bubblewrap); owns kernel namespace/mount construction only.
- `integrations/content_snapshot.py` — generic immutable byte snapshot/identity primitive; deliberately not a plan/run owner.
- [integrations/_fleet_plan_step_dataflow.py](../../backend/src/oh_goon/integrations/_fleet_plan_step_dataflow.py) — typed conditions, contained declared paths and runtime output binding; [fleet_plan_step_publication.py](../../backend/src/oh_goon/integrations/fleet_plan_step_publication.py) owns successful-attempt output publication and artifact evidence.
- [integrations/_fleet_plan_step_outcome.py](../../backend/src/oh_goon/integrations/_fleet_plan_step_outcome.py) — attempt outcomes and finalizer/failure classification; [integrations/_fleet_plan_step_evidence.py](../../backend/src/oh_goon/integrations/_fleet_plan_step_evidence.py) constructs canonical step/argv evidence.
- [core/fleet_plan_git.py](../../backend/src/oh_goon/core/fleet_plan_git.py) — typed Git authoring actions to canonical argv/result contracts; [integrations/_fleet_plan_step_local_execution.py](../../backend/src/oh_goon/integrations/_fleet_plan_step_local_execution.py) executes Git, pre-commit and extension attempts with the environment from [fleet_plan_environment.py](../../backend/src/oh_goon/integrations/fleet_plan_environment.py).
- `integrations/fleet_plan_preview.py` — non-effecting repository preview over an already resolved plan; marks prior-step/output dependencies runtime-dependent rather than simulating them.
- `integrations/fleet_plans.py` — turns one complete plan execution into canonical `OperationReport` evidence.
- [integrations/gitlab_ci_goon.py](../../backend/src/oh_goon/integrations/gitlab_ci_goon.py) — one-way GitLab CI migration into one native Atomic repository Goon; [gitlab_ci_compile.py](../../backend/src/oh_goon/integrations/gitlab_ci_compile.py) owns compilation and [gitlab_ci_component.py](../../backend/src/oh_goon/integrations/gitlab_ci_component.py) owns component acquisition. Migration never creates another runtime engine.
- `engine/git.py` — small native Git boundary: generic argv execution plus one coherent porcelain-v2 repository observation. Typed Git authoring actions compile to canonical argv/result semantics before runtime, so this service has no command-specific wrapper API and never becomes a feature whitelist.

See `FLEET_PLANS.md` for why YAML, TOML and Python deliberately coexist.
