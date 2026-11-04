# Execution boundary

Oh Goon has one executable Plan/Goon interpretation and FleetRun lifecycle.
Manual, CI/CD, scheduled, agentic, ML and application-source work may select
different transport, resource and isolation policies. They reuse the same
resolution, lock verification, admission and execution owners.

## Canonical flow

```text
Plan / Goons
    ↓
validation + one resolver
    ↓
immutable ResolvedFleetPlan
    ↓
goon.lock verification
    ↓
ExecutionIdentity
    ↓
FleetPlanTarget
    ↓
Fleet admission / selected Enforcer policy
    ↓
RUN_ADMITTED + ExecutionTicket
    ↓
RunExecutionSupervisor (current thread submission)
    ↓
FleetRunExecution → ExecutionSession
    ↓
FleetTargetScheduler → Step / Attempt → harness / native tool
    ↓
FleetRunLifecycle terminalization / SQLite + Journal
    ↓
Game Tape read projection
```

`FleetPlanService.prepare_execution_target()` is the production construction boundary for executable `FleetPlanTarget` values. It resolves once, verifies `goon.lock`, verifies the invocation source and preview Plan identity when supplied, and freezes the resulting semantic identity in `ExecutionIdentity`.

A `FleetPlanTarget` transports a resolved execution definition and verified
source/lock facts toward original Fleet admission. Holding or reconstructing
that value grants no repository effects. Production code must not construct
another target from loose Plan/source/digest fields later in the lifecycle.

Immediate, queued, scheduled and placement-readmitted submissions enter this
same path. A Schedule or native session is not another executable Run owner.
Native Pi/OpenCode/Codex session semantics remain beneath Step/Attempt.

## Required Run-worker custody boundary

The [canonical execution-lifecycle requirements](development/OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md)
strengthen the physical boundary after admission:

```text
RUN_ADMITTED + ExecutionTicket
    ↓
RunExecutionSupervisor
    ↓
fresh isolated Run worker → atomic custody claim
    ↓
existing FleetRunExecution → ExecutionSession → FleetTargetScheduler
    ↓
existing Step / Attempt / harness and terminalization
```

This is the required target, not an implemented process-custody guarantee.
`RunExecutionSupervisor` currently submits the admitted callable to a thread
pool; [HUG-180](https://linear.app/hugloss/issue/HUG-180) owns replacement of that
physical submission boundary and [HUG-181](https://linear.app/hugloss/issue/HUG-181)
owns its failure/drain qualification. The worker claim transfers only live
execution custody: it cannot re-admit, re-place, re-reserve capacity, re-resolve
the Plan or reselect repository work. The old thread route must be removed at
cutover; no local, CI, Schedule, ML, agent or fallback route survives beside it.
SQLite/Journal and existing lifecycle owners retain durable Run truth.

## Identity ownership

`ExecutionIdentity.digest` is the verified overall `goon.lock` execution digest. It identifies the semantic execution definition. It does not own source provenance, policy, repository targets, queue state, worker identity, or evidence.

`FleetPlanTarget.source` remains source identity/provenance. Source identity and execution identity are deliberately separate facts: formatting or comments may change source bytes while leaving semantic execution identity unchanged.

`ResolvedExecutionReference` is the single immutable read projection of Plan ID/name, typed source provenance, and `ExecutionIdentity`. Game Tape carries it optionally because non-Plan runs exist; Enforcer receipts require it because they describe strict Plan execution. Neither projection re-resolves or copies the lock.

`FleetPlanTarget` recomputes the canonical semantic lock digest during model validation. A target reconstructed from durable data therefore rejects an `ExecutionIdentity` that cannot describe its embedded resolved Plan.

Repository/content observation identity is supplied by mandatory Hashmarks
through the public `oh_goon.integrations.hashmarks` API. OH-Goon's admitted
source and workspace/materialization identities bind the exact bytes frozen,
transported and executed; they prove different scoped facts. Neither replaces
the semantic Plan identity, nor may consumer-local paths or hashes recreate a
Hashmarks-owned repository fact. See the [Hashmarks SSOT requirements](development/OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#35-ssot-is-scoped-by-semantic-fact-scope-and-generation).

## Schedule boundary

A Schedule owns timing and repository target selection. It transports one already-verified `FleetPlanTarget` as `target`.

Each explicit repository target is admitted through the same Fleet owners and
retains its own Run. Timing and occurrence bookkeeping cannot bypass admission
or become a Schedule-specific worker lifecycle.

A Schedule must not persist duplicate sibling copies of:

- resolved Plan
- Plan source
- execution digest

Doing so would allow those fields to drift independently. Persistence, admission and occurrence creation reuse `ScheduleDefinition.target` directly.

## Queue, restart and readmission

Durable Fleet intent serializes the complete immutable operation target. Queueing or process restart therefore preserves the verified execution identity without reopening Plan/Goon files.

Executor placement readmission preserves the exact parent target and capability. A changed Plan/Goon definition requires a new preparation/admission path and a new execution identity; readmission is not re-resolution.

Cancellation, placement changes and retries are lifecycle facts around the admitted target. They do not mutate the frozen execution definition.

Producer evidence and logical membership are likewise frozen before execution.
Revalidation can reject source drift; it cannot silently replace the current
Run's selected work. New producer observations require the appropriate new
admission rather than a worker-local discovery path.

## Non-negotiable invariants

- Resolve Plan/Goon semantics once per preparation.
- Verify the lock before admission.
- Require an `ExecutionIdentity` on every executable Fleet Plan target.
- Project Plan/source/execution identity together through `ResolvedExecutionReference`.
- Construct executable Fleet Plan targets in one production boundary.
- Transport the same target through schedule/queue/admission transitions rather than reconstructing it.
- Never re-read authoring files to decide what an already-admitted run means.
- Never let a retry/readmission silently adopt newer Plan/Goon semantics.
- Keep repository targeting outside the frozen execution definition.
- Keep Enforcer policy outside execution identity.
- Keep Game Tape evidence outside execution identity.

These rules exist to make the core promise mechanically true: the execution definition that was resolved, lock-verified and admitted is the execution definition that reaches the executor.
