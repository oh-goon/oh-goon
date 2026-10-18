# ADR — DB5: one owner for offline backup publication and failure outcomes

**Status:** Accepted for development only — 2026-10-15.
**Parent:** DB4 source SHA-256 `6f9df581513b144e79bd36ad27aba1fb71dd1e1eca9e29dc9817db23ef3a9a6f`.

## Context

DB4 verifies an offline SQLite snapshot prior to publishing it under a new
filesystem name. Publication uses an exclusive hardlink followed by a directory
`fsync`. If `fsync` fails after the link, the previous implementation could
return an exception while leaving an apparently usable named snapshot behind.
This is an **ambiguous publication**, not a durable backup receipt. Moving the
failure handling to a new snapshot service would duplicate the SQLite owner.

## Decision

`LocalDatabase.export_verified_backup` remains the sole owner of `VACUUM INTO`,
prepublication SQLite checks, publication, directory syncing, cleanup, and
final receipt creation. SQLite continues to own operational transaction and
crash recovery, and `LocalDatabase` remains the one domain write owner.

1. Prove the original database identity and durable revision before staging.
2. Verify the staged SQLite snapshot (integrity, foreign keys, SHA-256,
   database identity and revision) and fsync its bytes.
3. Publish with exclusive `link`, remove the staging name, fsync the parent,
   and verify the **published name** before returning a receipt.
4. If any operation after successful link fails, unlink **only the inode
   published by this operation** and fsync the parent again. Propagate the
   original error if cleanup was durably acknowledged.
5. If the target was replaced, the rollback fsync fails, or cleanup cannot
   prove ownership, raise `BackupPublicationUncertain` with a manual inspection
   requirement. Never delete a replacement and never return a success receipt.

The backup remains an **offline candidate**, not Enforcer admission proof and
not automatic operational database restoration. `BackupPublicationUncertain` is
an explicit caller-visible failure state, not a new execution/recovery flow.

## Tests and remaining physical evidence

Deterministic tests inject first fsync failure, repeated fsync failure,
postpublication byte mutation, and replacement by another owner. They prove
hosted code behavior, not that the real WSL disk honors host fsync/ENOSPC/EIO.

The existing X0 architecture checker guards the original backup owner. The
existing QENV1 CORE test ring, source selector and supervisor remain the only
qualification path. DB4 native WSL, native host-disk/fault and separate Gate
A/B/C, P3C, AG-03/04, APP PASS and release remain unproven. No certification
status is promoted by this ADR.
