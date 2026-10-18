# ADR: Original owner Git registration publication receipt and restart replay

Status: DEVELOPMENT-ONLY. Parent: CO-R2W1. No parallel execution or owner.

## Problem

`RepositoryWorkspaceAuthority.materialize_worktree` ran native Git registration,
index initialization and content copy before the SQLite `READY` commit. `git
worktree add` may partly succeed even when it returns nonzero; a process death
then leaves native Git metadata, candidate bytes and a durable `ALLOCATED`
record with no deterministic recovery authority. Directory deletion or
assuming rollback is unsafe.

## Decision

Persist a small owner-local receipt before the Git effect. Hold an exclusive
POSIX advisory file lock across the entire Git effect and SQLite decision;
release it automatically on process exit. The original owner alone can
`reconcile_worktree` after obtaining the same lock. It verifies original
source inode/device, durable generation, Git's native exact worktree roster,
common Git directory, revision, clean status and exact source identity. If
these agree, it uses the existing `LocalDatabase` transaction and original
checkpoint/state transition to publish READY, or re-proves an already READY
result. Without that proof, retain the effect and report it unresolved.

Only when the Git roster proves registration absent and the candidate path
is empty may the owner restore the ALLOCATED directory. No automatic deletion
of a partially registered worktree. Release checks durable native-worktree
metadata even if a hostile process deletes `.git`.

## Remaining cuts and qualification

Native Git `worktree remove` must receive its own crash-safe receipt and
positive deregistration/absence proof. Writer absence needs an independently
qualified LACE/fence proof, not a self-attested lease status. Concurrent
replacement, Git administrative storage corruption and native WSL OS faults
need exact-source qualified tests. A hosted POSIX Git child-exit test is not
an independent kernel/security certification.
