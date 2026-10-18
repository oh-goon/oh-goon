# Data flow

This document describes the current authoritative data flow. It is intentionally about ownership and irreversible boundaries rather than individual helper calls.

## Direct repository effect

```text
API / CLI command(operation_id)
  -> DirectEffectAdmission.run(action, target, operation_id)
  -> ResolvedCapabilityIntent + committed/unresolved identity check
  -> RuntimeSafetyService.admit_effect_cut()
  -> quarantine check + repository baseline
  -> ExecutionCoordinator.reserve_direct_with(... begin_direct_in ...)
       [lease + CLAIMED attempt commit atomically]
  -> RepositoryEffectExecution.execute_direct_claimed
  -> ExecutionSession
  -> repository-effect fence
  -> ExecutionSession.mutate(... start_effect_in ...)
       [exact owner + EFFECT_STARTED]
  -> CapabilityExecutor
       validate operation/Fleet/repository/capability/intent envelope
  -> integration/engine effect
  -> ExecutionSession.finish(...)
       [exact owner
        + Journal OperationReport
        + attempt retirement
        + lease release]
```

There is no implicit lease cleanup on exception. `DirectEffectAdmission` does not own effect-start/adapter/commit/recovery semantics; those exist only in `RepositoryEffectExecution`. A retry with the same committed Direct `operation_id` returns the canonical report and its persisted return code without executing effects again.

### Recovery ownership

Recovery writes are execution-fenced. An initial idle observation is not enough: every recovery transaction calls the execution owner again before retiring a claim, marking an effect unknown, reconciling/acknowledging an attempt, or recovery-terminalizing Fleet state. A second process therefore cannot mutate a live owner's durable lifecycle.

## Fleet admission and execution

```text
FleetRunRequest(fleet_run_id, intent, selection)
  -> structural repository scope = ALL | EXPLICIT(repository_ids)
  -> FleetRunControlPlane
  -> FleetAdmissionPlanner
       one SQLite read transaction:
         Runtime Safety Cut + LocalStateReadView
       target selection from that captured cut
  -> FleetRunLifecycle.admit
       -> ExecutionCoordinator.reserve_fleet_with
       expected durable revision CAS
       + exact Fleet lease
       + RUN_ADMITTED in the same transaction
  -> RunExecutionSupervisor
       live submission set + bounded last-failure diagnostic only
  -> FleetRunExecution
       -> FleetRunLifecycle.begin
          -> exact-owner transaction decides cancellation-vs-RUN_STARTED
       ExecutionSession.mutate(RUN_STARTED)
  -> FleetTargetScheduler
       serial/parallel scheduling only
  -> RepositoryEffectExecution.execute_fleet_target
       deterministic operation_id
       + CLAIMED attempt
       + shared fence/effect/adapter/settlement path
  -> RepositoryCompletedEvent is committed with canonical OperationReport
  -> FleetRunLifecycle.finish
       -> ExecutionSession.finish
       terminal Fleet fact + exact Fleet lease release
```

If background submission itself is rejected after admission, `FleetRunControlPlane` delegates the exact admitted ticket to `FleetRunLifecycle.finish_admitted`, which resolves `RUN_FAILED + lease release` through one `ExecutionSession.finish` transaction. It never terminalizes and then separately abandons a lease. Dead-owner cancellation is likewise one lifecycle transaction: reclaim stale lease + terminalize orphan.

## Repository-effect interruption

```text
before EFFECT_STARTED
  -> exact-owner settlement
  -> retire_unstarted_in
  -> Direct: finish/release
  -> Fleet: mutate/keep outer Fleet lease

after EFFECT_STARTED, before canonical report
  -> exact-owner settlement
  -> mark_unknown_in
  -> Direct: finish/release
  -> Fleet: mutate/keep outer Fleet lease

after canonical report
  -> canonical success remains authoritative
  -> canonical settlement is the final declared fault boundary
  -> no post-commit lifecycle/fault transition may rewrite success to unknown
```

