# ADR CO-R3A — Conformance on the original SQLite/Git/workspace owners

**Decision:** Accepted for development only. **Parent:** CO-R2c-Q1 source SHA-256 `280a8cdb73eb00d355655dd9519ad3b1994bb1cf556db43c8c5f05fe4681d27d`. **Scope:** the first, bounded CO-R3 conformance bundle, not full CO-R3 qualification.

## Why this checkpoint exists

Semantic similarity in a fake adapter is not proof that a native provider survives a real interruption. OH-Goon must not add a parallel provider stack merely to test the existing one. Keep `LocalDatabase` as the sole SQLite transaction owner, `RepositoryWorkspaceAuthority` as workspace lifecycle and recovery owner, `GitService` as native Git command adapter, and the original QENV1 `CORE` ring as the only exact-source qualification entry. In particular, never equate `RELEASED` in a local ledger with independently certified physical writer absence.

## Native conformance matrix (original owners)

| Provider / cut | Expected invariant | Native evidence in this checkpoint |
|---|---|---|
| SQLite DELETE, actual process exit inside owner `request` write transaction | No durable workspace state; the original owner can admit the same ID once on replay | Hosted real SQLite child `os._exit(181)`; DB reopen, original owner replay and row cardinality check |
| SQLite DELETE, death after owner `request` COMMIT before caller observes result | One persisted REQUESTED row, no second admission | Hosted child `os._exit(182)`; original owner duplicate rejection |
| SQLite WAL, same two cuts | Identical semantics, but only when admitted by the original filesystem/SQLite policy | Tests run if admitted; otherwise SKIP explicitly, NEVER downgrade WAL or claim PASS |
| Native Git remove succeeds but command acknowledgement is lost | Original owner does not declare RELEASED from exception; receipt remains, and owner recovery may publish only after native roster/path absence | Real Git + SQLite; test intercepts **only** native command response after effect; native operation is real |
| Git removal followed by real process death and foreign path replacement | Recovery must not remove adopted foreign bytes or falsely publish RELEASED | Real Git `worktree remove`, `os._exit(183)`, original owner restart, foreign-path preservation |
| Unknown database provider selection | No implicit substitution, no local DB file publication | Original `LocalDatabase` rejects `UNREGISTERED_PROVIDER` |

The hosted development environment uses SQLite 3.46.1 on `overlay`; the original WAL policy requires SQLite >=3.51.3 and an admitted local filesystem. Consequently WAL cases **skip here** rather than fabricate provider parity. The real Git/workspace test setup uses a deterministic identity-only Hashmarks test adapter because that external dependency is unavailable on this host: **Hashmarks identity, full execution, and independent custody are not qualified**. Native Git operations, SQLite persistence and child death/restart remain real.

## Original authority and anti-drift

The existing `co_r01_lifecycle_contract` X0 checker rejects removal of the CO-R3A native tests from the QENV1 CORE selector, changes which bypass the original Git effect/reconcile owner, missing native subprocess death cuts and silently lost SQLite mode coverage. The original `qualification_environment_contract` makes `CO_R3A_SOURCE.sha256` outrank CO-R2c-Q1 and all prior candidate seals. The original `scripts/qualify_b12_development_wsl.sh` selects both the conformance and adversarial architecture modules; there is no second front door, transaction path, lease owner, issuer, terminalizer, signer, fallback or recovery loop.

## Open evidence and promotion boundary

CO-R3A remains development conformance **PARTIAL** until an exact-source WSL run executes the complete existing QENV1 rings with real locked dependencies and positive proof for each admitted provider mode. A provider which is not admitted must remain `NOT_APPLICABLE/UNQUALIFIED`, not green. Still required: real post-rename/precommit and postcommit-failure scenarios across workspace and backup owners, evidence expiry under clock/commit stress, trust revoke and actual physical survivors, independent writer absence and cross-run custody, real block-device ENOSPC/EDQUOT/EIO/fsync, certified Bubblewrap lifecycle, Gate A/B/C, P3C, AG-03/04, APP PASS and release. CO-R2b writer-bearing Git cleanup is separately BLOCKED; CO-R4 provider replacement remains deferred.
