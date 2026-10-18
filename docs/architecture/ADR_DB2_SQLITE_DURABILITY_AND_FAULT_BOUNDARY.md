# ADR — DB2: one admitted SQLite durability authority

**Status:** accepted for development; native WSL and independent release certification **not passed**. Parent is the exact DB1B2 source SHA-256 `a40944c7a3da98e78a0c9a787b2e9c7555387645a2bc26c24df69ed237088c49`.

## Decision and owner

Preserve the existing operational `LocalDatabase`, its SQLite journal mode and its original cross-process SQLite authority gate. No PostgreSQL, ORM, generic SQL executor, second database service, second admission writer, duplicated watchdog or second recovery state machine. SQLite itself owns the transaction, locking, rollback and `SQLITE_FULL` disposition. OH-Goon owns **admission** to use this exact SQLite file and connection policy, not an alternative implementation of SQLite durability.

The canonical owner selects `AUTO|DELETE|WAL` **once under the original initial authority gate**, not before that gate. That selection is unchanged throughout one admitted runtime. The existing WAL guard requires the configured SQLite version and filesystem safety. Explicit DELETE remains valid even if filesystem suitability for WAL is not proved; certification's separate *proven local Linux filesystem* preflight remains mandatory and is not downgraded by DB2.

At startup `LocalDatabase` captures regular-file inode/device and observed Linux mount filesystem type in the same admitted path identity record. Subsequent original cut/open observations fail closed if inode/device or observed filesystem type changes; schema version, schema cookie, persisted application-id, journal mode and durable revision floor retain their existing inside-SQLite proof. Mount type and identity are admission observations, not claims of storage device fsync durability or hardware integrity. Unproven filesystem remains unproven; they are not promoted to a positive filesystem claim.

Each operational transaction (including reused execution-connection scopes) checks SQLite's `PRAGMA synchronous=FULL`, `foreign_keys=ON` and `query_only=OFF` **before** BEGIN. A downgraded persistent connection fails before any participant callback/effects. Do not silently repair it or accept the initial-open check as proof of later cuts. The original transaction owner is the sole commit/rollback authority.

## Evidence and fault classification

- Hosted fault: `PRAGMA max_page_count` drives native SQLite's real `SQLITE_FULL` error during a two-part original-owner write transaction. Reopen and query the original DB: both the staged row and revision must be absent. This proves SQLite pager rollback, **not** host `ENOSPC`, OS `fsync`, power-loss retention or disk capacity safety.
- Hosted multiprocessing: three independently constructed `LocalDatabase` processes submit 27 concurrent original-owner transactions; after reopening, committed revision and distinct committed rows agree. This proves tested local interleavings, **not** WSL/native filesystem endurance under realistic machine crash/disk faults.
- Fail-before-work: scoped handle downgraded to `synchronous=OFF`, `foreign_keys=OFF` or `query_only=ON` is denied; changed filesystem mount classification is denied. Targeted X0 and QENV1 regressions protect the original owner/qualification path.
- Existing DB1B2 before/after COMMIT OS-process-exit and signer/lease-transfer races remain authoritative regression coverage. Do not duplicate them in DB2 to inflate counts.

## Explicit blocked gates

This development checkpoint does not establish native WSL `ENOSPC`/`EDQUOT`/`EIO`/fsync injection, kill/power-failure persistence, network/9p/DrvFS mount behavior, forensic restart receipts, DB4 independent validation, release, Gate A/B/C, P3C, AG-03/04 or APP PASS. A local hosted test result cannot be substituted for those facts. Keep the original `QUALIFY_B27_EFFECTFUL=1 make qualify-development-wsl` on the exact DB2 source. The native operator must collect separate disk/fault receipts before claiming **DB2-NATIVE PASS**.

After DB2's development work, DB3 owns Scout/backup/maintenance and DB4 owns final exact-source native product certification. Do not use DB3 to bypass a failed DB2 native fault gate.
