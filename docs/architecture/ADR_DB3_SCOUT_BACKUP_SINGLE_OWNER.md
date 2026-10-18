# ADR DB3 — Disposable Scout, explicit SQLite backup, no recovery authority fork

**Status:** Accepted for development only · 2026-10-14. **Parent:** DB2 SHA-256 `ca7c5e8199a95139117b2d01525456a1ab55796c2d12b4d33ab2401c3606eb73`.

## Decision

Keep `LocalDatabase` as the one operational SQLite transaction/durability owner. Scout remains an independent, disposable *derived view*, never an admission source, fallback journal, causal authority, or restore input. No PostgreSQL, ORM, service database, second operational transaction owner or parallel recovery engine is introduced.

`ScoutAnalyticalDatabase` rejects every existing Scout database-family file that is a symlink, non-regular file, or hardlink. Its identity preflight compares Scout main/WAL/SHM inodes against operational main/WAL/SHM/rollback journal inodes. This protects against pre-existing aliases that could make a disposable write corrupt operational SQLite. It is a local pre-effect correctness fence, **not** a kernel-enforced guarantee against concurrent hostile filesystem mutation; that requires separate physical custody evidence.

`LocalDatabase.export_verified_backup(destination)` is an explicit, operator-called method. Under the existing cross-process SQLite write gate, it re-proves admitted operational identity and connection policy, reads durable revision and instance cookie, uses SQLite-native `VACUUM INTO` to create a separate consistent snapshot, runs the existing read-only SQLite integrity and foreign-key inspector, fsyncs the backup file, publishes it with exclusive no-clobber hardlink creation, removes its private staging file, and fsyncs the containing directory. The parent directory must be private (existing public/shared directories are rejected, never chmodded); no backup destination directory is created before the operational source proof. It returns a *local observer* receipt with SHA-256 and revision/cookie. That receipt is NOT signed by Enforcer, is NOT live execution admission, and cannot authorize a release.

The snapshot is never attached or automatically restored to the running owner. Offline recovery consists of explicitly stopping the runtime, checking a separate snapshot's integrity and instance cookie against its recorded receipt, then separately qualifying that candidate under the ordinary `LocalDatabase` startup/admission path with the existing Gate A/B/C restrictions. A snapshot or successful local reopen is not a certified recovery or unattended repair. No `reset_local_state`, binary copy of live WAL, or Scout rebuild is treated as recovery authority.

## Why

- A path-only Scout/operational separation is insufficient when the sidecar is a hardlink to a real source inode.
- SQLite's backup mechanisms understand live transactions/WAL; a plain copy of a live `.db` does not.
- A designated backup needs a fail-before-work no-clobber boundary and independent integrity evidence; unqualified restore would reopen admission without the required authentication.
- One owner must resolve/admit/observe, not a new maintenance service with its own SQLite transaction path.

## Required regressions and gates

1. Hardlinked operational/foreign-victim Scout files are rejected without modifying victims; disposable Scout deletion/recreation cannot change operational file content.
2. Snapshot validation checks `integrity_check`, `foreign_key_check`, revision, application ID, SHA-256, exclusive publication and process-local restart/reopen.
3. An active write cut cannot start maintenance; a racing destination reservation does not overwrite data or strand stage files.
4. Native WSL requalification is required on this exact source, including the known DB1B2 `PHYSICAL_OBSERVER` failure regression. Host ENOSPC/EIO/fsync and physically independent recovery are still unproven.
5. DB4 retains the independent integration, disk fault, restart, source-locked and release gates. Gate A/B/C, P3C, AG-03/04 and APP PASS remain BLOCKED.
