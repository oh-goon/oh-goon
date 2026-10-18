# SKILL-3C2D2C2B — original staged-descendant seal and FD revalidation

**Status:** development-only; Agent native execution **DENIED**. **Owner:** original Fleet staging and original LACE bind-source FD lease.

## Problem

The Agent's staged source directory was held by an `O_PATH` FD and mounted read-only in the planned Bubblewrap roster, but the FD pinned only the *directory inode*. Its descendants were still owner-writable after the original byte-exact readback. Read-only sandbox mounts do not prevent a same-UID host process from changing a source tree before the mount is consumed.

## Changes in canonical owners

1. `FleetPlanStepAttempt._stage_agent_prework_bytes` seals individually verified files to `0400`, seals directories bottom-up to `0500`, fsyncs the sealed stage, and retains the original non-executable receipt format and digest.
2. The existing `_inspect_agent_prework_readonly_mount` checks those precise file and directory modes while validating the original immutable source payload and source root ancestry. No new manifest, archive, signer, or authority is created.
3. `OriginalBindSourceLease` walks the **same pinned staged-source FD** with no symlink following, bounded entries/bytes, single-linked regular files and the owner/mode constraints above. It fingerprints descendant bytes, names, inode identity, size, timestamps and directory metadata. The existing `verify_kernel_custody` repeats this walk before each native FD-argument consumption and fails on drift. Failures close the lease.
4. All other source mounts retain their previous behavior. The signed grant, request-to-attempt correspondence, target namespace protection, revocation fences and downstream `RESOURCE_INTENT` denial are unchanged.

## Safety boundary

This is a POSIX discretionary *seal* plus a bounded **detective check**, **not** immutable descendant custody. A hostile process acting as the same owner or as root can chmod, replace, remount, or alter descendants after the final verification, even if an O_PATH root FD survives. Rechecking cannot make a sequence of reads and process launch one atomic kernel operation; data may be changed and restored after the last check. No sandbox execution can be admitted solely on this checkpoint.

A future positive Agent `RESOURCE_INTENT` grant requires independently protected, immutable source descendants (e.g. a trusted immutable snapshot/filesystem or equivalent kernel-enforced exclusive writer custody), whole-process/mount/FD confinement, safe revocation, cgroup and network isolation, and provable teardown on the actual target host. No duplicate LACE path and no promotion of prework receipts to grants.

## Verification

Focused adversarial regressions include modified bytes, restored identical bytes, added and swapped descendants, symlink/hardlink substitution, permission regression, and stage seal inspection. Existing FD-hand-off, signed grant, source roster, Agent architecture and command-Step tests remain in the qualification ring. The historical mount-order mutation-test failure is recorded separately and is not attributed to this change. No native WSL or physical containment certification is claimed.