Startup `RepositoryEffectRecovery` handles unresolved rows for both origins. Direct/Fleet is provenance, not a second recovery algorithm. Startup automatically owns `CLAIMED`, `EFFECT_STARTED`, and `OUTCOME_UNKNOWN` only; `PARTIALLY_APPLIED`/`INDETERMINATE` remain stable operator quarantine. If an explicit terminal recovery command committed but its response was lost, retry resolves the canonical Journal reconciliation/acknowledgement evidence instead of generating another terminal fact.

## Schedule definition mutation identity

```text
ScheduleDefinition(schedule_id, generation_id, record_version)
  -> PUT carries generation_id + record_version
       same committed body/version replay -> return canonical current definition
       stale version/generation -> conflict
  -> DELETE carries generation_id + record_version
       first success -> delete
       response-loss retry while absent -> success
       recreated same schedule_id with new generation_id -> old command conflicts
```

The immutable generation closes the delete/recreate ABA hole that a version counter alone cannot detect. The persisted definition itself contains one repository, one resolved Plan target, one interval, and active/paused state; it cannot carry an executable step list or multi-repository scope.

Across this flow, admitted intent, lifecycle states, Journal facts/indexes, and completed projections are immutable after publication. Mutable folds/builders exist only inside one construction step; opaque third-party JSON is isolated by defensive copying at the Journal boundary rather than shared by alias.

## Pre-admission queue and executor operations

```text
strict CI/CD start
  -> FIFO placement ticket (observable before any FleetRun exists)
       cancel wins -> EXECUTOR_ADMISSION_CANCELLED -> reservation released -> no RUN_ADMITTED
       admission wins
         -> reservation + exact executor placement
         -> queue admission barrier
         -> RUN_ADMITTED commit
         -> ticket removed

operator executor state (logical executor ID)
  active + trusted   -> eligible for new placement
  draining + trusted -> no new placement; admitted exact placement still valid
  active/draining + revoked
                     -> no new placement; admitted exact revalidation fails placement-lost
```

The queue is not a shadow run registry. Its ticket exists only until the durable admission decision and cancellation never creates a failed/cancelled FleetRun. Executor operational state is deployment-lifetime authority stored by logical executor ID and survives generation replacement; it is not part of plan YAML or the Journal event vocabulary. Drain therefore changes candidate eligibility only. Trust revocation changes both candidate eligibility and exact-bound trust, but neither state may substitute a different executor into an admitted run.

## FleetRun projection

```text
RUN_ADMITTED
  -> QueuedFleetRun
RUN_STARTED
  -> RunningFleetRun
RUN_CANCEL_REQUESTED
  -> CancellingFleetRun
RUN_COMPLETED / RUN_CANCELLED / RUN_FAILED
  -> concrete terminal state
```

`core/run_projection.py` holds a concrete `FleetRunState` at every fold step. Repository-completion events resolve their canonical `OperationReport`, validate exact run/capability/target/repository lineage, update progress, and rebuild the same structural active variant. No nullable lifecycle aggregate or transition-table shadow state exists.

## Journal write/read cut

```text
LocalDatabase transaction
  -> JournalFactStore.load_entries
       decode + verify hash chain
  -> JournalReadView.from_entries
  -> JournalIndex.build
       operations_by_id
       repository timelines + freshness
       events_by_run
       FleetRunDetail per run
       latest/active/history indexes
```

Normal reads consume this cut. Write validation consumes the same cut:

```text
Journal.commit_operation_in
  -> JournalFactStore.read_view(connection)
  -> existing operation/run events from JournalReadView
  -> validate candidate through canonical Fleet reducer
  -> append one new Journal fact
```

Fleet lifecycle fact construction follows the same rule inside `Journal`; there is no separate `FleetRunFactWriter` or raw-entry `journal_projection` authority.

## Managed repository scope mutation

```text
Repository include/exclude request
  -> capture LocalStateReadView
       managed repositories + durable_revision
  -> prove requested repository is managed in that exact cut
  -> BEGIN IMMEDIATE
  -> require_durable_revision(captured revision)
  -> require idle scope mutation
  -> mutate exclusion row
```

A concurrent location/scope change makes the command stale instead of letting a proof from one cut authorize a mutation in another. Empty revisioned write transactions do not advance `durable_revision`; the revision represents actual durable row mutation.

