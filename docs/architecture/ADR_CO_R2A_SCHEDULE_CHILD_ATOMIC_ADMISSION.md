# ADR CO-R2a — One original Fleet admission cut owns scheduled child binding

**Status:** IMPLEMENTED IN DEVELOPMENT, NOT RELEASE CERTIFIED · **Parent:** CO-R2W3 SHA-256 `527208bc5f776ca6d8f88ee681dfe6d0c21be2dec8d518e5308efb35700611d0` · 2026-10-15.

## Proven gap

The previous `ScheduleExecutionEngine._start_repository()` committed an original Fleet `RUN_ADMITTED` and lease through `FleetRunControlPlane.start_bound()`, **then** performed an independent schedule `compare_and_set(mark_repository_running(...))`. A worker could be submitted after the Fleet cut but before a durable occurrence child reference. Cancellation, process death, a duplicate `advance`, or a lost acknowledgment could leave durable contradictory authorities. Two prechecks and deterministic IDs did not close the inter-transaction window.

## Decision and canonical ownership

- **One entrypoint:** `ScheduleExecutionEngine.advance()` calls the original `FleetRunControlPlane.start_bound()` once. No schedule launcher or alternate Fleet ingress.
- **One admission authority:** `FleetRunLifecycle.admit()` preserves `schedule_run_id` in its immutable in-cut `FleetRunAdmissionIntent` in legacy Direct, authenticated Direct, legacy Strict and authenticated Strict paths. The original journal still constructs the only `RUN_ADMITTED` event.
- **One SQLite write cut:** inside original `Journal.admit_run_in(connection, ...)`, scheduled admissions call the existing `ScheduleExecutionStore.bind_admitted_child_in()` with the **same live connection**. The store requires an active RUNNING occurrence, deterministic child ID, same immutable capability, Plan target, repository scope, pending child, and compares row version. It does not begin/commit another transaction. The transaction appends `RUN_ADMITTED` only after that binding has been validated/mutated; rollback reverts both rows plus the existing Fleet lease and/or strict placement.
- **No second publication:** `ScheduleExecutionEngine._start_repository()` now reloads the committed child and rejects a returned Fleet admission lacking that binding; it never CAS-mints RUNNING after admission. Existing advance/observe becomes restart-safe: a committed child is never freshly admitted on replay. A missing bound Fleet Run must retain its durable occurrence identity and require original recovery, **not** silently become NOT_STARTED.
- **Worker effects follow original commit:** `_submit` remains in the original Fleet control plane, after `FleetRunLifecycle.admit()` returns. No work starts in the transaction. Failures after admission do not roll back its durable existence or permit a second child.

The pre-admission schedule checks remain for early failure, but the final source-of-truth is the same-cut `ScheduleExecutionStore` check. No new durable reservation table, journal, signer, Fleet lease owner, scheduler, or database transaction manager.

## Hostile and crash cuts exercised

- Real SQLite `RUN_ADMITTED` + schedule row same revision, as well as original `ExecutionCoordinator.reserve_fleet_with` lease + Run + schedule row in one cut.
- Exception after provisional in-transaction publication: rollback proves zero ghost schedule bindings and zero admitted Fleet facts; repeat succeeds.
- Occurrence canceled/failed before admission cut: no Run, lease or schedule re-admission; existing version preserved.
- Duplicate and concurrent competing connections: no second committed child.
- Lost acknowledgment after commit and fresh `LocalDatabase` restart: existing RUNNING child is observed, no alternate `start_bound` call; temporarily missing Fleet read preserves binding.
- Original X0 source/AST negative mutations for missing journal in-cut join, fabricated store update, missing legacy Direct schedule identity, duplicate post-admission CAS, and inherited native Git guards.

## Limits; independent gates remain blocked

These are **hosted logical SQLite correctness tests**, not evidence for native WSL storage crash durability, fsync/ENOSPC/EIO, cross-process physical effects, independent Enforcer signer/provisioning, independent LACE quiescence or security certification. Authenticated Direct and Strict enrollment/issuer/full worker e2e have architecture coverage, not independently qualified native signer/physical execution proof. Full P3C/Gate A/B/C, AG-03/04, APP PASS and release remain blocked. The writer-bearing CO-R2b worktree retirement remains unchanged and blocked pending independent physical absence.

**Follow-up:** CO-R2c evidence acceptance lease expiry must be rechecked at the original acceptance cut. Then CO-R3 cross-provider native conformance and exact-source WSL qualification. CO-R4 replaceability remains deferred until justified.
