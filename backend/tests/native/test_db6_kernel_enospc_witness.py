"""DB6: opt-in real Linux ENOSPC on a separately provisioned disposable mount.

Development-only evidence, in the ORIGINAL QENV1 CORE ring. No synthetic errno,
new backup owner, runtime restore, fsync/EIO claim or independent certification.
Never fill a shared/host root: the user supplies an empty private mountpoint.
"""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import stat
from pathlib import Path
from uuid import uuid4

from oh_goon.runtime.database import LocalDatabase

_ALLOWED_FS = frozenset({"tmpfs", "ext4", "xfs"})
_MIN_FREE = 2 * 1024 * 1024
_MAX_FREE = 16 * 1024 * 1024


def _mountinfo_fstype(root: Path, mountinfo: str) -> str:
    """Require an EXACT dedicated mountpoint, not a path on a host/shared FS."""

    def decode(value: str) -> str:
        import re

        return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)

    matching = []
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(" - ")
        lhs, rhs = left.split(), right.split()
        if sep and len(lhs) >= 5 and len(rhs) >= 1 and decode(lhs[4]) == str(root):
            matching.append(rhs[0])
    if len(matching) != 1 or matching[0] not in _ALLOWED_FS:
        raise RuntimeError("DB6 requires exactly one dedicated tmpfs/ext4/xfs mountpoint")
    return matching[0]


def _require_disposable_mount(root: Path, mountinfo: str) -> dict[str, int | str]:
    """Reject ALL suspect roots before creating or writing a single byte."""
    if not root.is_absolute() or root == Path("/") or root.is_symlink() or root.resolve() != root:
        raise RuntimeError("DB6 mount root must be an absolute canonical nonsymlink path")
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise RuntimeError("DB6 mount must be user-owned, private (0700) directory")
    fstype = _mountinfo_fstype(root, mountinfo)
    if any(root.iterdir()):
        raise RuntimeError("DB6 mount must be completely empty and dedicated to the witness")
    if root.stat().st_dev == Path("/").stat().st_dev:
        raise RuntimeError("DB6 mount cannot share the host root filesystem")
    filesystem = os.statvfs(root)
    free_bytes = filesystem.f_bavail * filesystem.f_frsize
    total_bytes = filesystem.f_blocks * filesystem.f_frsize
    if not (_MIN_FREE <= free_bytes <= _MAX_FREE) or total_bytes > 32 * 1024 * 1024:
        raise RuntimeError("DB6 witness requires a dedicated small mount with 2–16 MiB free")
    return {"filesystem": fstype, "free_bytes_before": free_bytes, "total_bytes": total_bytes}


def _kernel_enospc_probe(root: Path, max_bytes: int) -> int:
    """Observe errno.ENOSPC from real write(2), bounded to the provisioned mount."""
    probe = root / f".kernel-enospc-{uuid4().hex}"
    descriptor = os.open(
        probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    written = 0
    try:
        while written <= max_bytes + 1024 * 1024:
            try:
                written += os.write(descriptor, b"x" * 65536)
            except OSError as exc:
                if exc.errno != errno.ENOSPC:
                    raise
                return written
        raise AssertionError("DB6 did not observe kernel ENOSPC within dedicated mount capacity")
    finally:
        os.close(descriptor)
        probe.unlink()


def test_db6_real_kernel_enospc_original_sqlite_backup(tmp_path: Path) -> None:
    requested = os.environ.get("QUALIFY_DB6_ENOSPC_ROOT", "")
    assert requested, "DB6 opt-in native mount path is required; no synthetic or skipped PASS"
    root = Path(requested)
    observed = _require_disposable_mount(root, Path("/proc/self/mountinfo").read_text())
    # SQLite source remains on the regular pytest filesystem, NEVER on the
    # capacity-limited disposable host mount. That mount contains only backup effects.
    assert root.stat().st_dev != tmp_path.stat().st_dev, (
        "DB6 source and fault target share filesystem"
    )
    owner = LocalDatabase(tmp_path / "original.sqlite3", journal_mode="DELETE")
    with owner.transaction(write=True) as connection:
        # Use a schema-authorized original domain table (not a new SQL owner).
        # Distinct live rows exceed the mount's available capacity without using
        # SQLite max_page_count, mocked writes or an application-level fake errno.
        for index in range(24):
            connection.execute(
                "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
                (f"/db6/{index}/" + "A" * (1024 * 1024), index, "native"),
            )
    assert owner.path.stat().st_size > int(observed["free_bytes_before"]) * 2
    destination = root / "candidate.sqlite3"
    try:
        owner.export_verified_backup(destination)
    except sqlite3.OperationalError as exc:
        assert exc.sqlite_errorcode == sqlite3.SQLITE_FULL, (
            f"DB6 expected SQLite FULL from kernel mount: {exc}"
        )
    else:
        raise AssertionError("DB6 capacity-limited mount unexpectedly published backup")
    assert not destination.exists(), (
        "DB6 cannot acknowledge or leave a published backup on full mount"
    )
    assert not list(root.iterdir()), "DB6 original owner failed to clean temporary snapshot"
    written = _kernel_enospc_probe(root, int(observed["total_bytes"]))
    assert not list(root.iterdir()), "DB6 probe did not clean dedicated mount"
    with owner.transaction() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM locations WHERE path LIKE '/db6/%'"
            ).fetchone()[0]
            == 24
        )
    print(
        "DB6_REAL_KERNEL_ENOSPC="
        + json.dumps(
            {
                **observed,
                "source_database_bytes": owner.path.stat().st_size,
                "kernel_errno": errno.ENOSPC,
                "probe_bytes": written,
                "original_owner_reopened": True,
                "backup_published": False,
                "evidence_class": "NATIVE_DEVELOPMENT_KERNEL_ENOSPC_ONLY_NOT_FSYNCH_EIO_OR_GATE",
            },
            sort_keys=True,
        ),
        flush=True,
    )
