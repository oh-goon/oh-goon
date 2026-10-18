# ADR — CO-R2W1: recoverable workspace filesystem effects in the original owner

**Status:** Development implementation, partial CO-R2b only. **Parent:** CO-R01 source
`0d40ecc660c5188db8435a3e6cb079350de6f2bcabe864088fe50393bc59e452`.

## Decision and authority

`RepositoryWorkspaceAuthority` remains the sole owner of durable workspace
identity and the only coordinator of its filesystem lifecycle. SQLite remains
`LocalDatabase` and Git remains the native worktree registration authority. No
extra queue, actor, generic backend provider or execution flow is created.

**Allocation:** A same-owner, workspace/generation-bound preparation receipt is
fsynced in the existing private managed-workspaces root before `mkdir`. A crash
before SQLite ALLOCATED commit leaves REQUESTED plus a receipt; the original
`reconcile_allocation` refuses nonempty/foreign paths, removes only an empty
receipted allocation, or confirms a committed ALLOCATED directory. This is
filesystem evidence, not cryptographic proof against arbitrary code executing
as the same OS principal.

**READ_SNAPSHOT:** A versioned source-identity preparation receipt is fsynced
before atomic rename. `reconcile_read_snapshot` takes the original SQLite write
cut, re-proves source identity and either completes ALLOCATED -> READY with the
original metadata row, preserves the existing READY record, or refuses unknown
bytes. An exception after committed READY must **never** delete its tree.
Normal consumption still re-proves bytes independently. An interrupted empty
snapshot cannot prove whether rename occurred and stays ALLOCATED for retry.

**Release:** Check current and historical unsettled/unknown writer records in
one original SQLite write cut. Rename an owned tree to deterministic same-root
quarantine, then commit RELEASED and reclaim the quarantine only after commit.
`reconcile_release` restores a precommit quarantine to its original path; for a
committed RELEASED state it reclaims the quarantine. Before rename, an owner
receipt durably binds the workspace version and the exact directory device/inode;
recovery rejects unreceipted or substituted quarantines. Unknown or replaced paths
are left unresolved, not deleted. Registered Git worktrees are refused pending
native deregistration and recovery evidence.

**Git worktree:** A failed cleanup cannot silently fall through to `rmtree`.
If Git does not prove removal, preserve the registration and signal unresolved;
if SQLite READY is observed after an ambiguous commit, keep the worktree. This
is containment, not complete interrupted-Git recovery.

## Explicit incompleteness

This is **CO-R2W1**, an independently useful development checkpoint of the
CO-R2b workspace bundle, **not full lifecycle closure**. Full CO-R2b still
needs: durable postcrash native Git worktree re-registration/deregistration
proof and provenance; independent physical writer quiescence; read-snapshot
race and kernel tamper tests; full T-WS-1..9 on the WSL host; source-bound
native qualification; reconciliation/absence proof for all ambiguous effects.
Neither SQLite+filesystem atomicity nor APP PASS or Gate B/C is claimed.

**Selected hosted fault cuts:** real process exit before/after allocation
commit and release commit, simulated postcommit read verification failure,
precommit source publication recovery, lease refusal and replacement-path
fencing. Native WSL and power-loss/disk fault evidence remain NOT RUN.
