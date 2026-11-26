from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from oh_goon.core.errors import StateConflict
from oh_goon.core.path_authority import ensure_private_directory
from oh_goon.core.path_layouts import ApplicationStatePathLayout

_LOCK_SHARDS = 64


@contextmanager
def serialize_run_admission(database_path: Path, identity: str) -> Generator[None, None, None]:
    """Serialize one caller-stable admission identity across threads and processes.

    The lock is deliberately ephemeral kernel authority, not durable Fleet state. A bounded
    shard set avoids one lock file per historical run while still allowing unrelated admissions
    to proceed concurrently. Linux releases ``flock`` ownership automatically on process death.
    """
    with _serialize(database_path.parent / ".admission-locks", identity):
        yield


@contextmanager
def serialize_branch_handoff(
    database_path: Path, checkout_subject: str
) -> Generator[None, None, None]:
    """Keep checkout synchronization and the original Run admission in one local turn."""
    layout = ApplicationStatePathLayout.from_database(database_path)
    with _serialize(layout.branch_handoff_locks, checkout_subject):
        yield


@contextmanager
def _serialize(directory: Path, identity: str) -> Generator[None, None, None]:
    if not identity:
        raise StateConflict("Admission serialization requires a non-empty identity")
    lock_dir = ensure_private_directory(directory)
    shard = (
        int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest()[:8], "big") % _LOCK_SHARDS
    )
    path = lock_dir / f"admission-{shard:02d}.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StateConflict(f"Fleet admission lock is not a regular file: {path}")
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
