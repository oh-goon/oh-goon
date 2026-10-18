# ADR LACE-WF-01 — Policy-specific demand, invariant native-effect custody

**Status:** Adopted as *demand-only* development contract. Does not authorize execution.

## Decision

The canonical `IsolationRequirements` remains the one strict CI/CD backend
capability comparator. It receives a typed boolean trust policy or typed threat
model, not a truthy string. A new frozen `LaceRequiredGuarantees` view belongs
to the **existing** `fleet_plan_execution_policy` model; it derives demand from
that one policy and delegates isolation compilation to that same original
`isolation_requirements` function. Purpose-specific profiles not yet defined
must remain explicitly unresolved. This avoids inventing a second policy parser.

No existing caller is required to consume the projection until WF-03 binds its
requirements to real backend checks. It is **not** a backdoor to accept a
`trusted_repository` program for raw local execution.

Kernel-native mutation custody is conditional on *actual physical effects*, not
on blanket machine inspection: when a backend performs native cgroup, mount,
network, process-control or teardown mutations, only the original effect owner
can authorize them after independent OS-level custody. This obligation cannot
be disabled by a workload flag or deferred to Game Tape.

## Rejected alternatives

- Universal hardware/cgroup demand for every authoring or inspection operation.
- Default `trusted-local` / uncontained fallback to allow WSL execution.
- Treat a missing profile as zero required guarantees.
- Duplicate LACE, signer, resolver, validator, capability issuer, or cleanup owner.
- Accept `IsolationThreatModel` by Python truthiness (found downgrade defect).

## Forward dependency

WF-02 must explicitly review and approve any trusted-local policy. WF-03 then
connects the approved typed requirements through the existing backends. No
physical qualification is implied by the development contract.
