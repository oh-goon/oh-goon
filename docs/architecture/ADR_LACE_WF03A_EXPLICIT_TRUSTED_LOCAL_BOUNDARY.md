# ADR: WF-03A — Explicit trusted-local eligibility without implicit authority

**Status:** IMPLEMENTED PARTIAL, NOT ENABLED. Human TL-1 policy approval recorded; runtime authorization/launch remains open.

## Original owners

The source of meaning is the resolved Plan's original `CicdExecutionPolicy` and the original `isolation_requirements` compiler. `require_policy_aware_lace_backend` compares a selected backend's true capabilities with the original strict requirements by default. The original `select_bound_process_isolation_backend` still rechecks exact admitted identity, strict capabilities and availability. The new eligibility record is never substituted for a Fleet admission or a LACE OS custody proof.

`TrustedLocalRunConsent` describes one interactive operator authorization **in principle**. Its fields bind Run ID, immutable execution digest, backend ID and configuration, executor ID and generation, and accurately state ambient host access. Merely constructing this public Pydantic object does not establish operator presence; only the original Run issuer can eventually create and transport actual authority. The policy adapter requires all provenance facts explicitly and refuses untrusted, capped, parallel, scheduled, unscoped, remote or falsely sandboxed combinations.

## Rejected shortcuts

- Treating `trust_repository_code=True` as host permission.
- Setting an environment toggle and silently replacing the strict backend.
- Using a fabricated approval model as an executable token.
- Calling Bubblewrap a bare-host executor, weakening its native guarantees, or bypassing the Fleet executor.
- Claiming no sandbox restrictions while still advertising them as present.

## Explicit remaining WF-03 contract

Implement admission/worker consent transport with original authority, truthful local backend capability, effect-specific LACE resource checks and actual execution/cancellation/teardown. Exact operator-authenticated Run identity must survive all transitions, including restart/recovery, without replay authorization. These changes are not present in WF-03A. No RUN-8 Iris claim is possible from this qualification alone.
