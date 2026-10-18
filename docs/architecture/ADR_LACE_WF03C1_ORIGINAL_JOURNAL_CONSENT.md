# ADR — LACE-WF-03C1: consent is existing Run admission, not parallel state

**Decision:** Extend only the original typed `StrictFleetExecutionAdmission` with an optional TL-1 consent fact, carried inside the original `RUN_ADMITTED` and immutable original journal projection. No separate consent store, security token issuer, Step worker, executable backend selection or retry policy.

**Rationale:** An interactive TL-1 permission must be bound to exact Plan source and executor at the original Fleet admission cut; an in-memory or sidecar consent would disappear or diverge on restart. Model-only transport permits positive and negative SQLite tests without declaring physical effect authority. Consent is data, not authorization to execute: original worker/Enforcer/LACE still must verify contemporaneous authority at the actual launch.

**Strict compatibility:** no consent → original strict JSON and exact selection unchanged. With consent → only matching local backend/identity, single manual repository and actual canonical Plan execution digest; never unattended, untrusted, read-only strict workspace, restricted egress or nested strict. Recheck after model-copy bypass at original event validator.

**Deferred:** operator review/issue after resolved Plan and exact placement, original Fleet control-plane admission API, continuous worker effect-time authorization and revocation, deliberate inventory opt-in, real Iris and restart/cancellation qualification. Not release.
