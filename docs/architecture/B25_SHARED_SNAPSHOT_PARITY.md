# B25 — shared kernel snapshot identity and read-only projection parity

**Parent:** exact `OH_GOON_B24_CANDIDATE.zip` (SHA-256 `dba8b04c1b8dcc1123dd917a1b1a8d2a79199b9a785f21717f9b455ab8a02c80`).
**Authority:** hosted development only. This checkpoint is a bounded B12E-C3 parity slice, not physical Gate B or independently provisioned Enforcer admission.

## Implemented

- One immutable, validated `PhysicalKernelSnapshot` owns the only canonical, domain-separated `shared-kernel-snapshot/v1` identity. Every component name/state/detail and the ordered tuple are included in the canonical digest. The `PhysicalKernelFact` rejects unknown states, controls and over-budget detail; `PhysicalKernelSnapshot` rejects empty, duplicate, invalid or oversized component sets.
- The existing operator-scoped **diagnostic** projects the existing `observe_kernel` return as `observations` plus `kernel_snapshot_identity`, explicitly `DEVELOPMENT_DIAGNOSTIC_ONLY`.
- The existing original Fleet recovery-owner sampler continues the B24 signed intent/claim/allocation/inventory envelope, two authority cuts and **one** shared Linux observer invocation. `OriginalRecoveryPhysicalSample.kernel_snapshot_identity` is a read-only projection of its original `facts.identity`. There is **no** new sampler, reader, publisher, journal fact, recovery executor or policy classifier.
- New parity tests compare identical injected kernel facts returned through diagnostic and original-owner projections, including exact identities and no original journal changes. Negative tests reject malformed facts, duplicate/over-budget inventories, identity substitution, authority inflation, and X0 bypasses.
- The supervised native PHYSICAL_OBSERVER ring is extended to require this exact B25 regression module whenever `.checkpoint/B25_SOURCE.sha256` is present; this is a qualification *requirement*, not a claim of a completed B25 WSL run.

## Independent review of earlier WSL evidence

Uploaded `OH_GOON_NATIVE_EVIDENCE-run-005.zip` is bound to the **B23** source (not B24/B25), source snapshot `973b7a5b2fbe86aadaff170634aae94c64ad02bb46ee2b9663d4442aff413f4f`. Evidence 41/41 process groups PASS: CORE 117, B6 process 10×6, B6 rollback 10×1, B7 races 10×4, PHYSICAL_OBSERVER 10×151 = **1,737** executed test cases, 0 failures/errors/skips; 169/169 listed evidence hashes match after resolving the archived original absolute paths to their exact archived basenames, and pre/post source inventories equal. No observed lingering processes in 41 group receipts. Classification is explicitly DEVELOPMENT_ONLY and cannot be inherited by B24/B25. The proof does not validate original Fleet external signer/six OS-independent roots or post-cleanup independent physical absence.

## Still blocked

The B24 `UNVERIFIED_NATIVE_CLASSES` remain unchanged: actual Bubblewrap/host-tool/detached mounts, foreign mount FD handles, all namespace holders, network kernel obligations, all descendants, and independently OS-owned observer custody. A deterministic hash is **not** external witness testimony, containment, or proof of nonexistence.

**Next:** complete original Run-created physical allocation inventory, bind every OS handle to the original Enforcer-issued source and independently witnessed observer identity; then C4 original journal publication/classification, C5 native post-cleanup convergence, and C6 global single-path enforcement. Preserve independent A-NATIVE, Gate B local/remote, Gate C, Q0, APP PASS and release gates.
