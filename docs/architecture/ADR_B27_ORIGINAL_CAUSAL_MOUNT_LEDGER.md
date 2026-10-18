# ADR — P1/P2B1: Original Run-bound kernel mount observation candidate

Status: IMPLEMENTED AS DEVELOPMENT HARDENING; NOT GATE B CERTIFICATION.
Parent: exact P1B27_GATE_P1P2A1 candidate.

## Decision

Preserve the canonical pipeline: original signed `RESOURCE_INTENT` → original
allocation/inventory and Fleet recovery claim → single shared LACE Linux kernel
observer → original two-cut Fleet owner → immutable **unqualified** recovery
sample. No additional observer, signer, publisher, admission path or executor.

A positive mount-namespace observation MUST contain one and only one bounded
native target ledger declaration: count of visible and declared targets, no
ambiguous mounts, and a unique positive kernel mount ID for every signed target.
The original Fleet owner rejects a fabricated positive observation even when
the observer was replaced by a faulty adapter; incomplete observations still
remain read-only and unqualified. The existing shared kernel reader retains
sole responsibility for parsing `/proc/<pid>/mountinfo` and deriving kernel
mount/device/root fingerprints. This checkpoint does not duplicate that parser.

The original typed sample exposes `causal_observation_identity`, a domain-separated
SHA-256 over the verified resource envelope identity, exact observation scope,
shared snapshot identity, mounted intent identity and non-authorizing
classification. Unlike `kernel_snapshot_identity`, this candidate identity
cannot be reused across different original Run/claim/inventory/scope lineages.
It is **not** a signed physical fact or authorization token.

## Explicit limitations and next acceptance

- No live source-FD/inode → kernel mounted object correspondence is asserted.
- A native mounted source can change in place even with stable kernel mount ID;
  content/source custody needs independent evidence.
- Local `/proc` cannot prove all detached mounts, network state, hidden FDs,
  namespace references or physical absence.
- LACE physical-fact signing with independent custody is NOT implemented here.
  The already established Enforcer/journal semantics remain authoritative.
- Native effectful Bubblewrap qualification of this **exact source** and
  independent P3 crash/cancel/revocation/surviving-holder teardown evidence
  are required before Gate B can be considered.
- Gate A custody, AG-03/04 egress, Gate C, APP PASS and release remain blocked.

This prepares a causally bound candidate for the *existing original physical
fact owner* once native custody and independent attestation are available.
Do not introduce a second physical publisher or infer Gate B from hosted tests.
