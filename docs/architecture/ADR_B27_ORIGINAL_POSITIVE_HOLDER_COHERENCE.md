# ADR — Original positive mount-FD holder claim coherence

## Decision
The existing original Fleet `sample_recovery_physical_unqualified` call remains the **only** Run-bound read-only kernel observation/journal path. The existing shared Linux observer remains the sole kernel data collector. Before the second original signed journal cut, its already present `require_original_sample_kernel_identity` rejects an unsupported positive holder claim or inconsistent holder census transcript.

A positive FD-holder result requires a positive tracked namespace, matching exact namespace and kernel target fingerprint, one each of census fields, a positive process count, an exact signed-target count, and positive matching FDs no greater than inspected FDs. Contradictory positive evidence is rejected before mutation; it is not silently reinterpreted as absence. If the local reader could not observe a mount or holders (`UNKNOWN`, `NOT_OBSERVED`), the existing non-authorizing recording remains possible, and neither state proves teardown/absence.

## Authority
- Semantic owner: original Fleet/LACE Run recovery sample writer, under its existing authenticated journal claim and two-cut lease/head fencing.
- Kernel data owner: existing read-only `linux_physical_observer.observe_kernel` (one call).
- Writer: original Fleet journal, `RESOURCE_PHYSICAL_SAMPLE_RECORDED`, always unqualified.
- No new publication authority, signer, independent custody, separate Agent execution path, token or privilege is granted.
- Original resource envelope + Run claim and mount-target intent remain the only permitted context. The native snapshot cannot independently expand authority.

## Evidence and limitations
Hostile tests cover unsupported positive holders, incomplete or duplicated identities, foreign namespaces, zero/impossible FD/census counts and pre-journal rejection with no additional kernel scan. QENV1 requires the module under PHYSICAL_OBSERVER. This is hosted consistency evidence only. Gate B requires real effectful Bubblewrap, independent physical observer custody, complete native resource coverage and independently authenticated teardown absence. Gate A and other gates are separately blocked.
