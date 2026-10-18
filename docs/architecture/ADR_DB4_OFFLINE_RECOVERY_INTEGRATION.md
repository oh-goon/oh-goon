# ADR DB4 — Verify offline SQLite backup identity before publication and recovery review

**Status:** Development implemented 2026-10-15; native exact-source qualification and independent certification **NOT PASSED**. Parent: DB3 `ca850299adb2857183a3b46cb029df34a0148a0655376fa0c20d0c188869adcd`.

## Decision

Keep **one** operational SQLite transaction/durability owner: `LocalDatabase`. Preserve the DB3 native `VACUUM INTO` snapshot path. Strengthen that exact original owner so it verifies a newly produced snapshot's immutable read-only SQLite state **before** publishing the snapshot: SHA-256, regular single-link pathname and descriptor identity, existing native SQLite integrity and foreign-key inspection, application-ID database cookie and durable revision equal to source. The public `LocalDatabase.verify_offline_backup(receipt)` is inspection only. It does not launch execution, restore a database, switch operational paths, change trust claims or sign external evidence. A local caller-provided receipt is not a signed or independent certification.

A real process exit inside a SQLite write transaction and a real exit after commit/before acknowledgement are verified under the *existing* `LocalDatabase` reopening path, with the same original backup call. The original authenticated Fleet prework admission test path writes the source data; an offline snapshot readback asserts the exact persisted run admission and causal event kinds after reopening the snapshot through the original database startup. This does not assert permission to re-execute that historical Run.

The X0 canonical SQLite authority guard rejects weakening the snapshot checks and QENV1 has the **same** original CORE qualification ring select the DB4 tests. There is no DB4 runner/owner, SQLite portability abstraction, Scout recovery writer, or alternate scheduler.

## Observed evidence and deliberately excluded claims

- Hosted SQLite/pager/process tests, original Fleet integration fixture, snapshot corruption/replay safety, and architecture mutation tests are development evidence only.
- The full exact-source native WSL `QUALIFY_B27_EFFECTFUL=1` qualification **must be rerun on DB4**, especially the repaired original `PHYSICAL_OBSERVER` cancellation/revocation witness. Older DB1B2 native FAIL remains FAIL; DB3's local hosted tests cannot replace it.
- Native host ENOSPC/EIO/fsync failures, device/filesystem crash recovery, independently authenticated signer/key custody, Gate A/B/C, P3C and AG-03/04, APP PASS and release are **NOT PROVEN**.
- Reopening an offline snapshot through normal `LocalDatabase` admission may legitimately mutate SQLite files. Only inspect an original frozen snapshot with the static read-only verifier; restoring it requires an externally approved, offline procedure followed by exact normal admission and physical security checks. No automatic restore.

## Next

1. Run the **unchanged** canonical `make qualify-development-wsl` on an extracted exact DB4 archive and retain the full receipts.
2. Add independent host filesystem fault/kill/fsync evidence and confirm absence of duplicated DB authority.
3. Certify the required external physical and trust gates separately. Do not label this DB4 development checkpoint an independent native PASS.
