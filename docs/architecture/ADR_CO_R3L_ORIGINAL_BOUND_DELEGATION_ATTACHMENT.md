# ADR CO-R3L — Creation-bound delegated controller and process attachment effects

Status: implemented for development, not independently physically certified. Parent is CO-R3K SHA-256 `0611f832789c18da531d8498a5f39a7ca347b5c90ba24397d0f09ea0a1396171`.

## Decision

**One original native cgroup owner** (`_fleet_plan_cgroup_resources.py`) retains controller effects, delegated child allocation, process-membership effects and native cleanup. No alternative runtime, LACE physical observer, Enforcer decision authority or second cleanup path is introduced.

- Original `CgroupV2AttemptLease.open` already persists the attempt cgroup's device/inode before ordinary writes (CO-R3K). The nested creation path now transports that exact identity into controller enabling, which uses an opened, `O_NOFOLLOW` directory and `cgroup.subtree_control` handle with pre-/post-effect pathname identity checks.
- Nested execution and monitor children are created relative to the opened parent directory. Their creation-time device/inode identities are carried in the existing nested launcher command, and the launcher refuses missing or malformed identities before `bwrap` is released.
- All nested `cgroup.procs` writes use the same `_write_bound_cgroup_control` primitive, are no-symlink relative to an identity-verified cgroup descriptor, and reject a short write or changed native object. The same shared primitive is used for controller activation.
- Direct attempt execution's original `-S` bootstrap transports `cgroup` directory, device and inode to the command subprocess. It checks the original directory handle and current path both before and after writing its `cgroup.procs` control; missing or substituted targets prevent `execvpe`.
- Delegated process-subgroup creation is now relative to the original pinned parent handle. If its membership effect fails, the same guarded `_remove_cgroup` owner attempts identity-bound rollback; uncertainty refuses cleanup success and preserves a foreign substituted target.
- Prior tests that created fake cgroupfs directories without the kernel-created controller files were updated to materialize those files. No test-only runtime bypass was added.

## Verification, exact limitations

Hosted Linux tmp-directory descriptor controls and subprocess effects exercised with pytest and source-mutation guards in original QENV1 CORE. Tests are not actual native cgroup-v2 privileged effects; the subprocess observes local regular-file approximations. Inherited SQLite WAL tests may skip when host admission refuses WAL. Exact-source native WSL has not run for CO-R3L. No independently authenticated Enforcer revocation, LACE complete writer/FD/mount/namespace evidence, or kernel-enforced exclusive parent mutation custody is established. Linux `rmdir(name,dir_fd)` still is not atomically identity-conditional. Do **not** promote completed nested attachment or group quiescence into Run/Step writer absence or CO-R2b Git-worktree deletion authority.

## Remaining work

Qualify CO-R3L on a real native WSL cgroup-v2 host; independently test hostile path races and detached process writers. Close the original attempt `mkdir`–durable identity gap without authorizing cleanup of unknown inodes. Obtain independently provisioned generation-bound parent mutation custody from original Enforcer/LACE, or keep destructive certification blocked. Require independent signer/trust-epoch and physical LACE evidence for Gate A/B/C, P3C, APP PASS and release.
