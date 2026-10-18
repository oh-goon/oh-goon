# ADR CO-R3D — Original cgroup quiescence and receipt publication fence

**Decision:** Harden `CgroupV2AttemptLease` and the existing `_wait_for_cgroup_empty` within the sole original Fleet attempt-resource implementation. Do not add a cgroup owner, process supervisor, native reader, journal signer, or another LACE/Enforcer execution path. The shared `linux_physical_observer._read_regular` performs the bounded, no-symlink kernel-file reads; it is not a new absence classifier.

## Defect and implementation

The prior wait returned successfully when `cgroup.events` was missing and `cgroup.procs` was empty or missing. A deleted, replaced or partially observable cgroup could therefore produce a false positive local quiescence signal. `CgroupV2AttemptLease` also did not retain a directory inode/device from creation through teardown, and normal attempt resource evidence was published *before* native cgroup removal, allowing failed teardown to leave a misleading finished-looking receipt.

The original `open()` now pins the just-created cgroup directory `(st_dev, st_ino)` and `close()` requires that exact identity before kill, during the wait, and before native removal. Both bounded kernel pseudo-files must be readable under the pinned directory. `populated 0` and an empty immediate process roster must agree, while `populated 1`, incomplete observations, path replacement, symlinks, malformed PIDs and races cannot authorize completion. The unchanged original `close()` resource-lock authority drives kill, wait, metrics read, native removal and only then evidence publication. On failed native removal, the source record remains, with no completed attempt metrics published.

All tests use the exact original owner; negative mutations enforce the ordering and the original QENV1 CORE enrollment. One inherited nested-Bubblewrap test fixture also gained current mandatory `paths.root` and `bubblewrap_bindings` fields. This changes no Bubblewrap runtime code.

## Physical-proof boundary

This is a **local attempt-cgroup observation**. It does not prove escaped `setsid` writers, foreign processes, mount FD holders, source-fd identity or independent LACE custody. Kernel `populated 0` is not a full physical-writer-absence receipt; cgroup `kill` is not a cross-namespace fence. The in-process pinned path has a residual substitution race around pathname-native removal that must be qualified on the original delegated cgroup filesystem; true independent physical proof will require handle-based identity and physical-observer custody under original Enforcer/LACE authority. Therefore CO-R2b writer-bearing Git retirement and all release/physical certification gates remain blocked.

## Results and next gate

Hosted selective inherited + new ring: **86 collected, 82 passed, 4 skipped**, with skips limited to WAL modes this host does not admit. Original X0 architecture and QENV1 shell syntax PASS. Real independent cgroup-v2 kernel containment, ENOSPC/EIO/power-loss durability, exact-source native WSL and Gate A/B/C/P3C/APP PASS are **not** claimed. The next CO-R3D continuation must bind the native cgroup generation and trust epoch to the original signed Run/intent and obtain independently qualified physical-writer absence at the destructive cut without silently substituting diagnostic observations.
