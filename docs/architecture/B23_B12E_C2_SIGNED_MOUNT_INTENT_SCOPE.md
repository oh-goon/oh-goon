# B23 — original signed mount-intent scope binding (development-only)

## Ownership decision

One original Fleet recovery owner (`OriginalFleetLocalLaceRecoveryOwner`) performs
**one** invocation of `integrations/linux_physical_observer.observe_kernel()`.
The observer never signs, writes the causal journal, calls cleanup, admits a
Step, or makes a quiescence/teardown decision. The operator CLI remains separate
and cannot enter this original authority chain.

## Exact existing authorization used

The original signed `RESOURCE_INTENT.resources.mounts` is a domain-separated
SHA-256 commitment to a *prepared request's* `sandbox_bindings` and `host_tools`.
B23's frozen `OriginalMountIntentDescriptor` encodes those exact original
source, target, content identities and host-tool descriptors using the same
`oh-goon/b12c/mounts/v1` domain. The existing recovery sampling write cut
requires exact equality to the journal-verified original intent commitment,
and verifies the requested sandbox targets equal the frozen descriptor targets.
This is repeated after observation in the same original-owner trust path.

### Limits

- The descriptor may be provided by a caller but only matches when its digest
  equals the signed original intent; a caller cannot invent a different target.
- A signed *mount plan* does **not** certify that a mount physically existed,
  all Bubblewrap/runtime mounts have been inventoried, or that a detached mount,
  external namespace or kernel-held reference is absent. Host-tool target
  mapping is not inferable from the original `host_tools` commitment alone.
- The signed plan's sandbox target path is not necessarily a host mountinfo
  target. Kernel observations remain scoped and `NOT_OBSERVED` remains **not**
  physical `ABSENT`. No gate or signed physical-fact publication results.
- Original issuer provisioning, six independently custodied verification roots,
  independently owned physical observer and real native absence proof remain
  separate **blocked** conditions.

## Regression and X0 contract

- Reject foreign source, target, digest, host tools, incomplete/extra target,
  ambiguous descriptor, invalid path or malformed hash *before* kernel access.
- Verify equal original signed plan, one shared kernel read, double owner fencing,
  read-only output with unqualified physical truths and no journal side effects.
- Static X0 owner guard rejects deletion/inversion of the signed mount-intent
  comparison. Existing guard retains single kernel reader and diagnostic
  authority isolation. B23 module explicitly selected in the native WSL ring.

## Next independent step

Authenticate a **complete** original Run-owned physical resource envelope,
including runtime/host-tool/detached mount resources, executor generation and
observer custody. Only a complete and independent native physical census may
become original-LACE facts and be classified once by Enforcer. Do not create
another observer, journal publisher or semantic redecision owner.
