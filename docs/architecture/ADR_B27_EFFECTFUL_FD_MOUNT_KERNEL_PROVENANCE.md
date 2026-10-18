# ADR — B27 effectful FD-mount kernel provenance, same original path

**Checkpoint:** P1B27_GATE_P1P2B2A (development-only). **Parent:** P1P2B1.

## Decision and boundary

The next native acceptance witness must demonstrate that the actual mounted
kernel objects at `/home/fleet` and `/workspace` have the same `st_dev:st_ino`
as the source descriptors held by the ORIGINAL `OriginalBindSourceLease` before
supervised `run_command`. The expected identity is read using `fstat(fd)` after
`verify_kernel_custody`, **not** from a resolved pathname. The child computes
`stat -L` on the mounted targets. Any mismatch or unavailable utility rejects
the opt-in effectful qualification. Retain the original `--bind-fd` arg builder,
combined FD handoff, pinned source lease, sealed atomic authority, single
supervised launcher, and original QENV1 `PHYSICAL_OBSERVER` selection.

`--ro-bind-data` is a DIFFERENT Linux operation. The implementation may
materialize sealed FD bytes into a new kernel inode; require the existing
`_linux_seal_operations` protection and exact child content, **not** the
source memfd inode equality. Never infer actual original Run admission,
LACE publisher authentication, source-content immutability for ordinary
bind mounts, physical disappearance or Gate B from the local child test.

## Ownership and negative evidence

- Original semantic owner: Enforcer/LACE Run admission and resource intent.
- Original executor: Fleet BubblewrapProcessIsolation via `run_command`.
- Native mount source owner: `OriginalBindSourceLease` (unchanged).
- Read-only shared observer: `linux_physical_observer` (unchanged).
- Physical-fact writer: **not implemented or authorized by this checkpoint**.
- Native test only consumes already-pinned FDs; no second mount builder,
  executor, signer, new root or kernel scanner.
- Negative regression: pathname is replaced after pinning; expected identity
  remains bound to the prior FD. Missing/unlisted targets reject. X0 mutation
  rejects removal of both bind-target comparisons or source FD validation.
- Host requirement: actual effectful Bubblewrap FD-bind support. If unavailable,
  opt-in test fails, not skips or passes.

## Remaining independent exit evidence

Provisioned signed original Run/Step kernel source-to-mount provenance,
independently held physical observer credentials, one authenticated original
LACE fact publication, complete mount/namespace/FD/cgroup/network coverage,
crash/cancel/revocation surviving-holder detection, bounded teardown absence,
external Gate A/B/C acceptance and final APP PASS/release remain **BLOCKED**.
The test does not authorize any Agent effect.
