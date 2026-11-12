# Execution supervisor convergence

Oh-Goon retains one FleetRun lifecycle and distinct, nested lifetime owners.
Run submission is owned by `RunExecutionSupervisor`; individual native commands
and repository qualification batches use the existing command owner. A test
batch's process supervision is not another product FleetRun admission route.

## Run submission and durable authority

[`FleetRunControlPlane`](../../backend/src/oh_goon/services/fleet_run_control_plane.py)
captures explicit worker configuration before admission, then submits the exact admitted `ExecutionTicket` through
[`RunExecutionSupervisor`](../../backend/src/oh_goon/runtime/execution/run_supervisor.py)
to one fresh native Run-worker process. The supervisor owns bounded active Run
observations and process-local failure reporting; its observer thread runs only
the existing native command owner. It accepts a prepared `RunWorkerLaunch`,
never a Fleet execution callable or a reusable worker pool. The conservative
one-active-Run limit remains in force per runtime. Its
`ACCEPTING → QUIESCING → CLOSED` states control local submission; they are not
durable Run status.

The [SQLite execution lease](../../backend/src/oh_goon/runtime/execution/sqlite_lease.py)
also admits only one live execution across runtimes sharing that database. A
second runtime, different repository or another native profile does not bypass
that atomic lease. Busy refusal prevents a second admitted worker/Step; supported
preflight and source capture may already have occurred before final admission.
It is not a claim of zero preparatory processes or source transport.

