# ADR CO-R3B — Original-owner post-effect failure-cut conformance

**Decision:** Keep scheduled Fleet child binding, executor result evidence, and
SQLite offline backup under their established canonical owners. Conformance is
an attached negative-evidence test suite on the original QENV1 CORE, never a
shadow transaction manager, launch service, backup writer or runtime backend.

## Guaranteed by existing code and new hosted tests

1. **Schedule:** `Journal.admit_run_in` makes the Schedule child `RUNNING`
   in the same SQLite transaction as Fleet `RUN_ADMITTED`. A real process
   exit before COMMIT leaves a pending occurrence and no Fleet Run; an exit
   after COMMIT produces an occurrence bound to exactly that admitted run.
   No worker launch is attempted by the test.
2. **Evidence:** `ExecutionEvidenceAcceptanceAuthority.accept` samples one
   timestamp at the original write cut; a lease expired at that instant
   cannot leave a receipt. Pre-COMMIT process death rolls back; post-COMMIT
   death preserves one durable receipt. The focused ledger fixture holds
   external physical preflight stable — this is NOT physical writer proof.
3. **Backup:** `LocalDatabase.export_verified_backup` uses real SQLite
   snapshot/verification and a no-clobber hardlink. Process death before
   publication leaves no final name, while death after hardlink but before
   durability acknowledgement leaves a candidate that **must not** be
   classified as a successful, acknowledged backup or installed as state.
   The original DB stays readable. An existing candidate cannot be overwritten
   by a later export; leftovers require operator inspection rather than
   an unproven automatic cleanup owner.

## Negative architecture assurance

`co_r01_lifecycle_contract` in original X0 requires the exact CO-R3B
subprocess witnesses and mutation regression in the original QENV1 CORE.
Removing the native process death, original admission, acceptance or backup
operation, or CORE enrollment is rejected as architecture regression.

## Nonclaims and follow-up

No physical-liveness or signer-revocation inference comes from SQLite rows,
POSIX process-exit simulations, or staged-backup presence. Real kernel power
loss, directory fsync faults and independent LACE containment must be qualified
on target platforms. CO-R2b writer-bearing cleanup remains blocked. CO-R3C
should cover revocation/effect-after-revoke and physically observed no-live-
writer boundaries through their existing Enforcer/LACE owners.
