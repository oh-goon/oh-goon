# ADR — EXEC-01B2B1: standard owned-process cleanup belongs to the existing supervisor

## Decision

Ordinary Linux/WSL workflow execution must terminate and account for its own
process group under deadline, cancellation, and leader-exit-with-child conditions.
Exercise the existing `oh_goon.engine.command.run_command` and its process-tree
termination owner with real subprocesses; do not introduce a second controller.

## Evidence boundary

The added regression confirms functional process-group cleanup on the hosted
Linux environment. It is **not** a high-security host isolation qualification,
mount/FD custody audit, or proof about processes that deliberately escape the
owned group. WSL must be tested independently. The installed-Hashmarks native
FleetRun ring remains mandatory for claiming the complete native functional
qualification, and must not be replaced by a stub.

## Non-drift

Execution first. Security proportional to the workload. Keep advanced host
certification and privileged mount/FD custody work deferred. No additional
signers, backend implementations, or admission gates are authorized here.
