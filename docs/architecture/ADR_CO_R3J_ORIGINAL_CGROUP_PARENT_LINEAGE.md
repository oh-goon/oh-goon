# ADR CO-R3J — Original cgroup parent lineage as a fail-closed ACK fence

**Decision date:** 2026-10-15  
**Status:** Development implemented, partial, NOT RELEASE.  
**Parent:** CO-R3I complete source SHA-256 `f486061c22226993ace5bb2e02cee63d248f5e22b0e2fee1fafcd2f21afffe53`.

## Problem and authority

The original Fleet cgroup owner already pins the target cgroup inode/device and
performs handle-relative kill, observation, removal, and original-inode
post-removal acknowledgement. The `rmdir(name, dir_fd=parent_fd)` syscall
cannot atomically require that the name still denotes the original inode.
The parent's *own* pathname might also have been displaced while its opened
file descriptor remains valid. Name-only post-checks would miss parent-lineage
replacements.

## Decision

Continue using only the existing `_remove_cgroup` owner. For each recursive
removal, pin the original parent device/inode from its opened descriptor;
compare the descriptor with a no-symlink-follow stat of its original path;
repeat that comparison immediately before and after the native `rmdir` cut.
Unobservable or changed lineage rejects retirement ACK and prevents ordinary
completion/resource evidence, and LACE cleanup records remain in recovery.
The original target-inode post-cut link-count fence stays mandatory.

No new Enforcer, LACE, process supervisor, filesystem-lock authority,
trust/signing service, or cleanup implementation is introduced.

## Negative controls

1. Unchanged parent supports normal local removal.
2. Parent displaced before destructive cut refuses native removal.
3. Parent displaced during native `rmdir` refuses completion, even when original
   effect happened through the pinned descriptor.
4. Missing post-cut parent observation refuses ACK.
5. Original X0/QENV1 checks reject removal of this protection and CORE tests.

## Strict limitations

This is a *post/pre-cut identity evidence fence*, not externally authenticated
exclusive mutation custody. It cannot roll back a native syscall. The parent
identity is acquired in the current removal operation, not a signer-bound
physical generation lease. An adversary able to replace a child at the exact
`rmdir` syscall remains capable of forcing wrong-target deletion, which later
checks may only detect. No independent cgroup-v2, privilege, mount, namespace,
writer-FD, escaped-writer, power-loss, or host-native WSL certification is
claimed. Local test filesystems do not prove cgroupfs kernel semantics.

Writer-bearing CO-R2b Git retirement, independently signed Enforcer revocation,
independent LACE writer absence, Gate A/B/C, APP PASS and release remain
**BLOCKED**. A future provider must demonstrate kernel-enforced parent mutation
custody bound to original run/step/trust generation before privileged destructive
certification. Without that proof, fail closed. Do not substitute a local lock
or fabricate a physical absence receipt.
