# B24 — signed original Run resource envelope (development, not Gate B)

## Authority and source

B24 builds **on the exact UX-D authoring source**. It changes no template grammar, runner,
Fleet admission, native effects, recovery journal writer, Enforcer classifier or Game Tape
projection. It is a focused B12E-C2 improvement implemented inside the **existing**
`OriginalFleetLocalLaceRecoveryOwner.sample_recovery_physical_unqualified()` and
`require_original_physical_sampling_in()` chain. It does not create a parallel reader.

## What changed

The one original Fleet owner, after verifying provisioned admission lineage, lease,
signer, original signed `RESOURCE_INTENT`, claimed inventory, native allocation and
mount descriptor, now constructs an immutable `OriginalRunPhysicalEnvelope`. The
envelope records independently domain-separated identities of three original
journal-verified ancestors: signed intent event, allocation observation, and
original orphan inventory event. It also binds the original native record identity,
claim epoch, subject, Run, mount intent digest and exact declared target order.

The exact envelope is transported through both original journal transactions.
The one shared Linux observer still executes **once**, between those transactions.
Head/epoch/subject/envelope mismatch fails closed; the sample carries only the
original envelope and immutable physical facts. It cannot grant effects, signed
physical publication, quiescence, teardown or terminal classification.

Even a match to the complete *declared* mount plan does **not** enumerate real
host-tool/runtime/detached mounts, surviving namespace and FD holders, kernel-held
resources or other runtime allocations. `coverage=INCOMPLETE_NATIVE_RESOURCE_SCOPE`
and `observer_custody=UNVERIFIED` are non-overridable. A read-only `NOT_OBSERVED`
remains scoped non-detection, never physical absence.

## Rejection and evidence

- Reject mismatched inventory intent/epoch/subject, forged native record identity,
  allocation versus inventory lineage, signed mount digest, caller target scope,
  signer's generation, lease/cancel and all original negative cases **before kernel read**.
- Reject scope/journal drift after the kernel scan and guarantee no physical-fact
  journal append or extra second scanner.
- X0 guards the exact single envelope constructor and transport into the original
  sample. Native WSL PHYSICAL_OBSERVER ring now requires B24's regression module.
- The packaged hosted tests only prove the above developmental properties; they
  cannot prove A-NATIVE, independent witness custody, physical completeness,
  Gate B, APP PASS or release.

## Next coherent implementation boundary

B12E-C2 continuation must obtain an actual original executor-owned native resource
inventory and independently witnessed custody for **all** allocations (Bubblewrap
mount/FD/namespace holders, host tools, network, cgroups, descendants and detached
resources), not a caller-provided hint. Then C3 thin diagnostic parity and C4 original
journal/Enforcer only fact publisher can be bundled if the same authority/validation
cut is reused. C5 real bounded post-cleanup physical convergence and C6 anti-duplicate
call-graph/negative witnesses remain gated by native WSL and independently provisioned
original issuer/roots. Never promote B24 digest equivalence to absence.

The fixed `unverified_resource_classes` list explicitly names all eight coverage
holes; no caller can suppress it or change its digest while claiming B24 identity.
