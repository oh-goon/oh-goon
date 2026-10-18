# ADR — CO-R3F: identity-bound original cgroup kill cut

**Status:** Development; not independent containment or release certification.

## Problem

CO-R3E persisted original cgroup directory identity, but its original native `cgroup.kill` operation reopened the path after identity checks. A hostile replacement between validation and write could redirect the destructive control operation to a foreign resource. A missing `cgroup.kill` could silently fall back to PID SIGKILL, which does not preserve the identity-bound cgroup target.

## Decision

Keep **one original owner**, `backend/src/oh_goon/integrations/_fleet_plan_cgroup_resources.py`, and the same `CgroupV2AttemptLease.close()` and non-LACE orphan-recovery paths. Transport the original creation-time `(st_dev, st_ino)` into the existing `_kill_cgroup()` operation. It opens a native directory handle with `O_DIRECTORY | O_NOFOLLOW`, validates `fstat` against the expected identity, opens `cgroup.kill` relative to that handle using `dir_fd`, rejects a symlink/nonregular file, and issues the exact write using that control descriptor. Any unavailable/mismatched identity or native write uncertainty fails closed; no PID-based fallback when the durable identity is supplied.

A swapped pathname after descriptor pinning cannot redirect the kernel control write to the replacement. If the swap is detected before write, reject; if it occurs precisely at the write, only the pinned old resource can receive the effect. All subsequent checks and completion proof still follow the original owner, with no new Enforcer, LACE publisher, signer or rollback engine.

## Explicit limits

1. Scoped physical effect correctness is not proof of independent physical writer absence.
2. `_wait_for_cgroup_empty()` and recursive `_remove_cgroup()` still use pathnames. They remain unqualified against fully hostile races. A successful `cgroup.kill` does not entitle completion/cleanup if downstream facts are uncertain.
3. Legacy **unpublished allocation rollback** retains best-effort cleanup; no durable receipt or independent writer absence may be inferred from it.
4. The LACE journal-owned orphan path remains excluded from PID scavenging. Authenticated Enforcer revocation and generation-bound LACE observations need their own tests and authority.
5. Actual cgroup-v2 kernel behavior, hostile replacement on native WSL, escaped/foreign writer/mount/FD/namespace containment, abrupt host-stop, EIO/ENOSPC and eligible WAL are not established by hosted filesystem fixtures.

CO-R2b writer-bearing Git release, P3C, Gate A/B/C, APP PASS and release remain blocked.
