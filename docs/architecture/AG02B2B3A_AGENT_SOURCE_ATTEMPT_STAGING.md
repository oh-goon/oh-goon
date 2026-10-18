# AG-02B2B3A — original attempt-owned source staging (development only)

## Decision

Extend the **existing** `FleetRunLifecycle.reserve_original_agent_source_for_attempt`
with the optional original `FleetPlanStepAttempt` source staging capability. There is
no second agent runtime, launcher, scheduler, admission, transaction, journal or
source decoder. The AG-02B2A packet is decoded once in the existing source service.
The original `ExecutionSession.mutate()` contains the reservation, prework staging,
revalidation and signed original-journal receipt in its **same write cut**.

The one `FleetPlanStepAttempt` storage owner writes bytes under its established
private attempt root, rejecting symlinked/nonprivate roots, duplicate/partial
staging, unsafe members, changed roots, file substitution and readback mismatch.
Source paths from the repository are never reopened. The original Run journal
stores only the source commitment and stage hash/count, not source bytes.

## Failure semantics

- Prework storage writes are physical *preparation* effects, **not** Agent Step
  execution or authority to use LLMs, tools, filesystems, network or secrets.
- Any aborted/cancelled/stale transaction fails and cannot publish a signed stage;
  orphan prework bytes may remain after a crash and require the **original**
  attempt cleanup/recovery. They can never be reused as a valid staging receipt.
- A successful local file readback is **not** independent observation, not an
  immutable read-only mount, not physical Bubblewrap custody, not complete B27
  resource inventory, not quiescence or teardown proof. The private directory is
  host-side stage storage. No Agent Step consumes this path yet.
- The `AGENT_SOURCE_PREWORK_STAGED` event is authenticated by the original journal
  issuer with its own domain and refers to the exact prior signed reservation.
  It is evidence of prework staging only (`effect_permission=NONE`,
  `physical_sandbox_proven=false`). It cannot authorize external model or MCP
  disclosure, terminal classification, or agent launch.

## Next acceptance boundary

AG-02B2B3B must connect this attempt-owned stage to the existing LACE physical
sandbox via an original Run/Step-bound **read-only mount**, after independently
witnessed real resource ownership, before any agent instruction can run. It must
revalidate byte identity at actual consumption, handle crash/recovery orphan
cleanup and enforce AG-03/04 egress/data-disclosure policies. Native WSL,
independent Gate A issuer/roots, local/remote Gate B, Gate C and APP PASS are
separate qualifications; none is satisfied by hosted tests.
