# ADR — SKILL-3C2D2C2C4A: Original prework immutable-stage rollback

## Problem

The opt-in original Fleet source stager can seal nested directories with Linux
`FS_IMMUTABLE_FL` after enabling per-file fs-verity. If stage creation later
fails before emitting a signed stage receipt, already-sealed directories can
become impossible to remove by the normal owner. Previously the original
stager simply propagated the failure and closed its FDs.

## Decision / canonical owner

Extend **only** the original Fleet stager's failed, *unpublished* preparation
path. A new helper in its existing Linux directory-immutable integration walks
the just-created, attempt-owned stage by opened directory FDs. The entire
bounded stage must be privately owned and contain only ordinary, single-linked
files or directories; no symlinks, foreign owners, unusual modes, or cross-
device subtrees. All directory FDs remain open while rolling back bottom-up.
Clear only the kernel immutable flag and remeasure, preserving other flags.
Do **not** delete files or directories, chmod them writable, issue a receipt,
reuse a failed stage, open arbitrary cleanup paths, or invent a new LACE owner.

If the host cannot read or clear immutable flags, the original stager reports
an explicit *privileged recovery required* error rather than claiming cleanup.
Unexpected entries fail before any flag change. This rollback is never invoked
on successful staging, a published Agent prework record, or a live/finished
Agent process. Post-admission LACE quiescence, original grant consumption,
privileged cleanup and teardown remain independent unimplemented gates.

## Tests and limits

Focused tests inject partial kernel sealing failure, verify only immutable bits
are cleared and file bytes/inodes preserved, enforce no stage reuse, and refuse
symlink/public-tree or lost-capability cases. These hosted ioctl simulations
do not prove Linux/WSL filesystem support or protection from privileged actors.
A real kernel positive witness and original LACE teardown proof are mandatory
before relaxing Agent RESOURCE_INTENT or process launch restrictions.
