# ADR — DB6: Real kernel ENOSPC witness, one canonical qualification path

**Status:** Development-only. **Parent:** DB5 source SHA-256 `a44f4b176e111345d81ffae1b5e70da1d42812ca211482112300249f31c2fb02`.

## Why

DB2–DB5 have deterministic SQLite `SQLITE_FULL`, process-exit, backup and injected directory-fsync regressions. None establishes that a real Linux kernel returned ENOSPC on the exact native host or that a real WSL Bubblewrap witness passed. Do not turn those hosted tests into physical certification.

## Decision

Keep the one existing `make qualify-development-wsl` / QENV1 prepare-once, verify-once, run-once CORE ring and the one `LocalDatabase.export_verified_backup` owner. DB6 contributes **only** an opt-in native test selected by an explicit `QUALIFY_DB6_ENOSPC_ROOT` path and never runs a second entrypoint, verifier, recovery service or database.

The test fails **before any writes** unless the specified path is the exact, separately mounted, empty, private, owner-owned Linux tmpfs/ext4/xfs mountpoint with 2–16 MiB available and <=32 MiB total capacity. Its source database is created on a different filesystem with existing `LocalDatabase` and original domain records. Its size exceeds free target capacity. It then requires the original backup operation to reject publication with SQLite `SQLITE_FULL`, checks no candidate or staging file survived, separately observes a real `write(2)` returning `errno.ENOSPC` on the **same mounted filesystem**, verifies cleanup, and rereads the original operational facts unchanged. The kernel probe is bounded to this mount, not the system root or other shared disks.

The source is exact-manifest bound by the *existing* QENV1 selector. Failure or a missing mount when opted in is a non-PASS, never a skip or synthetic errno. When not opted in, ordinary QENV1 may pass, but DB6 kernel ENOSPC evidence remains **NOT RUN**.

## Explicit native setup (on WSL; not part of OH-Goon runtime)

Use a new, completely empty, **dedicated** mountpoint outside the repository and evidence directory, never an existing user data mount. Example (requires admin mount permission):

```bash
sudo mkdir -m 700 /tmp/oh-goon-db6-enospc
sudo mount -t tmpfs -o size=8m,nodev,nosuid,noexec tmpfs /tmp/oh-goon-db6-enospc
sudo chown "$(id -u):$(id -g)" /tmp/oh-goon-db6-enospc
sudo chmod 700 /tmp/oh-goon-db6-enospc
QUALIFY_DB6_ENOSPC_ROOT=/tmp/oh-goon-db6-enospc \
QUALIFY_B27_EFFECTFUL=1 make qualify-development-wsl \
  QUALIFY_EVIDENCE_DIR=../OH_GOON_DB6_NATIVE_EVIDENCE
# After inspecting the native evidence:
sudo umount /tmp/oh-goon-db6-enospc
sudo rmdir /tmp/oh-goon-db6-enospc
```

The user—not OH-Goon—mounts and unmounts the disposable target. No automatic provisioning, privileged operations, fallback or FS simulation occurs in qualification. If the OS cannot provide a dedicated mount, status remains BLOCKED/NOT RUN.

## Evidence boundary

A successful DB6 kernel witness proves **only** kernel ENOSPC on the provisioned filesystem, with SQLite failure and original owner recovery in one native test. tmpfs is memory-backed and does **not** prove block-device ENOSPC, EIO, fsync power-failure durability or actual WSL host-disk errors. It also does not prove independent LACE physical custody, B27 Bubblewrap success, Gate A/B/C, P3C, AG-03/04, APP PASS or release. Source receipt, kernel output and original QENV1 ring results must be reviewed together. No signer or runtime Run permission is derived from this test.
