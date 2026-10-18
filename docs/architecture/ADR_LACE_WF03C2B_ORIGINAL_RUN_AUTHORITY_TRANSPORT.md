# ADR — Transport original Fleet admitted-Run context, never mint new authority

**Decision:** Keep the original Fleet journal/Run reducer as the single source for TL-1 consent facts. Preserve its already-resolved `FleetRunDetail` in `FleetExecutionAuthority` and transport that exact object across original scheduling/effect/runner boundaries. Require exact Run and Plan target matching on the claimed effect before Plan preflight. The original backend selector performs TL-1 policy-aware re-selection; process effects still require their own Enforcer/LACE port.

**Equality rule:** Run progress changes between worker activation and subsequent `RUN_STARTED` and repository transitions. Exclude the transport-only `admitted_run` field from `FleetExecutionAuthority` equality, while keeping immutable target, capacity, admission and transaction-level signer/placement evidence compared exactly as before. The original `require_execution_authority_in` and effect cuts are untouched.

**Scope:** Provide the context only for `StrictFleetExecutionAdmission`. Non-Plan and direct Fleet paths are unchanged. Never turn receipt presence into permission, impersonate a sandbox, auto-fallback, or treat missing physical custody as a success. A worker with valid Run data but without the original per-effect authority must still refuse TL-1 process creation.

**Next:** WF-03C2C: operator-interactive post-placement consent issuance in the original admission transaction, configured TL-1 executor registration with explicit opt-in, and actual Step-level effect-authority rechecks/teardown on the original worker. Qualify RUN-8 Iris only after those paths are real.
