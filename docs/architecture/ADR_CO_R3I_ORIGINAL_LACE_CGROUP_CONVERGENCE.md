# ADR CO-R3I — Reuse original cgroup owner in journal-controlled LACE cleanup

Status: ACCEPTED for **development implementation**, NOT independent physical certification. Date: 2026-10-15.
Parent: CO-R3H complete-source SHA-256 `4abbcceafb6a1cb729d848dde668a3a6aa4c9230d644f8152b6bb4ba21597920`.

## Context and decision

The original journal-owned `fleet_plan_local_lace_native_reaper.cleanup_local_orphan` already fences the recovery claim through `owner.execute_native_cleanup`, original native allocator locks, a correlated `LocalLaceOrphanInventory` and renewed network/cgroup inventory. However, the LACE native effect historically repeated less-safe cgroup path reads and `Path.rmdir()`, bypassing the stronger existing Fleet native cgroup owner developed in CO-R3D–H.

In CO-R3I the **same callback and native owner** now transports the inventory's `(cgroup_device, cgroup_inode)` as `original_identity` into `_wait_for_cgroup_empty`, checks for unqualified nested entries through `_bound_cgroup_directory` and calls the existing `_remove_cgroup(expected_identity=original_identity)`. No additional cleanup worker, database writer, authority, protocol, signer or fallback remover is introduced. Network cleanup continues to be operated through the preexisting original network owner; after that effect, the cgroup observation is performed again.

## Guarantees and proof boundary

* Before calling native removal, LACE recovery must show the original inode, complete/consistent `cgroup.events` and `cgroup.procs`, a dead predecessor, a current inventory and the original journal cleanup ticket.
* The original remover checks the target before effect, uses a pinned parent descriptor and verifies the **original opened inode** became unlinked after native `rmdir`. A vanished pathname alone is insufficient.
* Ambiguous or partial effects result in `NativeCleanupFailure`; original recovery records remain and the owner never emits independently verified absence. Native result status `CLEANUP_ACTIONS_ATTEMPTED` has precisely its preexisting semantics.
* Host tests use regular files as cgroupfs control-file stand-ins. Tests emulate virtual kernel controls disappearing on removal, but cannot substitute for a privileged real cgroup-v2 verification.

**Not guaranteed:** `rmdir(name, dir_fd=...)` still cannot atomically bind the target inode. Hostile replacement at the last syscall can remove a foreign empty cgroup *before* post-effect checks reject acknowledgment. No Linux userspace check can reverse that deletion. These changes do NOT establish independent parent mutation custody, host-provisioned exclusive delegation, independently authenticated revocation, complete cgroup/namespace/FD/mount writer census, or CO-R2b writer-bearing workspace release permission. A lock held by the same mutable process is not independent custody.

## Follow-on requirement

For actual independently proven destructive safety, the existing Enforcer/LACE host provisioning must furnish kernel-enforced control of the parent mutation authority across the exact removal cut, bound to authenticated run/step and trust generation. Until external/native evidence proves parent mutation exclusivity and full writer quiescence, release and CO-R2b writer-bearing cleanup stay **BLOCKED**. Do not add a second enforcer or self-attested custody flag.

## Regressions

The original QENV1 CORE ring includes tests of symlinked controls, nested cgroups, wrong-inode substitution exactly during `rmdir`, unavailable post-effect inode observation, retained orphan journal record, and AST/X0 mutation guards forbidding the direct LACE `cgroup.rmdir()` bypass and removal from qualification. All inherited exact-source claims remain tied to their own checkpoint source.