## Schedule start

```text
Schedule run request(schedule_run_id)
  -> ScheduleService.start
  -> existing run with same identity? replay only if schedule_id matches
  -> ScheduleAdmissionPlanner
       one SQLite read transaction:
         Runtime Safety Cut + exact ScheduleDefinition
  -> ScheduledFleetRunIntent
       current EXECUTE_PLAN CapabilitySnapshot
       exact resolved FleetPlanTarget
       ExplicitRepositoryScope(exact repository target set)
  -> ScheduleExecutionEngine.start
       expected durable revision CAS
       persist one RunningScheduleRun occurrence
       initialize one Pending repository result per target
       admit/observe targets one at a time through Fleet
       derive stable fleet_run_id(schedule_run_id, repository_id)
       preserve one canonical Fleet Run per admitted repository
```

Definition create/replace validates the exact explicit-repository-set/one-Plan boundary. A scheduled occurrence receives a new `schedule_run_id` because it is a new logical trigger occurrence; repository execution identity remains the canonical Fleet Run derived for each target, and there is no Schedule-owned per-step identity or workflow cursor.

## Schedule scheduler safety

```text
process start
  -> ScheduleScheduler.start()
  -> periodic tick
       settle/advance already-admitted Schedule runs from canonical Fleet evidence
       RuntimeSafetyService.effects_allowed()?
         no  -> do not admit new scheduled effects
         yes -> resolve due definitions and admit one bound Schedule run each
```

Scheduler lifetime is availability infrastructure, not mutation authority and not itself a trigger. A new Schedule waits one full interval from creation; later intervals are anchored to the previous terminal completion. Runtime safety at each admission closes the effect boundary.

## Read current Fleet state

```text
LocalDatabase BEGIN
  -> durable revision + locations + exclusions + verified Journal cut
  -> LocalStateReadView
  -> JournalReadView / JournalIndex
  -> named core projections
  -> FleetSnapshot/API
  -> generated TypeScript contract
  -> focused React feature
```

Do not persist `FleetSnapshot` or another projection as authority.

## Frontend write causality

```text
user command
  -> caller-stable logical run ID where command is non-idempotent
  -> MutationController.acquire
  -> HTTP mutation
  -> confirmed success
  -> CausalityClock.advance
  -> WriteCommandResult(saved)
  -> invalidate ReadResources affected by mutation
```

Changing UI context while the request is in flight does not undo a committed write. Presentation relevance is separate from write truth.

## Frontend read cancellation

```text
ReadController(scope, identity)
  -> AbortSignal
  -> httpTransport request attempt
  -> transient failure -> abortable retry backoff
  -> next attempt only if signal still active
```

Reads may be superseded by newer identity/generation. Writes may not.

## Error semantics

```text
FleetDomainError(code, ErrorKind, retryable)
  -> API translation
  -> generated ApiErrorResponse
  -> frontend ApiError(kind, retryable, status)
```

HTTP status is transport metadata; semantic `ErrorKind` remains the cross-layer authority.

## Direct path ownership

```text
Fleet command
  -> FleetRunControlPlane            identity/replay/query/submission
  -> FleetAdmissionPlanner      safety + selector + prerequisites
  -> FleetRunLifecycle          lease + Journal lifecycle
  -> FleetRunExecution          orchestration
  -> FleetTargetScheduler       deterministic target scheduling
  -> RepositoryEffectExecution physical effect lifecycle
```

`FleetService` is not on this write path. Fleet replay uses `FleetRunState.repository_scope` rather than raw-event position. Schedule ticks call `ScheduleExecutionEngine.advance(schedule_run_id)` and keep current-state authority in `ScheduleExecutionStore`; the associated Fleet Run identity is derived from that Schedule run identity. `ScheduleScheduler` only invokes periodic ticks and owns no startup-trigger memory or executable recipe state. Delete/recreate gets a new definition generation, while admitted executions retain their immutable `ScheduledFleetRunIntent`. Included-target eligibility consumes the canonical repository catalog. Dependency discovery consumes the typed observation projection and never reopens every repository during a fleet query; discovery/migration share `RepositoryDiscoveryService.git_roots` only where live filesystem discovery is actually required.