Eligible repository targets may run in parallel beneath one claimed worker through
the existing [target scheduler](../../backend/src/oh_goon/services/fleet_target_scheduler.py)
and admitted Plan policy. This is distinct from admitting multiple Runs. Current
Agent ingress requires one candidate repository and one frozen native selection;
general target parallelism does not enable mixed-harness native overlap.
Independent databases have separate authority domains, not a shared capacity or
workspace lease. Choosing another store cannot prove an older writer quiescent or
authorize concurrent mutation of its workspace. See the [native concurrency
scope](AGENT_HARNESS_COMPATIBILITY.md#native-concurrency-boundary).
Its [scoped admission proof](AGENT_HARNESS_COMPATIBILITY.md#scoped-native-admission-qualification--2026-11-06)
checks refusal while the original worker is alive and fresh admission after
settlement; it does not qualify overlapping native Runs or expand capacity.

The internal [`_application_worker`](../../backend/src/oh_goon/_application_worker.py)
entrypoint verifies a bounded sealed-descriptor handoff, establishes the loaded
Hashmarks identity, and reopens the existing SQLite database with a fresh
process-local session. `ExecutionCoordinator.claim_fleet_worker` atomically
checks the exact Run/token/predecessor/session/PID/birth and transfers
`ADMITTED_UNCLAIMED` custody to `WORKER_OWNED`. Only then does minimal worker
composition construct the existing `FleetRunExecution`. It activates the
original `ExecutionSession`, schedules targets/Steps and delegates
terminalization to `FleetRunLifecycle`. SQLite leases and Journal facts remain
authoritative; a stale parent or replayed ticket cannot activate executable work.

Accepted Fleet operation reports remain current for their original admitting
control session and their actual worker writer. The Journal derives that
freshness context from the exact accepted Run binding; durable fact provenance
still records the worker's fresh session. A restarted runtime gets a new session
and sees those results as stale, and later operation invalidation still applies.

Submission failure after admission is settled through the admitted lifecycle
owner's restricted finish-only session, coupling terminal state and lease/capacity
settlement while custody remains unclaimed. A process exit, lost observation
channel or supervisor diagnostic cannot manufacture a
terminal Run result. Native harness sessions remain below Step/Attempt and do
not own Run admission, recovery or durable truth.

### Controlled shutdown

`ApplicationRuntime.shutdown()` first quiesces admission, then stops Schedule
production and lets already-admitted work finish. The default drain budget is
30 seconds, followed by durable Run cancellation and a five-second cancellation
grace. Remaining work is contained by the existing native command owner, with
one-second TERM grace and a ten-second containment wait. Callers may pass
explicit shutdown budgets; `drain_timeout_seconds=None` explicitly selects an
unbounded drain. `wait_for_runs=False` skips the initial drain, while preserving
durable cancellation, grace, containment and recovery.

Final admission registers a process-local pending submission inside the original
SQLite writer cut. It can launch after quiescing if admission won the ordering;
later admissions roll back. Capacity waits only probe the same supervisor and
never retain its lock. Runtime drain and an executor's `DRAINING` admission mode
remain separate: draining an executor preserves its existing placement.

Shutdown callers serialize through the existing runtime lifecycle. An incomplete
shutdown remains `QUIESCING`, reports its failure, and can be retried. Native
worker-group retirement does not establish Step-group quiescence. The original
recovery owner checks retained custody, positive predecessor death and known
native process scopes before reconciliation. Native session retirement may use
the remaining containment budget outside SQLite; the canonical recovery writer
then revalidates custody and quiescence before mutation. A terminal Run still
needs its original resource proof before runtime closure. Database or containment uncertainty
cannot become a fresh database, fabricated cancellation or `CLOSED` runtime.
Unsettled external effects remain unknown or quarantined; a forced process exit
never proves rollback.

Worker bootstrap reports distinct, fixed diagnostic cuts for handoff refusal,
runtime identity, database authority, claim refusal, composition, execution and
observed cancellation. Supervision separately reports spawn, abnormal exit and
containment failures. Safe Run/PID/birth correlation does not expose claim
material. Cleanup and display failures remain separate from the original Run
outcome; the bounded failure mailbox is diagnostic, while the Journal retains
durable truth.

## Frozen work and remaining qualification

The [canonical lifecycle requirements](OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md)
define the same supervisor's fresh-process boundary and single-use custody claim.
[HUG-180](https://linear.app/hugloss/issue/HUG-180) implements that cutover;
[HUG-181](https://linear.app/hugloss/issue/HUG-181) owns the native worker
crash, drain, cancellation and recovery matrix. Qualification is specific to the
actual OS, filesystem mode and workload exercised; installed-harness, privileged
provider and deployment guarantees do not follow from portable process tests.
The broader lifecycle audit remains
[HUG-183](https://linear.app/hugloss/issue/HUG-183). This process-boundary change
does not certify the complete normative matrix or a physical deployment host.

The worker consumes frozen admitted work and opens its own valid runtime/SQLite
resources. It does not resolve Plans, reselect Hashmarks evidence, place work or
reserve capacity. Spawn failure and exit before claim settle through the existing lifecycle.
After claim, a nonterminal dead worker remains visible to canonical recovery;
the original parent cannot steal its custody. Worker exit and IPC are observations;
SQLite/Journal remain durable truth. Drain, cancellation and forced containment
must reuse existing effect/result owners, without competing command cleanup.
Local, CI/CD, scheduled, agentic, ML and application-source Runs converge on this
same custody boundary. Spawn-to-claim and claimed-bootstrap timings are bounded
diagnostic observations; they grant no execution or completion authority.

Native ingress also captures the existing scoped Git command owner's exact
workspace and resolved native selection. The worker restores that same owner
with its admitted isolation backend and the canonical workspace pin; it never
loads the provider catalog again or substitutes unrestricted Git. Only the
owned native callable and its exact read-only observation binding are
transportable. Arbitrary callbacks, altered observation bindings, termination
callbacks and phase-specific repair contexts refuse before admission. Native
ingress also refuses a workspace/profile that differs from that scoped Git
owner before native preflight. Native
session IDs and late service notifications cannot change this Run's worker
custody or another Run's frozen selection.

Live output and terminal transport stay nonblocking. Bounded loss counters and
a best-effort final capture summary expose dropped observations; a missing final
summary leaves capture completeness unavailable. These diagnostics carry no Run
result or custody authority, and parent/display loss does not synthesize terminal
EOF or cancellation.

## Common command owner

`oh_goon.engine.command.run_command` is the common process owner. It owns process-group creation, output draining, cancellation, timeout cleanup, and non-exportable control environment.

After its canonical claim, the fresh Run worker explicitly binds this command
owner's child-custody default. Native Git, uv, preparation and Step commands all
receive that lifetime; an explicit independent lifetime remains explicit. A
forked process does not inherit a binding for another PID. The Run worker itself
keeps `parent_death_signal=False`, so loss of the control parent does not kill
healthy admitted work.

On supported Linux hosts, the existing exec guard owns a passive native
guardian using pidfds. It preserves the actual command PID, process group,
invocation, environment, terminal and child-wait semantics. The guardian retains
only kernel custody handles, with no workload environment or stdio. While the
original command owner lives, that owner alone performs cleanup and acknowledges
quiescence. Positive owner death
lets the guardian contain its original group. This is an emergency lifetime
boundary, not an effect-result authority or a second Run executor. Arbitrary
detached work and separately owned services require their actual native owner;
process-group evidence cannot certify a container or remote provider.

The existing operation lifecycle observer also captures native preparation and
generic operation commands. Operation-scoped process events have no Step
identity; Step-scoped events retain their complete canonical attempt binding.
An explicit Step observer takes precedence. No synthetic Step or parallel
process history is introduced. Proven resource quiescence may follow an unknown
process result, but cannot invent an exit code, finalize unknown output or make
an unknown Step successful.

This narrower owner remains responsible for its admitted command even beneath
the required Run-worker boundary. A worker supervisor contains the Run lifetime;
it does not infer rollback, effect absence or native-session completion merely
because a process disappeared. Cleanup failure remains separate from the primary
result. An operator-owned native server does not become an owned daemon merely
because an admitted Step connects to it.

Qualification batches use strict process-group quiescence. A pytest leader exiting is not enough to finish a batch when descendants remain alive in the same owned group. The hard batch timeout therefore remains active until the whole group is gone. On timeout, Oh-Goon terminates the group and reports exit 124 with bounded captured output.

This preserves the stronger certification firewall while removing the second `Popen`/kill-tree implementation that previously lived in `repository_tooling.test_batches`.

## Timeout shard refinement

A multi-member pytest batch timeout is execution evidence that the initial shard may be too coarse; it is not automatically evidence that any member fails.

The batch runner therefore bisects timed-out multi-member batches deterministically under the original suite deadline. Each child keeps the same hard per-batch timeout ceiling. Bisection continues until children pass or a single-member shard times out. Single-member timeout remains a failure and is never hidden by further splitting.

This changes scheduling granularity only. It does not weaken assertions, deselection rules, coverage, markers, or suite deadlines.

## Environment authority

The generic command owner strips presentation/control descriptors such as `OH_GOON_PROGRESS_FD` and local access tokens from children. Repository tests cannot bypass pytest capture by inheriting Oh-Goon presentation authority.

An explicitly budgeted waited CLI Run uses this same command owner to launch
the original CLI in one native systemd scope before preparation. Its bounded,
sealed handoff transports the captured configuration and resolved database,
League and SQLite settings; ordinary Step environments keep their existing
sanitization. The child verifies the handoff against its actual native unit
before restoring execution authority. The existing Fleet and Step owners
still execute and report the Run exactly once.

The outer CLI forwards SIGINT/SIGTERM through a birth-matched pidfd to that
original child. Native scope accounting and cleanup observations remain
separate from the durable workload outcome. The parent never rebuilds a global
Journal view to append final accounting: it consumes a bounded child-issued
context and the original Journal owner performs an exact-head append. Changed
or missing authority leaves persistence incomplete. See
[local Run budgets](TEMPLATE_INPUT_INSPECTION.md#limit-a-local-run).

## SQLite preparation observation

Long preparation uses fresh, read-only admitted SQLite handles at bounded observation cadence. If SQLite reports `SQLITE_NOTADB` or `SQLITE_CORRUPT` on one fresh observer open while path identity is unchanged, Oh-Goon performs at most three observation-only re-proof attempts with bounded delays. A successful later proof must re-establish schema, journal, database identity, durable revision, and `PRAGMA quick_check` before execution continues.

No re-proof path creates, repairs, rewrites, resets, or replays durable state. Persistent identity failure still raises `DatabasePathIdentityLost` and stops execution.
