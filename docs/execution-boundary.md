# Execution boundary

Oh Goon has one executable Plan/Goon interpretation path. Execution families may differ in lifecycle and transport, but they must not own separate resolution, lock verification, admission, or target reconstruction semantics.

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
admission / Enforcer
    ↓
immediate | queued | scheduled | readmitted
    ↓
executor
    ↓
Game Tape
```

`FleetPlanService.prepare_execution_target()` is the production construction boundary for executable `FleetPlanTarget` values. It resolves once, verifies `goon.lock`, verifies the invocation source and preview Plan identity when supplied, and freezes the resulting semantic identity in `ExecutionIdentity`.

A `FleetPlanTarget` is therefore not an authoring request. It is already-resolved execution authority transported toward admission and execution. Production code must not construct another `FleetPlanTarget` from loose Plan/source/digest fields later in the lifecycle.

## Identity ownership

`ExecutionIdentity.digest` is the verified overall `goon.lock` execution digest. It identifies the semantic execution definition. It does not own source provenance, policy, repository targets, queue state, worker identity, or evidence.

`FleetPlanTarget.source` remains source identity/provenance. Source identity and execution identity are deliberately separate facts: formatting or comments may change source bytes while leaving semantic execution identity unchanged.

`ResolvedExecutionReference` is the single immutable read projection of Plan ID/name, typed source provenance, and `ExecutionIdentity`. Game Tape carries it optionally because non-Plan runs exist; Enforcer receipts require it because they describe strict Plan execution. Neither projection re-resolves or copies the lock.

`FleetPlanTarget` recomputes the canonical semantic lock digest during model validation. A target reconstructed from durable data therefore rejects an `ExecutionIdentity` that cannot describe its embedded resolved Plan.

## Schedule boundary

A Schedule owns timing and repository target selection. It transports one already-verified `FleetPlanTarget` as `target`.

A Schedule must not persist duplicate sibling copies of:

- resolved Plan
- Plan source
- execution digest

Doing so would allow those fields to drift independently. Persistence, admission and occurrence creation reuse `ScheduleDefinition.target` directly.

## Queue, restart and readmission

Durable Fleet intent serializes the complete immutable operation target. Queueing or process restart therefore preserves the verified execution identity without reopening Plan/Goon files.

Executor placement readmission preserves the exact parent target and capability. A changed Plan/Goon definition requires a new preparation/admission path and a new execution identity; readmission is not re-resolution.

Cancellation, placement changes and retries are lifecycle facts around the admitted target. They do not mutate the frozen execution definition.

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
