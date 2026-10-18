# P1/B27B1 — Canonical native mount-argv completeness (development only)

**Parent:** exact P1B27A candidate. **One owner:** `BubblewrapProcessIsolation._prepare_mounts`.

## Problem

The original signed B12C mount descriptor and P1B27A physical scope cover managed
inputs, read-only workspace and host-tool targets, but Bubblewrap also creates
its runtime-root bind, private writable binds, per-attempt `/etc` file binds,
optional cgroup bind, `/proc` and `/dev`, and sealed runtime-file FD mounts.
Without a complete prelaunch correspondence check, a future change to the
Bubblewrap builder can mount a new source or omit a required mount without
changing the earlier signed managed-input projection.

## Implemented boundary

The *original* Bubblewrap mount builder calls `_require_original_native_mount_argv`
once **after** all original mount append operations and **before** returning its
argv. It compares all `--bind`, `--ro-bind`, and `--ro-bind-data` entries against
exact prepared original attempt paths, managed input and frozen host-tool
bindings, optional nested cgroup root, and exact sealed memfd descriptor/path/mode.
It rejects duplicated, missing, foreign, mode-changed, and unregistered mount
options. Synthetic `--proc` and `--dev` targets must occur exactly once.

The existing X0 checker freezes the actual straight-line call edge and checks
required source classes. Mutation tests force rejection of bypasses, altered
mount objects, FD mode, and unregistered options. Normal AGENT_PREWORK and
PHYSICAL_OBSERVER rings enroll the regressions. No additional builder, scheduler,
observer, signer, recovery owner or admission path was introduced.

## Limitations / next bounded work

This compares command arguments **only**. It does NOT sign additional mount
sources into `RESOURCE_INTENT`, establish open-FD/inode-pinned directory mounting,
prove which kernel objects Bubblewrap actually mounted, or independently prove
post-teardown absence. Runtime/writable `/etc` and atomic FD mount lifetimes
remain obligations of original LACE custody and its single physical observer.
An opaque FD integer is not by itself kernel-object provenance.

Next P1/B27B2 must bind complete original native resource identities to one
original pre-effect authority (including FD/inode custody), then establish
physical consumption and absence through the existing observer. Real Agent
source mounting remains denied. Gate A/B/C, AG-03/04, APP PASS and release remain
blocked. No development PASS may be promoted into these gates.
