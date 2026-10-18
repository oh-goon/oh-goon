# ADR — Original Fleet journal owns unqualified physical observation custody

**Checkpoint:** P1B27_GATE_P1P2B2B (2026-10-14). **Decision:** Accepted for development; Gate B and release **BLOCKED**.

## Problem

Original Fleet recovered a signed `RESOURCE_INTENT` / allocation / orphan inventory and sampled one shared Linux kernel observer. It returned a Run-bound `OriginalRecoveryPhysicalSample` after two fences but never stored the physical sample in the existing causal journal. Thus the exact read could be lost at a crash boundary and was unavailable to authenticated journal readers. Adding a second physical observer, signer or journal would violate the canonical ownership boundary.

## Decision and sole authority

In `OriginalFleetLocalLaceRecoveryOwner.sample_recovery_physical_unqualified`, retain the first current-epoch/lease/issuer/claim validation cut and one shared Linux observer call. Construct one typed immutable sample. In the original **second** `session.mutate` recovery cut, revalidate the full signed lineage and exact predecessor journal head, then append `RESOURCE_PHYSICAL_SAMPLE_RECORDED` using the **existing** `CausalRunJournal._append_locked` writer. If any Run/claim, lease, journal, cancellation or native positive-identity check changes, no sample is appended. The original `CausalRunJournal.verify` validates the bounded ordered kernel transcript, scope and sample identity, original recovery claim/inventory lineage, replay exclusion and every non-authorizing field.

The journal is **not** an independently provisioned physical-fact signer. Journal hash-chain consistency and an authenticated admission root bind the original owner and ordering; they do not establish that an independent custodian observed the kernel or that every resource was enumerated. The sample remains `ORIGINAL_OWNER_READ_ONLY_UNQUALIFIED`, `observer_custody=UNVERIFIED`, `coverage=INCOMPLETE_NATIVE_RESOURCE_SCOPE`, `effect_permission=NONE`; physical absence, quiescence and teardown are all `false`. Positive and `UNKNOWN` / scoped `NOT_OBSERVED` observations are recorded with their original states; none proves absence. An external authority must not accept this event as Gate B, APP PASS or permission to execute Agent effects.

## Boundaries

- **Meaning:** Resolver and original signed `RESOURCE_INTENT`; frozen before any effect.
- **Admission and trust:** existing Enforcer issuer/generation and original Fleet control epoch/lease.
- **Physical protection:** existing LACE runtime, one shared kernel observer mechanism, no diagnostic CLI.
- **Sole journal writer:** original Fleet-owned SQLite transaction; no second publisher, signer, scanner or journal.
- **Observed data:** one typed original snapshot with exact kernel component ordering and scope identity, an original journal-verified native envelope and sample digest.
- **Negative authorization:** no observer custody proof, physical absence, teardown, Gate B, cleanup permit or release effect.

## Acceptance and attacks

The existing WSL PHYSICAL_OBSERVER ring selects the dedicated regression. Prove no journal write after a stale lease, changed head, forged positive mount record, cancellation or observer fault. Reject duplicate sample replay, foreign claims, modified scope, reordered/missing kernel components and any attempt to turn `UNKNOWN` into proven absence. Verify the existing X0 and QENV1 guards against bypassing the original cut, writer or module selection.

## Still necessary (separate gates)

1. **P1/P2B2 remainder:** real effectful Bubblewrap kernel mount/source FD provenance tied to authenticated original Run/Step and independently held/verified physical evidence, using the same original production path. This local journal receipt is NOT an independent physical fact.
2. **P3:** native crash, cancellation, trust revocation, orphan and surviving namespace/FD holders; bounded convergence and independent absence proof.
3. **Trust T0B2/T1/T2 Gate A;** Agent AG-03/04 disclosure and egress; Gate C outcome/terminal proof; APP PASS and release remain BLOCKED. Every gate must use its exact source, host and authenticator evidence, not inherited older green tests.
