# ADR CO-R2W3 — Native Git worktree deregistration and original-owner release

Status: DEVELOPMENT ONLY; CO-R2b PARTIAL. Parent: CO-R2W2 exact source SHA-256 `ee40928fa2a9d8f1c90aa451b1c01d99bb74cbfb02b78bbe80378de2c79e4bfb`.

## Decision

Reuse `RepositoryWorkspaceAuthority`, `GitService`, `LocalDatabase`, existing `workspace_worktrees`/`workspace_state` and `WorkspaceLeaseStore` records. There is **no** second Git lifecycle owner, queue, transaction, terminalizer or worker. Do not add an automatic fallback, `worktree prune`, `--force`, or unqualified physical absence signal.

The existing `release()` entrypoint routes a registered FINALIZED worktree into its same-owner `retire_worktree` operation. That native retirement operation is allowed **only** for durable `FINALIZED` worktrees that are clean, checkpoint-identical, still registered by original Git, unleased and have **never delegated writer authority**. This last restriction is intentional: persisted `fence_evidence_json` or a settled lease cannot certify independent physical absence. Workspaces that ever started a writer require a future independently qualified LACE proof and remain blocked, even if they store a locally accepted fence.

1. Reprove exact durable workspace identity, state version, original `.git` hash and native Git roster, original common Git directory, HEAD/clean status, exact checkpoint bytes and settled leases.
2. Persist and fsync a generation-, source-inode-, destination-inode- and Git-marker-bound retirement receipt outside the workspace before Git mutation. Hold a nonblocking POSIX kernel `flock` until the SQLite decision or process death.
3. Reprove the receipt, destination identity, registration, Git metadata and candidate content after obtaining the lock; execute **one** native `git worktree remove` without force.
4. A nonzero native return is **not** proof of no effects. A success still needs native roster absence **and** physical destination absence. Only then, under the original `LocalDatabase` write cut, delete the durable registration and commit `RELEASED` together.
5. If the process dies before or after that SQLite cut, `reconcile_worktree_retirement` acquires the same receipt lock, rechecks original common-dir inode and exact durable state, Git roster absence and destination absence, and commits/re-proves the same original cut. The receipt is only cleared **after** that cut returns. A still-registered or replaced path stays unresolved; there is no silent deletion.

Existing `release` and `reconcile_release` refuse pending native retirement. A stale/tampered receipt cannot grant `RELEASED`. This is **logical native Git/SQLite custody**, not filesystem+database ACID, not prevention of arbitrary uncooperative outside writers, and not an independently attested LACE absence claim.

## Native and independent gaps

`CO-R2b` remains PARTIAL: writer-bearing worktree retirement, independently issued and fresh physical writer absence, original external teardown proof, multi-process hostile replacement races, abrupt kernel/storage interruption, block device EIO/fsync, full T-WS-1..9 native WSL qualification and release gates are open. The hosted POSIX Git process-exit tests certify only development evidence. They do **not** make the source APP PASS or releasable.
