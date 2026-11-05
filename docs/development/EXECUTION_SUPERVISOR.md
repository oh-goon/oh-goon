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
one-active-Run limit remains in force. Its `ACCEPTING → QUIESCING → CLOSED`
states control local submission; they are not durable Run status.

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

Current application shutdown stops Schedule production and quiesces submission.
`ApplicationRuntime.shutdown(wait_for_runs=False)` requests cancellation of its
own active Run in application mode before waiting for supervisor shutdown;
`wait_for_runs=True` waits without that request. This is not proof of the required isolated-worker
parent-death, drain-deadline or containment behavior.

## Frozen work and remaining qualification

The [canonical lifecycle requirements](OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md)
define the same supervisor's fresh-process boundary and single-use custody claim.
[HUG-180](https://linear.app/hugloss/issue/HUG-180) implements that cutover;
[HUG-181](https://linear.app/hugloss/issue/HUG-181) owns crash, drain, cancellation
and recovery qualification. The broader lifecycle audit remains
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
