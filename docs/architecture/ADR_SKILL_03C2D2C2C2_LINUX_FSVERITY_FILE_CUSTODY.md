# ADR — Linux fs-verity at the original Agent source boundary

## Problem

The previous `0400/0500` discretionary seals and the original `OriginalBindSourceLease` FD descendant recheck detect source drift but cannot stop a same-UID file writer immediately after the check. A directory FD pins only the directory inode, not all descendant file content.

## Decision

Add the narrow, explicit `fs-verity` kernel file-byte protection capability to the **existing** Fleet prework stager, with the exact same original LACE mount-FD owner measuring each file before consuming native arguments. Use Linux `FS_IOC_ENABLE_VERITY` with version 1 / SHA-256 after all file writers close, and `FS_IOC_MEASURE_VERITY` on the originally opened regular-file FDs. Both operations are fail-closed when explicitly requested. Never equate a userspace hash with successful kernel measurement; retain the original signed prework receipt semantics and the Agent launch refusals.

The option is deliberately *not* selected from Plan or Agent-authored files; only original lifecycle code can request it. No new signer, interpreter, database, policy owner, recipient, network call, dependency, or executor is added.

## Constraints and residual risk

This is a real Linux filesystem capability, not an emulation. It depends on the mounted filesystem's fs-verity support, kernel configuration and permissions. Failure to enable or measure is a refusal, never an implicit downgrade. `fs-verity` protects **file data**, but does not prevent untrusted writers from replacing filenames, directories, or using different inodes; final pathname/FD handoff, independent privileged staging ownership and protected mount namespace still require native qualification. This checkpoint makes no positive physical Agent release claim.

## Acceptance criteria

1. Verify Linux UAPI ioctl ABI, requested hash algorithm, expected 32-byte measurement and refusal on unexpected digest algorithm.
2. Real staging opt-in operates after byte-exact readback and before publishing the original no-effect receipt; failure leaves a non-reusable incomplete stage.
3. LACE opt-in requires kernel measurement at FD pin and every existing pre-consumption verification; it never calls an alternate launcher.
4. Unsupported filesystems and insufficient privileges fail closed; native positive test skips rather than invents a pass.
5. Existing default staging, signed grants, mount-target refusal and production Agent execution blockage remain unchanged.

## Explicit follow-on

Independently enforced *directory entry* writer custody and a real Linux/WSL positive native witness, then original Enforcer grant/RESOURCE_INTENT consumption, physical process confinement, revocation, and provable teardown. No extra authoring or signing layer.
