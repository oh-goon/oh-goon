# ADR — CO-R3H: original-inode retirement acknowledgment, not identity-conditional removal

Date: 2026-10-15 · Status: **accepted for development only; NOT RELEASE**

## Problem and previous behavior

CO-R3G pins the cgroup and parent descriptors and rechecks the original `(st_dev, st_ino)` before `rmdir(name, dir_fd=parent_fd)`. Linux does **not** offer an atomic expected-inode predicate for this syscall. Between that check and `rmdir`, a hostile parent mutator could rename the original cgroup out of the way, substitute an empty foreign directory and let the syscall remove the substitute. A subsequent absence check of the *old pathname* would report success while the original cgroup inode was still linked at its new name. The attempt could then publish a completion receipt or retire legacy recovery metadata.

## Decision — preserve the single original owner

The existing `_fleet_plan_cgroup_resources._remove_cgroup()` is the only path changed. Following successful native `rmdir`, while its original descriptor remains open, it uses `fstat(descriptor)` to establish that the **original directory inode** still matches the persisted device/inode identity and its `st_nlink == 0`. An unobservable descriptor, unexpected identity, or still-linked original inode causes `FleetPlanInvalid` before a normal attempt metrics receipt or a legacy orphan metadata deletion. The existing check that the pathname has not reappeared remains mandatory. No second remover, admission decision, trust authority, LACE observer or receipt kind is introduced.

The original QENV1 CORE enrolls local native-filesystem negative controls that inject a swap directly during the `rmdir` syscall, a nested-child swap, and an unavailable post-effect inode observation. Original X0 architecture guards reject removal or weakening of this check. CO-R3H wins **evidence accuracy after the syscall**, not atomic mutation safety.

## Critical limits, intentionally unresolved

1. This mechanism **does not prevent the wrong foreign directory from being removed** by a hostile swap at the syscall. It detects the common false-ack situation while the original inode remains linked, and fails the lifecycle closed. It cannot undo a destructive kernel effect.
2. A full adversary can alter names again or remove the original elsewhere. A zero link count does not independently prove writer quiescence, absence of escaped processes, exclusive parent custody, or lease/generation revocation.
3. Linux cgroup-v2 `st_nlink` behavior must be separately qualified on an admitted native WSL/kernel host; ordinary temporary-directory tests are not proof of actual cgroupfs semantics. Unavailable/unsupported observation must remain a failure; do not silently fall back to pathname-only success.
4. CO-R2b writer-bearing Git retirement, authenticated Enforcer epoch revocation, complete LACE Run/Step custody, hostile replacement exclusion, P3C, Gate A/B/C, APP PASS and release remain BLOCKED.

## Next owner boundary

CO-R3I should prove **exclusive native parent mutation custody and trust-generation fencing through the original Enforcer/LACE contract**, or refuse destructive cleanup when that precondition cannot be established. Do not invent a second cgroup remover or treat this post-effect receipt as a complete physical-absence certificate. Keep physical qualification independent of the hosted tests.