## Fleet plan authoring flow

```text
React Plan Composer
  -> GET source/catalog/inspect
  -> POST analyze/transform/refactor (read-only unsaved projections)
  -> saved Plan service / FleetPlanService
       -> YAML source + SHA-256 revision
       -> source-preserving bounded transform -> candidate bytes only
       -> structured move/delete -> exact source blocks + dependency recheck
       -> ordered workspace input/output refactor set -> per-op/final patch evidence + candidate files
       -> static module diagnostics
       -> shared FleetPlanResolver -> exact ResolvedFleetPlan preview

Workspace refactor proposal
  -> ordered operations execute against one in-memory candidate workspace
  -> bind complete base YAML revision manifest + exact candidate-workspace revision
  -> optional canonical portable artifact v3 + content-bound review receipt
  -> import verifies operations/manifests/proposal/patch/artifact identity
  -> workspace verification reports base / exact candidate / diverged
  -> hashes are content identity, not signatures
  -> exact candidate is review evidence only; no write authority
  -> no sequential multi-file "atomic" save (not crash-safe)

Save (one YAML source)
  -> expected YAML revision + candidate source
  -> parse + validate under explicit workspace containment root
  -> revision compare-and-swap
  -> atomic YAML replace
```

Browser Plan authoring has one saved-Plan owner under **Plans** and the `/league/shared-plans` boundaries. Shared Runs and Schedules consume saved Plans; they do not author Plan source. Internal Plan-path commands resolve through the same `FleetPlanService`/`FleetPlanResolver` execution semantics without exposing a second `/plans/*` HTTP workspace. YAML remains source truth for path-based tooling, while saved Plan documents remain the browser authoring truth until backend resolution creates the immutable execution target.

## Fleet plan resolution and scheduling flow

```text
TOML extension source + exact revision
              |
       explicit plan prepare
              v
 Fleet-owned immutable checkout
 + prepared external uv environment
              |
YAML plan/templates + TOML allowlist
              |
              v
 FleetPlanResolver (Python)
      flatten + bind only
              |
              v
       ResolvedFleetPlan
              |
       +------+------+
       |             |
  immediate run   ScheduleDefinition
       |             |
       +------v------+
          FleetRunControlPlane
              |
     RepositoryEffectExecution
              |
       read-only preflight
              |
         EFFECT_STARTED
              |
        FleetPlanRunner
```

YAML/TOML are read only before admission. Declarative YAML includes are contained beneath the root plan tree, bind only declared typed scalar inputs, evaluate small compile-time include_when selectors, rewrite public module outputs to concrete leaf outputs, statically expand bounded template matrices, namespace local runtime dependencies, and flatten completely before admission; managed extension source/revision/lock identity is embedded without persisting Fleet-local checkout paths. Explicit `plan prepare` imports only extensions referenced by the contained static composition tree, prepares their locked uv environments, and prepares any built-in runtime referenced by that tree; merely supplying a catalog does not authorize acquisition. Execution uses those prepared environments without synchronization. The durable target contains the exact contract-versioned flat resolved plan, including typed conditions, retry policy, declared artifact paths, public step-output declarations/references, and a trailing finalizer suffix. Schedule triggering and manual/Shared execution therefore share the same resolved Plan intent and Fleet execution path. Conditions that evaluate false create explicit skipped facts; retryable known failures preserve per-attempt repository before/after provenance; artifact evidence stores only hash/size or missing state; published output values remain process-local while canonical evidence stores only name/hash/size and declarative argv markers; finalizers run only on normal/known-failure control flow. Raw stdout/stderr remain transient and no matrix scheduler, module runtime, output store, step-level checkpoint, plugin registry, or recovery authority exists. `plan preview` observes path conditions without executing commands and marks prior-step-status/output decisions runtime-dependent. Git steps may use common typed actions or arbitrary native Git argv; both resolve to one `GitService` execution envelope with explicit timeout-or-null/environment/working-directory/failure policy. Every executable step declares a positive process deadline or explicit `null`; runtime does not invent one.
