# ADR — CO-R3G: original cgroup observation and removal native-descriptor fencing

**Status:** implemented development checkpoint; NOT independent LACE writer-absence, authenticated Enforcer revocation, or release certification.

## Problem and authority

CO-R3F pinned the `cgroup.kill` write to an original cgroup directory handle but the subsequent `cgroup.events`/`cgroup.procs` observation and recursive `rmdir` continued through mutable paths. A foreign substitution during quiescence could look like an empty original cgroup, while a stale parent or child pathname during removal could affect the wrong resource. This must fail before completion evidence is published. Only `backend/src/oh_goon/integrations/_fleet_plan_cgroup_resources.py` owns the attempt lifecycle and legacy non-LACE orphan recovery. Journal-owned LACE reconciliation remains independent and cannot use dead-PID scavenging.

## Decision

1. `_bound_cgroup_directory` opens `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` and compares `fstat(st_dev,st_ino)` to the original creation-time identity, with the existing pathname/ancestor guard. Every wait-loop sample obtains `cgroup.events` and `cgroup.procs` from the **same directory handle** via `_read_bound_cgroup_control`, no symlink following, bounded `os.read`, and verified regular control types. Missing, malformed, overlong, inconsistent, or renamed observations fail closed.
2. The original `close()` and non-LACE orphan recovery pass the original persisted identity into `_remove_cgroup`, maintaining one execution path. Removal opens the parent directory once, pins the original target, enumerates native child directory entries from the bound descriptor, rejects symlink children and changed child inode/type, verifies the parent-relative target identity immediately before native `rmdir(name, dir_fd=parent_fd)`, and refuses acknowledgment if the path reappears immediately after removal. The existing attempt metric publication remains **after** native removal; state/receipt retention on error remains unchanged.
3. The original QENV1 CORE ring includes targeted real filesystem descriptor replacement/symlink/overlength/child substitution/reappearance tests and architecture negative mutations. The exact-source manifest selection recognizes CO-R3F and CO-R3G, so the native qualifier cannot silently use CO-R3E when a later exact checkpoint is present.

## Deliberate limits — no overclaim

- **`rmdir(name, dir_fd=parent_fd)` is still pathname-conditioned:** Linux does not offer an atomic identity-conditional `rmdir` through this interface. An adversary who can exchange a target exactly between the final validation and the syscall is not excluded by this checkpoint. `post-rmdir` reappearance detection reduces false *publication* but cannot undo a wrong deletion. Do not claim complete hostile-race safety or mark CO-R2b writer-bearing Git cleanup admissible.
- Local cgroup-v2 quiescence does not include escaped or foreign writers, FD/mount/namespace holders, network effects or independent original LACE kernel observations. Durable trust-epoch revocation, generation binding and independent complete writer custody remain separate gates.
- Test files use a hosted Linux filesystem to exercise descriptor semantics, not actual delegated cgroup-v2 kernel integration. Native cgroup-v2/WSL, real process escape/revocation, hostile replacement, abrupt crash and EIO/ENOSPC require independent exact-source evidence.
- Unpublished allocation rollback retains the existing weaker best-effort behavior and is **not** an authenticated physical teardown proof. CO-R2b, P3C, Gate A/B/C, AG-03/04, APP PASS and release remain BLOCKED.

## Invariants

**Resolve once, transport once, execute once, report once.** Only the canonical attempt resource owner may publish its effect; no second Enforcer, LACE observer, signer, scheduler, or cleanup service was introduced. A failed check prevents completion evidence and state-file retirement. An indeterminate native result does not become an `absence` fact.
