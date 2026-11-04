# OH-Goon Canonical Execution Lifecycle Requirements

**Status:** Normative target requirements; implementation qualification remains separate.

**Scope:** FleetRun admission, isolated Run-worker custody, execution, drain,
cancellation, recovery, containment and terminalization.

**Source:** Consolidates `OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_HASHMARKS_SSOT_AND_OBSERVABILITY_REQUIREMENTS_REV2.md` (2026-10-31 REV2) for
[HUG-182](https://linear.app/hugloss/issue/HUG-182). Original source SHA-256:
`53d1c00e1154e76b09c2879cb9e2bf51a8753e5099b57f74155af9aac4f3d392`.

**Process workers strengthen the existing canonical OH-Goon execution lifecycle.
They never become another execution path. No local, CI, scheduled, ML, agentic,
compatibility or fallback path may execute a FleetRun outside that lifecycle.**

**Resolve once → transport/admit once → execute once → report once.**

This is the execution-lifecycle normative owner. The separate
[mandatory Hashmarks SSOT requirements](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md) own repository
semantics, the public integration API, producer capability/migration contracts
and their acceptance. Missing producer precision fails closed or causes
conservative OH-Goon policy; it never grants a local semantic replacement.

These requirements describe the required end state, not current worker support.
At the imported implementation baseline, `RunExecutionSupervisor` still submits
callables to a thread pool and `FleetRunControlPlane` invokes the existing
`FleetRunExecution` through it. [HUG-180](https://linear.app/hugloss/issue/HUG-180)
owns the custody cutover; [HUG-181](https://linear.app/hugloss/issue/HUG-181) owns
failure/drain qualification. Documentation alone does not establish either.
The source's reviewed OH-Goon `6483cf4b2699ec852f540e88b8ccb3517d5b285c`
and Hashmarks `6cb937974a8641333ceab64991dda33276f07f97` checkpoints are historical
review provenance. Package/release authority remains the native dependency lock
and loaded producer identity.

Current owners and existing executable checks are discoverable through
[Architecture](../ARCHITECTURE.md), [current decisions](ARCHITECTURE_DECISIONS.md),
[Agent runtime owners](AGENT_RUNTIME_OWNERS.md),
[`RunExecutionSupervisor`](../../backend/src/oh_goon/runtime/execution/run_supervisor.py),
[`FleetRunControlPlane`](../../backend/src/oh_goon/services/fleet_run_control_plane.py),
[`ExecutionCoordinator`](../../backend/src/oh_goon/runtime/execution/coordinator.py),
the [X0 execution contract](../../backend/src/oh_goon/repository_tooling/architecture/x0_single_execution_contract.py),
the [existing supervisor tests](../../backend/tests/unit/runtime/test_run_supervisor.py)
and [execution-lease tests](../../backend/tests/unit/runtime/test_execution_lease.py).
They protect current contracts; the specific claimed-worker guard required by
§85 must be added and proven with HUG-180 rather than inferred from their names.

The original section numbers and ELR identifiers are retained for traceability.
Moved producer sections are mapped in the [source crosswalk](#source-section-crosswalk).
References to DAG execution in the source are normalized to the existing
Plan/Goon/Step model; this work adds no graph or scheduling semantics.

# 1. Executive requirement

OH-Goon SHALL strengthen its existing Fleet execution lifecycle by introducing a stronger operating-system process boundary between the long-lived OH-Goon control/runtime process and each actively executing `FleetRun`.

This work SHALL be described as:

> **strengthening the existing execution lifecycle**

It SHALL NOT be described, designed, or implemented as:

> **implement LiveKit architecture**

LiveKit is architectural inspiration only for process isolation, explicit custody, dispatch boundaries, and drain semantics.

The single most important requirement is:

> **Process workers MUST NOT become a second OH-Goon execution path.**

The current semantic execution path SHALL remain authoritative.

Current conceptual path:

```text
Plan / operation ingress
        ↓
FleetRunControlPlane
        ↓
FleetRunLifecycle.admit()
        ↓
FleetExecutorPool placement/capacity
        ↓
RUN_ADMITTED + ExecutionTicket
        ↓
RunExecutionSupervisor
        ↓
FleetRunExecution.execute()
        ↓
ExecutionSession
        ↓
FleetTargetScheduler
        ↓
Step / Attempt
        ↓
Harness / native tool
        ↓
existing terminalization
```

The strengthened path SHALL become:

```text
Plan / operation ingress
        ↓
FleetRunControlPlane
        ↓
FleetRunLifecycle.admit()
        ↓
FleetExecutorPool placement/capacity
        ↓
RUN_ADMITTED + ExecutionTicket
        ↓
RunExecutionSupervisor
        ↓
isolated Run worker process
        ↓
atomic ExecutionTicket → worker ownership claim
        ↓
FleetRunExecution.execute()
        ↓
ExecutionSession
        ↓
FleetTargetScheduler
        ↓
Step / Attempt
        ↓
Pi / OpenCode / Codex / other execution backend
        ↓
existing terminalization
```

The new worker process is a physical execution boundary.

It is NOT:

- a second workflow engine;
- a second Run model;
- a second scheduler;
- a second execution authority;
- a new durable `Job` abstraction;
- an alternate local execution route;
- an agent-specific execution route;
- a CI-specific execution route.

---

# 2. Mandatory repository-intelligence dependency seam

Consume producer-qualified repository facts only through the public
`oh_goon.integrations.hashmarks` API. The
[mandatory Hashmarks requirements](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#1-purpose)
are normative for that producer/consumer boundary. This document specifies
only its execution seam: freeze admitted facts, transport their identities,
execute the exact admitted work, and retain result authority in OH-Goon.

---

# 3. Execution ownership boundary

- Run admission;
- Run identity;
- execution ownership;
- worker process lifecycle;
- worker claim;
- placement;
- executor capacity reservation;
- priority;
- physical batching;
- retry;
- timeout;
- ordered Goon/Step execution;
- native harness execution;
- Pi/OpenCode/Codex invocation;
- cancellation;
- external-effect settlement;
- recovery;
- terminal result;
- certification;
- execution receipts;
- execution-result authority.

Repository-intelligence ownership is specified once in
[Hashmarks §§3 and 7A](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#3-product-ownership-invariant).
Repository/content observation identity and execution-custody/materialization
identity may coexist when they prove different semantic facts, scopes or
generations; see [Hashmarks §3.5](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#35-ssot-is-scoped-by-semantic-fact-scope-and-generation).

---

# 4. Existing OH-Goon components that SHALL be strengthened, not replaced

The current repository already contains the primary owners needed for this design.

The implementation SHALL strengthen and consolidate these owners rather than creating parallel replacements:

- `FleetRunControlPlane`;
- `FleetRunLifecycle`;
- `FleetExecutorPool`;
- `ExecutorPlacementAdmission`;
- `FleetExecutorCapacityReservations`;
- `ExecutionCoordinator`;
- `ExecutionTicket`;
- `ExecutionSession`;
- `RunExecutionSupervisor`;
- `FleetRunExecution`;
- `FleetTargetScheduler`;
- existing effect-attempt ownership;
- existing recovery ownership;
- existing process-isolation backends;
- existing harness contracts;
- existing Hashmarks integration package.

No new abstraction may be introduced merely because another system uses different terminology.

---

# 5. Terminology

## 5.1 FleetRun

The durable OH-Goon unit of work.

Owned by the OH-Goon lifecycle and represented through canonical SQLite/Journal state.

A `FleetRun` may outlive:

- the API process;
- a worker process;
- a harness session;
- a terminal connection;
- a deployment.

## 5.2 ExecutionTicket

The admitted capability authorizing an already-created Run to proceed toward execution.

Its existing role SHALL be retained and strengthened.

It SHALL NOT be replaced by a second `JobTicket`, `DispatchTicket`, or equivalent durable authority.

## 5.3 Run worker

An operating-system process executing one admitted `FleetRun`.

The Run worker:

- owns live execution custody only after successful claim;
- executes the existing `FleetRunExecution`;
- opens its own valid runtime/database resources;
- disappears after execution custody ends.

The Run worker is not a durable workflow entity.

## 5.4 ExecutionSession

The existing live execution owner backed by canonical execution-lease authority.

After this change, the executable `ExecutionSession` SHALL correspond to the process that actually performs the Run.

## 5.5 Harness session

A temporary native-tool session owned by the selected harness, such as:

- Pi;
- OpenCode;
- Codex.

A harness session remains nested below Step/Attempt execution.

It MUST NOT own the `FleetRun`.

## 5.6 Executor

The existing OH-Goon placement/capacity concept.

An executor decides where and under what admitted capacity/isolation authority work may execute.

An executor is not the same semantic object as a Run worker.

---

# 6. Required ownership hierarchy

The target hierarchy SHALL be:

```text
FleetRun
│
│ durable authority:
│ SQLite / Journal / OH-Goon lifecycle
│
└── ExecutionSession
    │
    │ current live execution custody:
    │ one Run worker process
    │
    ├── repository execution
    │
    ├── Step
    │    │
    │    └── Attempt
    │         │
    │         └── HarnessSession
    │              ├── Pi
    │              ├── OpenCode
    │              └── Codex
    │
    └── Step ...
```

Forbidden authority inversion:

```text
HarnessSession
      ↓
becomes Run authority
```

Forbidden duplicate authority:

```text
Run worker local state
      ↓
becomes another durable Run truth
```

The containment relationship SHALL be explicit:

```text
OH-Goon owns Run
Run worker owns only live execution custody
Step/Attempt owns bounded work
Harness owns only native session behavior
```

---

# 7. One execution path

There SHALL be exactly one semantic Fleet execution path.

Local execution, strict CI/CD execution, scheduled execution, agentic execution, ML/training execution, and external-project execution SHALL converge on the same Run lifecycle.

The implementation SHALL NOT create permanent semantic paths such as:

```text
thread executor
process executor
CI executor
ML executor
agent executor
local executor
remote executor
```

Different workloads may configure:

- resource envelopes;
- execution backends;
- network permissions;
- harnesses;
- time budgets;
- isolation profiles;

but execution SHALL converge on the same canonical Run lifecycle.

---

# 8. No compatibility execution mode

The final merged architecture SHALL NOT retain a permanent configuration such as:

```text
USE_PROCESS_WORKER=true
legacy_thread_execution=true
execution_mode=thread|process
```

A temporary branch-local migration mechanism MAY exist during construction.

The final production architecture SHALL expose one canonical path.

No second path may survive solely as rollback convenience.

---

# 9. No silent fallback

If worker spawn or worker claim fails, OH-Goon SHALL settle that failure through the existing lifecycle.

It SHALL NOT perform:

```text
worker failed
    ↓
execute in parent instead
```

Such fallback would create two execution authorities.

Failure to establish the strengthened worker boundary MUST fail clearly.

---

# 10. Existing admission authority SHALL remain canonical

Strict Fleet admission currently combines critical facts through the existing admission boundary.

The process-worker work SHALL NOT split those facts into independently authoritative commits.

The implementation SHALL preserve the current authoritative relationships between:

- executor placement;
- capacity reservation;
- execution lease/ticket creation;
- `RUN_ADMITTED`;
- repository/Plan authority;
- scheduling receipt.

Worker creation occurs after admission.

Worker creation does not replace admission.

---

# 11. Canonical lifecycle

The normal lifecycle SHALL be:

```text
1. Resolve/freeze requested work
2. Resolve repository/Plan authority
3. Resolve Hashmarks repository-intelligence evidence when required
4. Freeze logical selected work
5. Perform executor placement when required
6. Reserve required executor capacity
7. Create durable Run admission
8. Create ExecutionTicket
9. Submit admitted Run to RunExecutionSupervisor
10. Spawn isolated Run worker
11. Worker proves its process identity
12. Worker atomically claims admitted execution custody
13. Worker enters existing FleetRunExecution
14. Existing lifecycle activates ExecutionSession inside that execution owner
15. Existing target/step/attempt machinery executes
16. Existing terminalization commits Run outcome
17. Execution lease is released
18. Capacity reservation is released
19. Worker exits
```

No Step or external execution effect may occur before worker claim succeeds.

---

# 12. ExecutionTicket → worker ownership claim

This is the most important new lifecycle boundary.

## ELR-001 — Claim is mandatory

Every isolated Run worker SHALL claim the exact admitted execution authority before executing Run work.

The worker SHALL NOT execute effectful Run logic without a successful claim.

## ELR-002 — Claim is atomic

The claim SHALL be committed using the existing SQLite execution-ownership authority.

It SHALL compare-and-swap the exact expected predecessor custody.

The claim MUST verify at least:

- exact Run identity;
- exact admitted ticket/lease identity;
- expected predecessor custody;
- expected lifecycle phase;
- claimant PID;
- claimant process-birth identity;
- canonical SQLite authority.

Two workers racing for the same admitted Run MUST result in exactly one successful claimant.

## ELR-003 — Claim transfers custody only

Claim SHALL NOT:

- create a second Run;
- re-place the Run;
- re-reserve capacity;
- change priority;
- change Plan;
- change repositories;
- change executor;
- change isolation backend;
- reselect Hashmarks verification work;
- re-resolve repository intelligence.

Claim transfers execution custody.

It does not re-admit work.

## ELR-004 — Claim SHOULD supersede the predecessor executable capability

After successful worker claim, previous admission-side executable capability SHOULD no longer authorize normal execution mutation.

Stale parent, stale child, or replayed ticket operations MUST fail closed.

## ELR-005 — Claim is single-use

The same admitted ticket SHALL NOT successfully create multiple executable worker sessions.

Replay MUST fail.

## ELR-006 — Claim failure is effect-free

If worker claim fails, the worker MUST exit without:

- `RUN_STARTED`;
- Step execution;
- harness invocation;
- repository mutation;
- external effects.

---

# 13. Submission ownership before worker claim

There is a real lifecycle interval:

```text
RUN_ADMITTED
     ↓
worker not yet claimed
```

This state SHALL have deterministic ownership.

Before worker claim:

- the existing admitted/control lifecycle remains responsible for submission failure;
- the worker has no effect authority;
- capacity remains reserved;
- the Run remains admitted;
- the Run MUST NOT be silently abandoned.

If process creation fails before claim, the existing lifecycle SHALL record the appropriate submission failure and release lease/capacity through the normal terminalization contract.

This SHALL preserve the semantics currently owned by the existing `_submit()` failure path.

---

# 14. Run worker requirements

## ELR-010 — One fresh Run worker per active Run

Phase 1 SHALL use one fresh operating-system process for one executing `FleetRun`.

A worker process SHALL NOT execute a second unrelated Run after terminalization.

## ELR-011 — No prewarmed reusable worker pool initially

Worker reuse/prewarming is explicitly out of scope.

This avoids cross-Run leakage of:

- module globals;
- file descriptors;
- native state;
- credentials;
- model/session state;
- filesystem handles;
- database handles;
- caches;
- inherited environment mutations.

A future optimization may introduce prewarming only after separate evidence proves it necessary and safe.

## ELR-012 — Worker owns no ingress

The Run worker SHALL NOT expose an alternative:

- HTTP Run admission endpoint;
- CLI Run admission endpoint;
- schedule admission endpoint;
- Plan-resolution endpoint.

It receives already-admitted work only.

## ELR-013 — Worker bootstrap may be specialized but not semantically independent

A dedicated internal worker startup profile MAY exist.

It SHALL only construct runtime components necessary to execute an already-admitted Run.

It SHALL NOT:

- run global scheduling;
- admit new arbitrary work;
- perform unrelated startup recovery;
- become another application server;
- re-resolve the Run.

## ELR-014 — No inherited SQLite transaction authority

The worker SHALL establish its own valid database/session resources.

An active SQLite connection or transaction from the parent SHALL NOT be treated as transferable execution authority.

The launch model SHALL prevent inherited stale database handles from becoming authoritative worker state.

---

# 15. RunExecutionSupervisor

`RunExecutionSupervisor` SHALL remain the process-local supervisor owner.

Its responsibility SHALL evolve from:

```text
submit callable to thread
```

to conceptually:

```text
submit admitted Run
       ↓
spawn worker
       ↓
observe worker process
       ↓
coordinate drain / cancellation / containment
```

without acquiring durable workflow authority.

## ELR-020 — Supervisor state is not durable truth

The supervisor may know:

- Run ID;
- worker PID;
- process handle;
- local exit status;
- local process-group identity.

The Journal/SQLite lifecycle remains authoritative for:

- Run status;
- completion;
- failure;
- cancellation;
- recovery eligibility;
- terminal result.

## ELR-021 — Process exit is evidence, not terminal authority

Worker exit code alone SHALL NOT determine the Run's terminal state.

For example:

```text
worker exit 0
but no durable terminal event
```

does not mean the Run completed.

Similarly:

```text
worker exits non-zero
after terminal completion was durably committed
```

does not rewrite the completed Run.

Durable lifecycle state wins.

---

# 16. Process isolation requirement

A worker crash SHALL NOT terminate the normal OH-Goon control/runtime process.

Qualification SHALL cover at least:

- unhandled worker exception;
- worker fatal signal;
- simulated process abort;
- native child failure;
- forced worker termination.

Where technically feasible, qualification SHOULD include at least one real abnormal process exit rather than only Python exceptions.

---

# 17. Parent death behavior

Required failure cut:

```text
worker successfully claims Run
        ↓
control/runtime parent dies
        ↓
worker remains alive
```

A replacement OH-Goon process MUST NOT steal or recreate execution merely because the previous parent disappeared.

It SHALL inspect canonical lease/process identity.

If the worker remains the proven live owner, the replacement control plane SHALL treat the Run as live and externally owned by the existing worker custody.

---

# 18. Worker death semantics

Worker death SHALL NOT imply:

```text
Run absent
```

and SHALL NOT imply:

```text
safe to retry
```

Required sequence:

```text
worker death observed
        ↓
prove predecessor no longer owns execution
        ↓
obtain explicit recovery custody
        ↓
reconcile existing effects
        ↓
classify known / unknown outcome
        ↓
retry only when existing retry/recovery contract permits it
```

---

# 19. No blind redispatch

OH-Goon SHALL NOT automatically redispatch a crashed Run as though no side effect occurred.

Forbidden:

```text
worker disappears
      ↓
start another worker
      ↓
rerun Run from beginning
```

without recovery/reconciliation.

This is unsafe for effects including:

- deployments;
- Git mutation;
- publication;
- external API writes;
- infrastructure mutation;
- agent edits;
- artifact publication;
- other non-idempotent effects.

---

# 20. Unknown remains unknown

Failure to observe an effect is not proof that the effect did not happen.

If worker death occurs across an external-effect boundary and no trustworthy settlement fact exists, the system MUST preserve uncertainty until reconciliation establishes more.

No worker restart may convert:

```text
unknown
```

into:

```text
not executed
```

by assumption.

---

# 21. Recovery requirements

## ELR-030 — Existing recovery authority remains canonical

Existing execution/recovery ownership SHALL be extended rather than replaced.

There SHALL NOT be a worker-specific recovery database, lock file, or alternate durable owner.

## ELR-031 — Live worker must not be stolen from

Recovery MUST prove predecessor death before transferring execution/recovery ownership.

A live worker SHALL retain custody.

## ELR-032 — PID alone is insufficient when stronger identity exists

Existing conservative PID/process-birth identity semantics SHALL be preserved or strengthened.

PID reuse must not create dual execution authority.

## ELR-033 — Recovery does not rewrite original history

Recovery SHALL operate through explicit recovery custody.

It SHALL NOT relabel the original execution path as though no interruption occurred.

---

# 22. Worker death before RUN_STARTED

Required failure cut:

```text
RUN_ADMITTED
        ↓
worker claim succeeds
        ↓
worker crashes
        ↓
RUN_STARTED never committed
```

The system MUST:

- preserve the original Run;
- prove worker death;
- transfer only explicit recovery authority;
- reconcile the interruption through the existing lifecycle;
- never assume effects occurred;
- never assume effects did not occur except where canonical transition/effect evidence proves that no effect boundary could have been crossed.

---

# 23. Worker death after RUN_STARTED

Required failure cut:

```text
RUN_STARTED
     ↓
some work
     ↓
worker dies
```

Recovery SHALL use existing:

- Run evidence;
- Step evidence;
- Attempt evidence;
- effect-attempt evidence;
- external-effect settlement evidence.

No whole-Run automatic replay is allowed.

---

# 24. Worker death around external effects

This failure cut is mandatory to test:

```text
external operation succeeds
        ↓
worker dies
        ↓
success settlement not durably committed
```

The architecture MUST NOT classify this as safe retry absence.

Existing effect reconciliation SHALL decide the recoverable meaning.

---

# 25. Runtime drain

The runtime SHALL support drain-first shutdown semantics for normal controlled termination.

Target lifecycle:

```text
RUNNING
   ↓
QUIESCING
   ↓
no new Run admission/submission
   ↓
existing workers continue
   ↓
all workers finish
   ↓
CLOSED
```

A bounded timeout MAY escalate this lifecycle.

---

# 26. Canonical shutdown sequence

Normal shutdown SHALL be:

```text
shutdown requested
        ↓
Runtime → QUIESCING
        ↓
stop schedule production/admission
        ↓
stop new Run submission
        ↓
allow active workers to continue
        ↓
drain deadline reached?
      /      \
    no        yes
    │          │
finish       request durable cancellation
               │
               ↓
          cancellation grace
               │
               ↓
         worker still alive?
            /      \
           no      yes
           │        │
           ▼        ▼
       reconcile   terminate worker/process group
                       │
                       ▼
                   reconcile
```

A normal rolling deploy/restart SHALL NOT automatically mean immediate cancellation of a healthy active Run when drain semantics apply.

---

# 27. Runtime drain vs executor drain

These are distinct facts.

## Runtime drain

```text
this OH-Goon application instance stops accepting new work
```

## Executor drain

```text
this configured executor receives no new placement
```

They may interact.

They SHALL NOT share one ambiguous generic `draining` state.

---

# 28. Executor drain semantics

Once an executor's authoritative admission mode is `DRAINING`, new placement SHALL not commit against it.

An already-admitted Run SHALL remain bound.

Executor drain SHALL NOT automatically relocate existing admitted work.

---

# 29. Cancellation

Cancellation SHALL remain a durable Run intent.

It SHALL NOT become:

```text
kill(pid)
```

as the business contract.

Required semantics:

```text
durable cancellation requested
        ↓
worker observes cancellation
        ↓
existing operation cancellation semantics run
        ↓
effects settle/reconcile
        ↓
terminal cancellation committed
```

Process termination is final containment.

It is not proof of successful cancellation or rollback.

---

# 30. Forced termination

If a worker does not stop after durable cancellation and the configured grace period, the supervisor MAY forcibly terminate the worker.

Forced termination SHALL:

1. preserve existing cancellation intent;
2. terminate the worker-owned process boundary;
3. preserve or trigger reconciliation where required;
4. avoid claiming external effects were undone merely because the local process died;
5. leave unresolved state recoverable when terminal truth cannot be established.

---

# 31. Process-tree ownership

The Run worker SHALL have a clearly defined process-tree lifetime.

Processes launched as part of the Run MUST either:

1. be explicitly owned by an existing execution/isolation backend; or
2. belong to a worker-owned process group/lifetime boundary.

The Run worker supervisor SHALL NOT create competing cleanup ownership with existing:

- command runner;
- process-isolation backend;
- sandbox;
- container;
- Kubernetes;
- OpenSandbox;
- native harness lifecycle.

The Run worker owns containment of the Run.

The narrower effect owner remains responsible for interpreting that effect's outcome.

---

# 32. Cleanup failure

Cleanup failure MUST NOT erase the primary Run outcome.

Examples:

```text
work failed
cleanup also failed
```

must preserve the work failure and separately expose cleanup failure.

Likewise:

```text
work succeeded
cleanup cannot be proven
```

must not overclaim a clean completion when the existing contract requires proven quiescence/cleanup.

---

# 33. Concurrency scope

Phase 1 SHALL preserve the current conservative Run-level concurrency posture.

Introducing process isolation SHALL NOT simultaneously introduce unrestricted multi-Run concurrency.

The initial strengthened model SHOULD remain conceptually:

```text
one admitted active FleetRun
        ↓
one isolated Run worker
        ↓
existing admitted target/step parallelism
```

Expanding simultaneous durable Runs is a separate architectural decision.

---

# 34. No generic ProcessPoolExecutor substitution

Replacing:

```python
ThreadPoolExecutor(...)
```

with:

```python
ProcessPoolExecutor(...)
```

does not satisfy this requirement unless the explicit durable ownership model also exists.

The required architecture is:

```text
Run identity
        ↓
explicit Run worker identity
        ↓
atomic ownership claim
        ↓
existing FleetRunExecution
        ↓
terminal settlement
```

A reusable process pool that hides worker identity or reuse is insufficient.

---

# 35. No prewarming initially

Prewarming or process reuse SHALL NOT be introduced in the first implementation.

Clean worker creation is a correctness, isolation, and security property.

Measure process startup overhead before considering reuse.

---

# 36. Database authority

The worker SHALL use the same canonical OH-Goon SQLite authority.

There SHALL NOT be:

- per-worker shadow Run databases;
- worker-local terminal-status files acting as truth;
- mirrored execution-status stores;
- JSON marker files used as durable ownership truth.

Worker-local transport files may exist only with explicitly non-authoritative roles.

---

# 37. Worker IPC

Any parent/worker IPC channel SHALL have a bounded role.

IPC loss after worker claim SHALL NOT rewrite durable Run state.

Durable truth MUST NOT depend on continued parent IPC connectivity.

---

# 38. Worker-claim capability security

ExecutionTicket/claim material is privileged capability material.

It MUST NOT appear in:

- ordinary logs;
- diagnostics;
- Game Tape;
- API responses;
- exception strings;
- process command-line arguments where avoidable.

Transport SHOULD use a bounded local mechanism with explicit ownership, such as:

- protected inherited descriptor;
- one-time local IPC channel;
- equivalent OS-native protected transport.

The exact mechanism is implementation-owned.

---

# 39. Worker environment authority

The worker SHALL receive only environment/configuration required for the already-admitted execution.

Worker isolation SHALL NOT broaden:

- filesystem authority;
- native configuration visibility;
- authentication visibility;
- secret authority;
- network authority.

Existing `ExecutionAssignment`, isolation, and harness contracts remain authoritative.

---

# 40. Harness ownership

Pi, OpenCode, Codex, and future harnesses SHALL continue owning their native session semantics.

OH-Goon SHALL own the contract around them.

No worker-lifecycle implementation may introduce scattered provider branches in scheduling, Run ownership, or recovery.

Harness-specific behavior remains behind the harness/native-agent boundary.

---

# 41. Native session authority prohibition

Native concepts such as:

- session;
- resume;
- continue;
- fork;
- daemon;
- server;

MUST remain subordinate to OH-Goon's admitted harness contract.

A native session ID is not a `FleetRun` ID.

A native session cannot become Run recovery authority.

---

# 42. No agent-specific worker path

Agent execution MUST NOT gain an independent `AgentWorker` lifecycle beside the Run worker.

Required:

```text
Run worker
    ↓
Step / Attempt
    ↓
Harness adapter
    ↓
Pi / OpenCode / Codex
```

---

# 43. No CI-specific worker path

CI/CD SHALL use the same Run worker lifecycle.

There SHALL NOT be a special CI worker path with independent Run semantics.

---

# 44. No ML-specific worker path

ML/training workflows SHALL use:

```text
FleetRun
→ Run worker
→ existing Goon / Step execution
```

They may request different resources.

They do not gain a new execution lifecycle.

---

# 45. No scheduled-work worker path

The scheduler SHALL produce/admit work through the normal Run lifecycle.

It SHALL NOT directly spawn a special worker path.

---

# 46. No local-development bypass

Ordinary local Runs SHALL use the canonical worker lifecycle.

If local mode uses direct parent execution while strict mode uses process workers, the architecture has two execution paths.

That is forbidden.

---

# 47. Executor capacity authority

OH-Goon SHALL retain its explicit resource/capacity model.

Authoritative placement may continue to consider:

- CPU capacity;
- memory capacity;
- process limits;
- admitted parallelism;
- executor generation;
- trust state;
- isolation backend;
- active capacity reservations.

A generic floating worker-load score SHALL NOT replace this authority.

Live-load observations MAY later be used as scheduling hints.

---

# 48. Worker health

Heartbeat or worker-health mechanisms MAY exist for diagnostics or future remote workers.

Heartbeat freshness SHALL NOT establish Run ownership.

Heartbeat expiry SHALL NOT automatically transfer a Run.

Ownership transfer still requires canonical lease/recovery authority.

---

# 49. Durable terminalization

Run completion, failure, and cancellation SHALL continue using the existing terminalization owner.

The worker SHALL NOT independently:

- delete its capacity reservation;
- delete its execution lease;
- write a terminal marker file;
- write an alternative terminal status.

Terminal Run state, lease release, and capacity release SHALL preserve their canonical ordered/atomic contract.

---

# 50. Terminal-before-exit

Preferred ordering:

```text
durable terminalization
       ↓
lease/capacity settlement
       ↓
worker exits
```

If the worker fails after terminal commit but before clean exit, the durable terminal result remains authoritative.

The abnormal exit becomes diagnostic/containment evidence only.

---

# 51. Observability

The strengthened lifecycle SHALL emit sufficient diagnostic information to understand ownership without creating a second authority.

Useful observations include:

- worker spawn requested;
- worker process created;
- worker claim succeeded;
- worker claim refused;
- worker started Run execution;
- worker exited;
- worker killed;
- runtime entered quiescing;
- drain deadline reached;
- cancellation escalation started;
- recovery custody transferred.

Where available, observations SHOULD correlate:

- `fleet_run_id`;
- worker PID;
- worker process identity;
- executor identity;
- safe claim/lease generation identifier;
- timestamp;
- exit reason;
- diagnostic-vs-authoritative classification.

Bearer lease tokens MUST NOT be exposed.

---

# 52. Observability is not state authority

Logs, Game Tape, diagnostics, process lists, and worker exit codes SHALL NOT become alternative Run status authorities.

They may project or explain canonical state.

SQLite/Journal remains authoritative.

---

# 53. Producer findings and capability requirements

Original REV2 §§53–74 are consolidated in the
[Hashmarks migration and baseline findings](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#8-required-oh-goon-migration),
[detailed producer requirements](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#24-additional-producer-requirements-retained-from-lifecycle-rev2)
and [migration evidence](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#25-producer-and-adapter-conformance-retained-from-lifecycle-rev2).
They are not a second lifecycle-owned repository-intelligence specification.

---

# 75. OH-HM-004 — execution policy remains OH-Goon-owned

Hashmarks may nominate:

- pytest target;
- Vitest target;
- Go test target;
- project verification target;
- exact logical verification membership.

OH-Goon still decides:

- whether to run it;
- when to run it;
- physical command projection;
- batching;
- workers;
- retry;
- timeout;
- parallelism;
- resource admission;
- broader gates;
- merge-policy sufficiency.

Hashmarks does not own physical execution.

---

# 76. Existing correct ownership: physical grouping

Current OH-Goon-owned grouping over already-admitted logical membership remains correct.

For example:

```text
A B C D E F
```

may physically become:

```text
[A B]
[C D]
[E F]
```

provided grouping:

- does not select new members;
- does not drop members;
- does not duplicate members;
- does not reorder in a way that breaks the declared contract.

Hashmarks owns logical selection.

OH-Goon owns physical grouping.

---

# 77. Existing correct ownership: retries and bisection

Retry, timeout refinement, physical shard splitting, and execution resume remain OH-Goon execution concerns.

Hashmarks may establish:

```text
these logical verification members are relevant
```

but it SHALL NOT establish:

```text
retry this test three times
split this batch
use two workers
resume this shard
apply this timeout
```

---

# 78. Existing correct ownership: execution receipt

OH-Goon SHALL continue owning proof that admitted logical membership was actually executed.

This includes:

- selected;
- attempted;
- resumed;
- completed;
- missing;
- duplicates;
- extras;
- retry lineage;
- terminal execution outcome.

Hashmarks owns selection evidence.

OH-Goon owns execution evidence.

---

# 79. Hashmarks MUST NOT become worker authority

None of the new Hashmarks requirements alter the process-worker architecture.

Hashmarks SHALL have no authority over:

- worker PID;
- Run process;
- ExecutionTicket claim;
- execution lease;
- worker shutdown;
- worker retry;
- Run recovery;
- Run placement;
- Run cancellation;
- process isolation.

Repository intelligence remains non-executable.

---

# 80. Critical cross-repository invariant

The intended flow SHALL be:

```text
HASHMARKS
    repository intelligence
            ↓
    qualified logical selection
            ↓
OH-GOON ADMISSION
    validates/freezes producer evidence
            ↓
    placement + capacity
            ↓
    Run admission
            ↓
RUN WORKER
    receives frozen authority
            ↓
    executes exactly admitted work
            ↓
OH-GOON
    records physical execution/result
```

At no stage may the worker ask:

```text
"Which tests should I really run?"
```

and derive a different logical answer.

---

# 81. Resolve once → transport once → execute once → report once

This invariant SHALL govern the integration.

## Resolve once

Hashmarks resolves repository-intelligence facts.

## Transport once

The public `oh_goon.integrations.hashmarks` API validates and carries
producer-qualified facts into admission.

## Execute once

OH-Goon's canonical Run path executes admitted work.

## Report once

OH-Goon records execution outcome against the exact admitted logical membership.

No layer SHALL independently repeat the previous layer's semantic decision.

---

# 82. Worker handoff carries resolved facts, not decision inputs

The worker handoff SHOULD contain references/identities for already-resolved values.

Conceptually:

- Run ID;
- ExecutionTicket capability;
- Plan identity;
- repository/source identity;
- selected logical work identity;
- placement identity;
- resource envelope.

It SHOULD NOT carry raw information primarily so the worker can independently repeat repository selection.

---

# 83. Hashmarks evidence is frozen before worker execution

The worker SHALL NOT perform a new Hashmarks logical selection for already-admitted work.

If repository freshness must be revalidated before effects, the system may verify that the admitted source identity still holds.

It SHALL NOT silently calculate a different logical selection.

A changed source SHALL invalidate/refuse the admitted work rather than mutate its selected membership underneath execution.

---

# 84. Single source of truth map

Use the current [architecture owner matrix](../ARCHITECTURE.md#ownership-matrix)
for actual execution owners and [Hashmarks §§3 and 7A](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#3-product-ownership-invariant)
for repository facts versus OH-Goon policy, custody and observability. Do not
create another standalone map or capability registry. Architecture checks
protect these semantic ownership boundaries; prose is not runtime authority.

---

# 85. Architecture guard: no second process-worker path

OH-Goon SHALL add a structural architecture guard proving that normal production Run execution can enter effectful `FleetRunExecution` only through the canonical Run-worker owner.

The guard SHALL detect accidental reintroduction of:

- direct thread execution;
- special local execution;
- special CI execution;
- special ML execution;
- special agent execution;
- special scheduled execution;
- fallback execution;
- bypass execution.

The check SHALL protect semantic ownership rather than aesthetic file structure.

---

# 86. Architecture guard: Hashmarks boundary

The mandatory public SSOT API/import/anti-shadow guards, capability census and
behavioral authority challenges are specified by
[Hashmarks §10](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#10-structural-architecture-guards)
and [§25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#25-producer-and-adapter-conformance-retained-from-lifecycle-rev2).
The [existing boundary implementation](../../backend/src/oh_goon/repository_tooling/architecture/hashmarks_harness_boundary_contract.py)
and [negative regressions](../../scripts/tests/test_hashmarks_harness_boundary_contract.py)
are executable owners, not a substitute for the complete semantic migration.

---

# 86G. Execution observability SHALL remain OH-Goon-owned

OH-Goon owns observations about execution lifecycle and execution outcome, including:

- Run admission;
- worker spawn;
- worker claim;
- execution custody;
- Step/Attempt lifecycle;
- harness invocation;
- retries;
- timeout;
- cancellation;
- recovery;
- process exit;
- external-effect settlement;
- terminalization;
- certification;
- execution receipt.

The boundary SHALL be:

```text
Hashmarks:
"What repository entity/evidence does this observation correspond to?"

OH-Goon:
"What happened during this admitted execution?"
```

Example:

```text
runtime traceback
      ↓
Hashmarks-qualified source / symbol / line correlation
      ↓
OH-Goon binds that qualified repository fact to
Run / Step / Attempt / execution receipt
```

OH-Goon may attach execution identity to the producer fact.

It SHALL NOT independently derive the repository fact if Hashmarks owns that capability.

---

# 86H. Game Tape, logs, tracing, and diagnostics SHALL be projections, not semantic owners

Any of the following may expose useful information:

- Game Tape;
- structured logs;
- trace spans;
- metrics;
- worker diagnostics;
- process diagnostics;
- CLI diagnostics;
- API diagnostics.

They SHALL NOT independently establish:

- repository ownership;
- repository relevance;
- verification ownership;
- dependency consumer scope;
- Run state;
- execution custody;
- effect settlement;
- terminal result.

The architecture SHALL classify every observation as one of:

```text
authoritative state
producer-qualified repository fact
execution evidence
diagnostic projection
```

A field SHALL not silently move between these categories.

---

# 86Q. Observability SHALL never bypass admission freeze

Repository correlation obtained after execution starts may explain an observation.

It SHALL NOT silently change already-admitted logical work.

Example:

```text
Run admitted with logical verification set A
        ↓
runtime observation produces new repository correlation B
```

The new correlation MAY:

- inform diagnostics;
- inform a future Run;
- trigger explicit recovery/policy handling where already allowed;
- produce a new bounded decision request through a new admission lifecycle.

It SHALL NOT silently mutate the current Run's frozen logical membership.

---

# 86S. Execution-observability ownership tests

The final implementation SHALL prove:

- Worker spawn/claim/exit diagnostics record execution facts without invoking
  repository-intelligence semantics.
- Changing a log line, Game Tape projection or trace emission does not alter
  canonical Run state or repository-intelligence truth.
- Source identity drift and later diagnostic correlation cannot silently rewrite
  the current Run's admitted selection.

Producer correlation, ambiguity and no-path-prefix-fallback challenges from
original REV2 §86S.B/D/E are specified by
[Hashmarks §25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#25-producer-and-adapter-conformance-retained-from-lifecycle-rev2).

---

# 87. Missing producer facts

The [Hashmarks failure contract](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#11-failure-and-unknown-state-requirements)
governs missing, unknown, stale, incomplete or unsupported producer facts.
Existing bounded OH-Goon policy may widen/refuse execution; the worker receives
only its admitted result and cannot fill missing repository truth itself.
Original REV2 temporary-heuristic and Node/npm clauses are dispositioned by
[Hashmarks §§24–26](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#24-additional-producer-requirements-retained-from-lifecycle-rev2).

---

# 89. State-model requirement

Worker custody SHOULD be represented using states that cannot express semantically impossible combinations.

Avoid loosely related fields capable of representing contradictions such as:

```text
claimed=true
worker_pid=null
execution_owner=parent
worker_running=true
```

Conceptual legal states may include:

```text
ADMITTED_UNCLAIMED
WORKER_OWNED
RECOVERY_OWNED
TERMINAL / no execution lease
```

Exact naming is implementation-owned.

Invalid combinations SHOULD fail at construction/storage boundaries rather than being interpreted repeatedly by callers.

---

# 90. Race ordering

Canonical SQLite transition authority SHALL order material races.

Wall-clock time SHALL NOT decide ownership races.

Required race cases include:

- worker claim vs cancellation;
- worker claim vs shutdown;
- worker death vs cancellation;
- worker completion vs cancellation;
- worker completion vs forced termination;
- worker death vs recovery transfer;
- two workers claiming one ticket;
- executor drain vs new placement.

Every race SHALL have one deterministic authoritative result.

---

# 91. Worker claim vs cancellation

If durable cancellation wins before worker claim commits, the worker SHALL NOT gain new normal effect authority.

If worker claim commits first, the worker SHALL observe cancellation using canonical durable cancellation semantics.

There SHALL be no local-timestamp ambiguity.

---

# 92. Completion vs cancellation

The existing Run lifecycle remains the ordering owner.

Late cancellation SHALL NOT rewrite an already committed terminal completion.

Completion after terminal/cancellation ownership has been lost SHALL fail closed.

---

# 93. Drain vs placement

Once executor `DRAINING` state is authoritative, new placement SHALL not commit against it.

Placement already atomically admitted before drain may continue.

The existing executor operational-state/placement transaction boundary SHALL order the race.

---

# 94. Failure contract

Failures SHALL preserve their meaning across layers.

At minimum distinguish:

- admission failure;
- worker spawn failure;
- worker claim refusal;
- worker crash;
- Run execution failure;
- cancellation;
- forced termination;
- database-authority failure;
- recovery failure;
- cleanup/containment failure.

These SHALL NOT collapse into one generic `worker failed` meaning when retry/recovery behavior differs.

---

# 95. No generic catch-and-respawn loop

The supervisor SHALL NOT implement:

```python
while worker_failed:
    spawn_again()
```

for admitted Run execution.

Any retry after worker loss MUST enter through explicit recovery/reconciliation semantics.

---

# 96. Performance requirements

Process isolation adds startup cost.

Measure separately:

- Plan resolution;
- Hashmarks selection;
- Run admission;
- process spawn;
- worker claim;
- worker bootstrap;
- harness startup;
- actual workload execution.

The implementation SHALL record before/after admission-to-`RUN_STARTED` measurements on representative local workloads.

No worker reuse/prewarming SHALL be introduced merely to avoid measuring the real cost.

Performance work may optimize:

- imports;
- bootstrap composition;
- state reopening;
- transport;

provided correctness boundaries remain unchanged.

---

# 97. Resource-lifetime requirement

The worker's lifetime SHALL be explicit:

```text
spawn
  ↓
claim
  ↓
execute
  ↓
terminalize / lose authority
  ↓
cleanup
  ↓
exit
```

Every worker-specific resource MUST have an explicit lifetime owner, including:

- temporary directories;
- sockets;
- IPC handles;
- log pipes;
- process groups;
- database handles;
- worker-local transport files.

Cleanup SHALL be owned in one place rather than duplicated defensively across unrelated callers.

---

# 98. Upgrade policy

This architecture SHALL NOT retain a permanent compatibility execution route for the previous in-process thread model.

Once the worker path is qualified, the direct thread execution route SHALL be removed.

Upgrade SHOULD prefer a quiescent cut:

```text
old runtime drains
        ↓
no active Run
        ↓
upgrade
        ↓
new runtime starts
```

If startup encounters active legacy ownership that cannot be interpreted safely, startup MUST fail closed or enter explicit existing recovery handling.

It SHALL NOT manufacture worker ownership from ambiguous legacy state.

---

# 99. Implementation sequencing

Implementation SHOULD proceed in coherent dependency order.

## Stages A–D — producer qualification, release and consumer adoption

Follow [Hashmarks §16](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#16-migration-order) and
[§24–25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#24-additional-producer-requirements-retained-from-lifecycle-rev2)
for the complete producer/consumer dependency order, released-package adoption
and replaced-heuristic removal. These requirements are owned there once.
Unsupported ecosystems do not block unrelated worker lifecycle work (§100).

## Stage E — strengthen architecture guards

Protect:

- Hashmarks semantic ownership;
- one execution path.

## Stage F — worker ownership model

Implement:

```text
admitted custody
→ worker process
→ atomic claim
→ ExecutionSession
```

without altering the semantic `FleetRunExecution` path.

## Stage G — supervisor switch

Change `RunExecutionSupervisor` from direct in-process execution to Run-worker supervision.

All production normal execution now uses the canonical path.

## Stage H — remove old execution path

Delete direct thread-owned Run execution.

No feature flag.

No fallback.

## Stage I — drain/recovery qualification

Qualify:

- parent death;
- worker death;
- claim races;
- cancellation races;
- drain;
- forced containment;
- effect reconciliation;
- executor drain;
- startup recovery.

---

# 100. Cross-repository sequencing rule

The process-worker lifecycle SHALL NOT be structurally blocked on every future Hashmarks improvement.

Worker lifecycle work may proceed once it obeys:

```text
worker consumes already-admitted repository evidence
worker does not reselect repository work
```

Hashmarks improvements may land in parallel.

However the new worker protocol SHALL NOT cement current consumer heuristics into durable execution concepts.

For example, the worker protocol SHALL NOT carry fields like:

```text
frontend_special_case
is_conftest
dependency_scope_guess
```

as durable semantic facts.

---

# 101. Mandatory execution failure matrix

| Failure cut | Required behavior |
|---|---|
| admission fails before durable Run | no Run execution, no worker |
| admission succeeds; supervisor submission fails | terminalize through existing admitted-owner path |
| process spawn fails | terminal submission failure; release through canonical lifecycle |
| worker starts but fails before claim | no execution effects |
| two workers race claim | exactly one winner |
| cancellation wins before claim | worker cannot begin normal work |
| claim succeeds then worker dies before `RUN_STARTED` | recover without blind replay |
| worker dies after `RUN_STARTED` | preserve Run; use durable Step/effect evidence |
| external effect succeeds then worker dies before settlement | outcome remains unknown until reconciliation |
| parent dies while worker is alive | replacement runtime does not steal ownership |
| worker completes while cancellation races | canonical lifecycle decides one terminal outcome |
| executor enters drain after Run admission | Run continues; no relocation |
| runtime enters drain with active Run | no new work; active worker may finish |
| drain deadline expires | durable cancel → grace → forced containment if required |
| force-kill succeeds | do not infer external effects were rolled back |
| SQLite authority becomes unavailable | do not fabricate terminal state |
| cleanup fails after work failure | preserve work failure plus cleanup evidence |
| cleanup fails after success | do not overclaim clean completion where cleanup proof matters |
| worker exits after terminal commit | terminal durable state remains authoritative |

---

# 102. Required producer boundary matrix

Consume the complete [Hashmarks test matrix](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#18-required-test-matrix)
and [producer/adapter challenges](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#25-producer-and-adapter-conformance-retained-from-lifecycle-rev2).
Source drift invalidates current admitted work rather than replacing its
membership (§83); malformed or insufficient evidence cannot become a test
failure, fabricated selection or worker effect authority.

---

# 103. Mandatory deterministic execution tests

## A. Canonical-path convergence

Prove that:

- manual local Run;
- scheduled Run;
- strict Run;
- agentic Plan Run;
- representative ML/training Run;

all converge through the same worker/supervisor execution boundary.

## B. Direct-path rejection

A production caller attempting effectful `FleetRunExecution` without worker claim MUST fail or be structurally impossible.

## C. Atomic claim race

Start two claimant processes for one ticket.

Exactly one gains executable ownership.

## D. Stale ticket replay

After worker claim, replay the original ticket.

It MUST fail.

## E. Dead predecessor recovery

Kill a worker after claim.

Recovery MUST require positive predecessor-death proof.

## F. Live predecessor protection

Attempt recovery while worker remains alive.

Recovery MUST fail closed.

## G. Parent crash

Terminate the parent/control process while worker remains alive.

Replacement runtime MUST detect existing worker ownership and MUST NOT redispatch.

## H. Worker crash isolation

Crash the Run worker abnormally.

Control/runtime process remains alive.

## I. Drain success

Begin runtime drain during an active Run.

Prove:

- no new Run admitted;
- active Run not cancelled merely because shutdown started;
- active Run may finish;
- runtime exits after completion.

## J. Drain escalation

Use controlled non-terminating work.

Prove:

```text
QUIESCING
→ cancellation
→ grace
→ forced containment
→ reconciliation
```

## K. Executor drain

Drain an executor that owns an admitted Run.

Existing Run remains bound.

New Run is not placed there.

## L. External-effect crash cut

Crash immediately after external visibility but before settlement.

No automatic replay.

Outcome remains unresolved until reconciliation.

## M. Terminal-before-exit

Commit terminal state, then force abnormal worker exit.

Terminal state remains authoritative.

## N. Database identity failure

Cause database authority validation failure in worker.

No fresh-database fallback.

No fabricated terminal Run.

---

# 104. Producer and adapter qualification

Original REV2 §§104–106 are retained in full under the
[Hashmarks producer and adapter conformance requirements](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#25-producer-and-adapter-conformance-retained-from-lifecycle-rev2).
They cover the complete changed-set producer vectors, consumer non-reinterpretation
and released dependency-consumer migration safety. They do not become worker
selection or execution logic.

---

# 107. Existing tests SHALL NOT be deleted merely because architecture changes

For every removed or rewritten test:

1. identify the requirement it previously protected;
2. state whether that requirement still applies;
3. if it still applies, provide replacement evidence;
4. if the requirement changed, document the new requirement.

Deleting a test is not itself a repair.

---

# 108. Process death is not effect completion

Tests SHALL reject assumptions such as:

```text
process gone
=
effect stopped
```

or:

```text
process gone
=
effect never happened
```

For subprocesses, remote execution, and native services, the actual effect owner must establish outcome/quiescence.

---

# 109. No additional generic workflow infrastructure

This work SHALL NOT introduce:

- a generic Job framework;
- a generic Saga framework;
- a new distributed task queue;
- a second durable workflow state machine;
- a new scheduler database;
- a new event-sourcing system solely for worker isolation.

Existing OH-Goon lifecycle machinery SHALL be extended only where needed.

---

# 110. No LiveKit dependency

OH-Goon SHALL NOT depend on LiveKit to implement this work.

Borrowed concepts are limited to:

- explicit custody boundaries;
- isolated job/run process;
- drain-first runtime lifecycle;
- separation between long-lived server and short-lived execution process.

These concepts SHALL be translated into existing OH-Goon domain ownership.

---

# 111. Pros of the target model

Expected improvements:

## Failure isolation

One Run's process-level failure is less likely to kill the control plane.

## Clear live custody

The process physically executing the Run becomes the durable execution owner.

## Better cleanup

One Run has one operating-system process lifetime.

## Better deployment behavior

Runtime stops admitting work while active work drains.

## Stronger harness containment

Native-agent issues remain beneath a Run-worker boundary.

## Future extensibility

Remote workers or multi-Run concurrency can later build on explicit custody without changing Run semantics.

## Simpler reasoning

The ownership hierarchy becomes:

```text
Run
→ worker execution custody
→ Step / Attempt
→ native harness session
```

---

# 112. Accepted trade-offs

## Process startup overhead

Accepted in exchange for real failure isolation.

Measure before optimizing.

## More explicit ownership transfer

Required because process isolation without custody transfer would weaken correctness.

## More shutdown states

Necessary to distinguish drain, cancellation, and forced containment.

## More failure testing

Necessary because crashes can occur between admission, spawn, claim, execution, settlement, and exit.

## Platform differences

Linux and Windows process launch may differ.

Semantic lifecycle MUST remain identical.

## More process-correlation diagnostics

Required but non-authoritative.

---

# 113. Explicit non-goals

This work SHALL NOT:

- implement LiveKit Agents;
- implement WebRTC;
- implement LiveKit rooms;
- introduce a new agent-delegation framework;
- replace Pi/OpenCode/Codex;
- replace SQLite;
- replace FleetRun;
- redesign the Plan/Goon/Step model;
- introduce arbitrary distributed execution;
- introduce unrestricted multi-Run concurrency;
- implement worker prewarming;
- introduce automatic Run redispatch;
- create a second scheduler;
- create a durable second Job model;
- retain old thread execution as permanent compatibility;
- move execution/result authority into Hashmarks.

---

# 114. Deletion requirements

A feature is not complete merely because new code exists.

Repository-semantic deletion obligations are owned by
[Hashmarks §14](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#14-required-removal-and-cleanup)
and the [source-specific migration census](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#25-producer-and-adapter-conformance-retained-from-lifecycle-rev2).

## After worker migration

Search for and remove:

- direct `FleetRunExecution` submission from control process;
- `ThreadPoolExecutor`-owned production Run execution;
- local execution bypass;
- scheduled execution bypass;
- agent execution bypass;
- worker fallback to parent execution;
- compatibility flags selecting old execution.

No alternate owner remains callable.

---

# 115. Prohibited final architecture

This fails the requirements:

```text
                       FleetRun
                          │
               ┌──────────┴───────────┐
               │                      │
        old thread execution    process execution
               │                      │
               └──────────┬───────────┘
                          ▼
                        Steps
```

Two independently callable entry paths remain two execution authorities even if they share lower-level functions.

---

# 116. Required final architecture

```text
                         HASHMARKS
                             │
                    repository intelligence
                             │
             qualified logical work/evidence
                             │
                             ▼
             oh_goon.integrations.hashmarks
                             │
                       validate/project
                             │
                             ▼
                    FleetRun admission
                             │
              placement + capacity reservation
                             │
                             ▼
                      ExecutionTicket
                             │
                             ▼
                 RunExecutionSupervisor
                             │
                             ▼
                    Run worker process
                             │
                     atomic custody claim
                             │
                             ▼
                  FleetRunExecution
                             │
                             ▼
                    ExecutionSession
                             │
                             ▼
                 FleetTargetScheduler
                             │
                      Step / Attempt
                             │
                             ▼
                Pi / OpenCode / Codex
```

There is:

- one repository-intelligence owner;
- one Run admission owner;
- one process-worker execution path;
- one live execution-custody owner;
- one terminalization path.

---

# 117. Cross-repository completion dependency

The complete producer and public integration boundary definition of done is
specified by [Hashmarks §21](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#21-definition-of-done),
with retained detailed producer requirements and conformance in §24–25.
Completing this execution lifecycle does not establish those claims. Final
cross-repository closure requires both contracts and the HUG-183 evidence audit.

---

# 119. Definition of done — worker lifecycle

The execution lifecycle is complete only when:

- every normal production `FleetRun` uses the Run-worker boundary;
- no production thread-owned alternative remains;
- no configuration selects old versus new execution;
- spawn failure cannot trigger parent execution fallback;
- worker claim is atomic;
- stale ticket replay fails;
- the worker is the actual durable live process owner;
- parent death does not allow theft from a live worker;
- dead worker recovery requires positive predecessor-death proof;
- worker death does not cause blind redispatch;
- runtime shutdown supports drain-first behavior;
- forced kill remains containment rather than result authority;
- local, scheduled, agentic, CI/CD, and ML workflows use the same lifecycle;
- architecture checks prevent a second execution path from returning.

---

# 120. Final non-negotiable invariants

## Invariant A

> **Process workers strengthen the canonical OH-Goon execution path. They never become another execution path.**

## Invariant B

See the canonical [Hashmarks ownership invariant](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#3-product-ownership-invariant).

## Invariant C

> **OH-Goon remains the sole owner of admission, physical execution, worker lifecycle, retries, recovery, and results.**

## Invariant D

> **A Run worker consumes resolved/admitted facts. It does not rediscover work.**

## Invariant E

See the canonical [Hashmarks ownership invariant](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#14-required-removal-and-cleanup).

## Invariant F

> **Unknown evidence remains unknown. Process failure or missing observation never manufactures certainty.**

## Invariant G

> **One durable transition has one canonical owner and one canonical commit path.**

## Invariant H

See the canonical [Hashmarks ownership invariant](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#35-ssot-is-scoped-by-semantic-fact-scope-and-generation).

---

# 121. Implementation review questions

Every implementation change SHALL be reviewed against these questions.

## Question 1 — Does this add another Run execution route?

If yes, reject the design unless it replaces and deletes the previous route.

## Question 2 — Does this let the Run worker re-decide already-admitted work?

If yes, reject the design.

## Question 3 — Does this let OH-Goon decide a repository-intelligence fact Hashmarks should own?

Apply the canonical [Hashmarks future-change review](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#23-future-change-gate).

## Question 4 — Does this create another durable Run/worker truth?

If yes, reject the design.

## Question 5 — Does this turn a heartbeat, PID, log, Game Tape projection, or process exit into Run authority?

If yes, reject the design.

## Question 6 — Does this treat worker death as proof that an external effect did not happen?

If yes, reject the design.

## Question 7 — Does this preserve a replaced heuristic "just in case"?

Apply the canonical [Hashmarks removal contract](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#14-required-removal-and-cleanup); missing producer precision never authorizes a semantic fallback.

---

# 122. Final design statement

The project SHALL describe this work as:

> **Strengthen OH-Goon's existing execution lifecycle with explicit isolated Run-worker custody, while consolidating repository-intelligence semantics in Hashmarks.**

It SHALL NOT be described as:

> **implement LiveKit architecture**

and SHALL NOT be implemented as:

> **add process workers alongside the current executor**

The final ownership model is:

```text
Hashmarks
    owns what repository evidence means
        ↓
OH-Goon
    owns what admitted work is executed
        ↓
Run worker
    owns only current physical execution custody
        ↓
Harness
    owns only its native session
```

The architectural goal is:

```text
fewer owners
fewer duplicated semantic decisions
fewer execution paths
stronger explicit boundaries
```

not more abstractions.

---

# 123. Completion gate

This requirements document SHALL not be considered implemented until all of the following are evidenced on the final candidate:

- canonical worker path implemented;
- old direct thread execution removed;
- no silent fallback exists;
- worker claim race qualified;
- stale-ticket replay refused;
- parent-death/live-worker behavior qualified;
- dead-worker recovery qualified;
- drain-first shutdown qualified;
- cancellation escalation qualified;
- external-effect crash-cut qualified;
- terminal-before-exit behavior qualified;
- architecture guard for one execution path passes;
- the complete [Hashmarks definition of done](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#21-definition-of-done) and retained §24–25 requirements are independently evidenced;
- execution observability remains OH-Goon-owned and non-authoritative projections remain projections;
- local, scheduled, agentic, CI/CD, and representative ML execution all converge on the same Run-worker lifecycle;
- final architecture documentation and ownership map match the implementation.

No clean completion claim is valid while a known alternate execution route, alternate repository-intelligence owner, or unreviewed fallback remains.

## Source-section crosswalk

This is source traceability for the imported REV2 requirements, not a runtime
registry, proof receipt or implementation-status ledger. Original section and
ELR identities remain valid references. Execution sections retained without
semantic change are §§1, 4–10, 12–52, 75–83, 85, 86G–H, 86Q, 89–98, 100–101, 103,
107–113, 115, 119 and 122. Original DAG terminology is normalized as noted
above. The remaining sections are dispositioned below.

| Original lifecycle REV2 sections | Canonical disposition |
| --- | --- |
| 2–3 | Execution seam in §§2–3 here; complete product ownership in [Hashmarks §§3/7A](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#3-product-ownership-invariant). |
| 11, 116 | Correct the source's reversed activation/owner order to match §1 and the existing owner: custody claim → `FleetRunExecution` → lifecycle-owned `ExecutionSession` → `FleetTargetScheduler`. Claim remains mandatory before effectful work. |
| 53–57 | Historical findings in [Hashmarks §§22/25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#22-reviewed-baseline-findings-that-this-document-must-close); released adoption in §16. |
| 58–65 / HM-CHANGE-001–008 | Retained in full in [Hashmarks §24](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#24-additional-producer-requirements-retained-from-lifecycle-rev2). |
| 66–69 / HM-DEP-001–004 | Retained in full in [Hashmarks §24](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#24-additional-producer-requirements-retained-from-lifecycle-rev2). |
| 70–74 / OH-HM-001, OH-HM-002, OH-HM-003 | [Hashmarks §§5–6/8/14](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#5-allowed-responsibilities-of-the-oh-goon-hashmarks-boundary); exact source-cleanup findings in §25. |
| 84 | Current architecture owner matrix plus [Hashmarks §§3/7A](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#3-product-ownership-invariant); no third registry. |
| 86, 86A–E, 86J–L | [Hashmarks §§4–6/9–10](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#4-canonical-oh-goon-hashmarks-composition-root-and-mandatory-ssot-api); semantic call paths and public API conformance in §§10/25. |
| 86F, 86O–P | Runtime/source observation and bounded source-bound correlation retained in full in [Hashmarks §24](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#24-additional-producer-requirements-retained-from-lifecycle-rev2). |
| 86I, 86T | [Hashmarks §§7A/10.10/14/25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#7a-semantic-classification-of-similar-looking-oh-goon-code) own complete semantic sweep and classification. |
| 86M–N, 87–88 | [Hashmarks §§1/3.4/6/11/14/24–26](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#26-source-reconciliation-and-delivery-traceability): stronger mandatory no-fallback rule; finite removal evidence and safety proof retained without a new registry. |
| 86R | [Hashmarks §§10/18/25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#10-structural-architecture-guards) retain direct/private/raw-row challenges, mutation sensitivity, unknown preservation, release cleanup and public-surface convergence. |
| 86S | Execution tests in §86S here; producer correlation/ambiguity/no-prefix tests in [Hashmarks §§18/25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#25-producer-and-adapter-conformance-retained-from-lifecycle-rev2). |
| 86U–V | [Hashmarks §§4/21/25–26](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#21-definition-of-done), including complete public API and semantic completion obligations. |
| 99 | Execution stages E–I retained here; producer/adoption stages A–D in [Hashmarks §§16/24–25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#16-migration-order). |
| 102 | [Hashmarks §§18/25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#18-required-test-matrix) plus source-freeze invalidation in §§83/102 here. |
| 104–106 | Full original vectors retained in [Hashmarks §25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#25-producer-and-adapter-conformance-retained-from-lifecycle-rev2). |
| 114 | Worker deletion remains here; producer deletion in [Hashmarks §§14/25](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#14-required-removal-and-cleanup). |
| 117–118 | [Hashmarks §§21/24–26](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#21-definition-of-done); §117 here retains the cross-contract dependency. |
| 120–121 | Execution invariants/questions retained here; repository ownership/removal questions link to [Hashmarks §§3.5/14/23](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#23-future-change-gate). |
| 123 | Execution completion retained here; full independent [Hashmarks completion](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md#21-definition-of-done) required by reference. |

## Bundled delivery and documentation hierarchy

| Delivery owner | Scope |
| --- | --- |
| [HUG-177](https://linear.app/hugloss/issue/HUG-177) / [HUG-178](https://linear.app/hugloss/issue/HUG-178) | Hashmarks producer requirements in the separate canonical document. |
| [HUG-179](https://linear.app/hugloss/issue/HUG-179) | Public SSOT adoption and removal of shadow repository semantics. |
| [HUG-180](https://linear.app/hugloss/issue/HUG-180) | Worker custody, launch, claim, cutover and one-path protection (§§1–16, 33–50, 80–85, 89–100, 114–116/119/123). |
| [HUG-181](https://linear.app/hugloss/issue/HUG-181) | Failure/drain/cancel/recovery/resource qualification (§§17–32, 48–52, 86G–H/Q/S, 90–97, 101/103/107–108/119/123). |
| [HUG-182](https://linear.app/hugloss/issue/HUG-182) | Normative deduplication, navigation and source traceability. |
| [HUG-184](https://linear.app/hugloss/issue/HUG-184) | Active documentation convergence; current behavior must remain distinguishable from this target. |
| [HUG-185](https://linear.app/hugloss/issue/HUG-185) | Compatible execution-adjacent backlog scope and material dependencies. |
| [HUG-183](https://linear.app/hugloss/issue/HUG-183) | Final full-document requirement-to-evidence audit, leftover sweep and released/candidate qualification. |

The two separate normative documents define their respective requirement scopes.
Current architecture/owner documents describe implemented behavior and executable
owners. Contributor/operator guides and examples consume those contracts;
historical ADRs/checkpoint material supply context, not current qualification.
A guide must not redefine a higher-level owner, and a target requirement must
not be presented as an implemented or qualified guarantee. Authority-changing
work follows the [existing documentation anti-drift rules](DOCUMENTATION_ANTI_DRIFT.md).

Use focused lifecycle/architecture proof during construction and the existing
coherent-candidate qualification once ready. Do not add a new gate or repeat a
complete native/release suite for each checklist row. These issue allocations
are traceability, not PASS claims or substitutes for exact-candidate evidence.
