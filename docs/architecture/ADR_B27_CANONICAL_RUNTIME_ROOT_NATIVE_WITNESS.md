# ADR — P1P2B2B2: Native witness uses canonical runtime-root preparation

Date: 2026-10-14. Status: accepted as a **development-only** architecture constraint.

## Problem

The original B27 native mechanical witness previously substituted host `/` and
failed mounting `/workspace` under a read-only root. B2B1 replaced the host root
with a private sparse-root scaffold but still recreated utility/dependency capture
and disabled `verify_runtime_root()` in its test. This tested different preparation
semantics from the production strict Fleet path.

## Decision

The *existing* `capture_runtime_root()` and `verify_runtime_root()` remain the
only sparse-root construction and identity-verification entrypoints. A native
witness declares exactly `sh`, `cat`, `stat` and `awk` as snapshot-bound
`HostToolBinding` inputs; the existing capture resolves their dependencies and
snapshots the sparse root. The original Bubblewrap host-tool mount builder
consumes these frozen tools, and the original root identity verifier remains
active. Witness utility paths are resolved from the original `/toolchain/bin`
mounts, not copied into a special test root. The writable Step `/tmp` mount owns
the sealed-data output target; it is not a second runtime-root preparation API.

There is **no** new runtime code path, signer, LACE publisher, FD lease owner,
or permission transition. A host without `hashmarks` or a real FD-capable bwrap
cannot claim that this test has passed.

## Adversarial proof requirements

- X0 rejects replacing `capture_runtime_root`, snapshot host-tool inputs, root
  verification, original toolchain transport or the original `_command`.
- QENV1 selects the exact P1P2B2B2 source and enrolls the focused regressions
  in the existing `PHYSICAL_OBSERVER` ring, with the opt-in effectful test.
- The native root identity must fail upon post-capture file mutation before
  the mount builder runs.
- The real native test still compares pinned source device/inode to mounted
  device/inode and verifies the sealed data by content, retaining the B27
  fake-child-argument attack regression.

## Not certified

Local structural tests and a temporary test-only hashing substitute do not
exercise the real Hashmarks identity implementation. No effectful Bubblewrap
run or independent physical-observer custody was available in this workspace.
Gate A/B/C, original physical fact qualification, P3 absence, AG-03/04,
APP PASS and release remain BLOCKED.
