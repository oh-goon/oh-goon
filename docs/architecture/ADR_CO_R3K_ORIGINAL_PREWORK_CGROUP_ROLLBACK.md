# ADR CO-R3K — Identity-pinned prework cgroup limits and rollback

**Decision:** Development implemented, NOT RELEASE. **Parent:** CO-R3J exact source SHA-256 `ba86f54225307e7f549f4aa05b35caf9071f03f27641117b7609b3a21ac3a73e`.

## Threat and original owner

The same Fleet cgroup attempt owner already pins the exact cgroup identity at allocation and requires identity-bound kill/removal for normal close and recovery. Its **open failure branch** had been overlooked: unbound `_kill_cgroup(cgroup)` could fall back to PID kills, and unbound `_remove_cgroup(cgroup)` could reinterpret a replaced pathname. The allocation step also wrote CPU/memory/PID controls by pathname and persisted original recovery identity **after** those controller effects. A prework failure therefore had an unsafe cleanup and a limited recovery window.

## Decision

Maintain **one existing `CgroupV2AttemptLease.open` owner**, original recovery owner, original locks, original native primitives, and original QENV1 CORE. After `mkdir`:

1. Pin creation-time device/inode; if unreadable, refuse all destructive rollback. No re-derived identity from a substituted pathname.
2. Persist the original lease metadata, including the existing signed/journal-bound `lace_intent` when supplied, **before** controller limit changes or nested delegation setup. This is recovery bookkeeping, not permission to certify physical absence.
3. Apply ordinary CPU, memory, swap, and PID limits through an identity-checked original directory FD; open each kernel control `O_NOFOLLOW` relative to that FD and reject unavailable/non-regular controls, incomplete writes, or changed original path. Do not silently fall back to pathname writes.
4. On any initialization failure with pinned identity, verify original path and perform the **same original identity-bound kill, kernel-population wait, and remove**. Remove the persisted lease metadata only once all three steps succeed. If any is uncertain, retain the record and fail closed. Preserve the initial failure if fully rolled back.

No new agent, Enforcer, LACE, process supervisor, alternate cleanup authority, configuration precedence, or diagnostic runner is created.

## Verified negative controls

Tests demonstrate: recovery record precedes the first controller effect; unknown identity refuses unbound kill and remove; failed initialization invokes the bound native owner exactly once; changed path leaves foreign cgroup untouched and original metadata intact; failed termination retains the identity-bound recovery record; symlinked controller file is not followed; short kernel write fails; and substitution after opening the directory cannot redirect a controller write. Original X0 AST mutation guards and QENV1 CORE enrollment reject bypass.

## Strict limitations and future work

The tests use real local directory descriptors with simulated cgroup control files, not privileged cgroup-v2. Original nested delegation setup still contains pathname-based controller/subtree operations and requires its own handle-bound review. There remains a possible crash window between kernel `mkdir` and successful durable recovery-record publication, because those effects cannot be committed atomically across kernel/filesystem boundaries. This checkpoint does **not** prove independent, authenticated, generation-bound parent mutation custody or complete LACE workspace-writer absence. Linux `rmdir(name, dir_fd=...)` is not identity-conditional. Hostile cgroup rename at the exact destructive syscall, independent Enforcer signer/trust revoke, WSL/native cgroup-v2 qualification, CO-R2b writer-bearing Git release, P3C, Gate A/B/C, APP PASS, and release all remain **BLOCKED**.

Never promote a local successful teardown, lock, or metadata record into independently authenticated kernel custody or workspace absence.
