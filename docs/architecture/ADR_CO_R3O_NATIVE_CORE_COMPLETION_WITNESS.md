# ADR CO-R3O — original CORE native cgroup effects and truthful completion receipts

**Status:** implemented, development-only, not release.  **Parent:** CO-R3N exact source SHA-256 `a713644ab5aa005d09aebbd73b1f582ec64316fe82fb9bab76fc0ebe0964af3e`.

## Decision

Extend the **existing** QENV1 CORE's optional `test_co_r3n_native_cgroup_v2_witness.py` rather than adding a second qualifier, cgroup owner or private Enforcer. The already explicit `QUALIFY_R3N_CGROUP_ROOT` is the sole input. No root => `R3O_NATIVE_STATUS.txt` is `NOT_PROVEN`; a requested root begins at `REQUESTED_UNQUALIFIED`, not PASS. The existing B11 process-ring supervisor requires the original native test module to contribute a JUnit case with no failures/skips. After the original CORE ring, and only after a successful supervisor exit with a complete receipt, the status becomes `CORE_ACCEPTED_NATIVE_DEVELOPMENT_ONLY`; other results remain `FAILED_OR_INCOMPLETE` or `REQUESTED_UNQUALIFIED`. The original QENV1 exit trap preserves the status, exact-source PRE/POST and receipt digest.

## Added real kernel fault cuts

1. **Stale creation-bound identity:** provision a real cgroup via `CgroupV2AttemptLease.open`, transport a deliberately false device/inode in the **original direct process bootstrap**, and prove its payload does not begin. Restore the original identity for normal bound teardown. No guessed PID cleanup.
2. **Nested controller delegation:** require the original owner's native `cgroup.subtree_control` to really enable `cpu`, `memory`, and `pids`, and bind both generated nested subgroups to their original directory identities. A missing nested identity must reject the launch before any Bubblewrap effect. Unsupported kernel delegation is a test failure, never a skip or synthetic success.
3. Existing CO-R3N real native attachment/teardown and post-mkdir crash quarantine remain mandatory when native ROOT is provided.

The original AST architecture owner checks CORE enrollment, manifest precedence, signed-supervisor-style module identity, honest status progression, and the native negative controls. A focused mutation suite rejects bypasses. An inherited CO-R3N guard was tightened to inspect the actual original teardown test's AST so unrelated new tests cannot mask deletion of `lease.close()`.

## Excluded authority (mandatory fail-closed)

This is **not** an independently issued Run/Step trust-generation revocation receipt. It is not a proof of absence of escaped, FD-, mount-, namespace-, or workspace-bound writers. It is not kernel-enforced exclusive parent mutation custody. `rmdir(name, dir_fd=...)` is not atomically conditional on original child inode. The native fault cuts have **not** been executed in this hosted environment, whose cgroup-v2 mount is read-only. No CO-R2b writer-bearing Git retirement, P3C, Gate A/B/C, APP PASS or release may be authorized from this checkpoint.

The witness deliberately leaves an unknown-identity child quarantined after the crash test. Operators must not blindly delete or reuse the disposable native root; recovery requires separately proven authority.
