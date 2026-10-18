# ADR — Original admitted Fleet Run owns TL-1 worker backend selection

**Decision:** Make the existing `select_bound_process_isolation_backend` policy-aware only with an explicit `FleetRunDetail` from the original journal; preserve strict selection otherwise. The same existing backend implementation is selected by exact `PlanIsolationBackendEvidence`; no second executor or backend fallback is introduced.

**Why:** WF-03C1 persists immutable TL-1 operator-consent facts in `RUN_ADMITTED`; WF-03B can supervise a real host process but has no production authority bridge. The original strict selector would reject trusted-local's truthful ambient-host capabilities. Rather than globally weakening strict capability checks, this checkpoint only switches to consent-specific checks when original immutable Run-detail facts agree with the frozen Plan, exact placement, explicit repository scope and source.

**Invariant:** Admission evidence is necessary but insufficient. Real process launch still requires worker-supplied original effect-time authority; a caller cannot convert a standalone consent into execution rights or claim a host process is physically sandboxed.

**Deferrals:** Fleet interactive issuance after exact selection; transport of read Run detail through worker scheduler and Step request; original Enforcer/LACE effect gate; production inventory enablement; real Iris, Composite, Plan and recovery qualification. Those cannot be inferred from unit tests or a policy approval alone.
