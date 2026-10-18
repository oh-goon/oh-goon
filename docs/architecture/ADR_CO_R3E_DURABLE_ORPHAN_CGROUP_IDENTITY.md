# ADR — CO-R3E: original legacy cgroup orphan recovery must carry native creation identity

**Status:** Development implementation, not release or Gate B.

## Problem

The original `CgroupV2AttemptLease` pinned `(st_dev, st_ino)` in memory for `close()`, but `_write_resource_lease()` persisted only PID, process birth and cgroup name. Its legacy non-LACE `_reconcile_stale_cgroups()` used the stale PID/name to issue `_kill_cgroup()` and `_remove_cgroup()` without verifying that the target directory was the original resource. After process death or a path substitution, name-based cleanup could act on a foreign resource.

## Decision — one owner and one route

**Owner:** `backend/src/oh_goon/integrations/_fleet_plan_cgroup_resources.py`, existing Fleet attempt cgroup lifecycle, original state file and `_resource_lock`. No new scavenger, observer, signer, LACE publisher or execution path.

- The original allocation pins a cgroup device/inode before effects. `_write_resource_lease` requires that original identity and persists it alongside the existing PID/birth/name record. It refuses to write an unpinned lease.
- A stale **non-LACE** record must provide two strictly positive, nonboolean integer identity fields. Older records without them are quarantined, not auto-migrated through guessed names.
- Before kill, revalidate exact original directory identity (including symlink rejection). After kill, the existing bounded kernel `cgroup.events` and `cgroup.procs` checks must establish scoped emptiness; revalidate identity before native removal. Completion metadata is retired only after removal.
- Lost ACK after native directory removal: if the pinned record remains but the original cgroup path is absent (not a symlink), retire only the legacy bookkeeping record. No physical-absence receipt, LACE fact, or workspace-release authority is inferred.
- Original journal-owned `lace_intent` leases still **never** run PID scavenging. They require their original journal recovery claim and independent physical LACE gates.

## Negative evidence and limits

Regressions cover missing/old/malformed identity, booleans, cross-identity substitution, symlinks, unreadable kernel facts, lost acknowledgment and LACE-owned records. The original X0 guard checks persistent fields, original call edges/ordering, non-LACE boundary and QENV1 CORE enrollment. It does not prove resistance to a hostile actor racing path operations between identity checking and syscalls: native handle-backed kernel/resource custody and independently authenticated writer absence remain open. No delegation to Git retirement is permitted.

## Next steps

Qualify the CO-R3E exact source on native WSL. Separately pursue authenticated trust epoch revocation, handle-backed cgroup/native resource custody and independent LACE mount/FD/namespace/writer-absence reporting. CO-R2b writer-bearing Git retirement, physical P3C, Gate A/B/C, APP PASS and release remain **BLOCKED**.
