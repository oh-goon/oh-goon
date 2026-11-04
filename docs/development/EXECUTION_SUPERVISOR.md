# Execution supervisor convergence

Oh-Goon retains one FleetRun lifecycle and distinct, nested lifetime owners.
Run submission is owned by `RunExecutionSupervisor`; individual native commands
and repository qualification batches use the existing command owner. A test
batch's process supervision is not another product FleetRun admission route.

## Run submission and durable authority

[`FleetRunControlPlane`](../../backend/src/oh_goon/services/fleet_run_control_plane.py)
submits the exact admitted `ExecutionTicket` through
[`RunExecutionSupervisor`](../../backend/src/oh_goon/runtime/execution/run_supervisor.py)
to the existing `FleetRunExecution`. The supervisor currently owns a
`ThreadPoolExecutor`, a bounded set of active Run IDs and process-local failure
reporting. Its `ACCEPTING → QUIESCING → CLOSED` states control local submission;
they are not durable Run status. `FleetRunExecution` activates the original
`ExecutionSession`, schedules targets/Steps and delegates terminalization to
`FleetRunLifecycle`. SQLite leases and Journal facts remain authoritative.

Submission failure after admission is settled through the admitted lifecycle
owner, coupling terminal state and lease/capacity settlement. An inactive thread,
returned callable, process exit or supervisor diagnostic cannot manufacture a
terminal Run result. Native harness sessions remain below Step/Attempt and do
not own Run admission, recovery or durable truth.

Current application shutdown stops Schedule production and quiesces submission.
`ApplicationRuntime.shutdown(wait_for_runs=False)` requests cancellation of its
own active Run in application mode before waiting for supervisor shutdown;
`wait_for_runs=True` waits without that request. This is not proof of the required isolated-worker
parent-death, drain-deadline or containment behavior.

## Required isolated-worker evolution

The [canonical lifecycle requirements](OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md)
require the same supervisor to spawn one fresh Run worker, transfer live custody
through an atomic single-use claim and enter the existing Fleet execution path.
[HUG-180](https://linear.app/hugloss/issue/HUG-180) owns that cutover;
[HUG-181](https://linear.app/hugloss/issue/HUG-181) owns crash, drain, cancellation
and recovery qualification. This target is not yet the supervisor implementation.

The worker consumes frozen admitted work and opens its own valid runtime/SQLite
resources. It does not resolve Plans, reselect Hashmarks evidence, place work or
reserve capacity. Claim/spawn failure must settle through the existing lifecycle
without parent-execution fallback. Worker exit and IPC are observations;
SQLite/Journal remain durable truth. Drain, cancellation and forced containment
must reuse existing effect/result owners, without competing command cleanup.
Local, CI/CD, scheduled, agentic, ML and application-source Runs converge on this
same custody boundary at cutover; there is no thread/process compatibility mode.

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
