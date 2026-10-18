from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from oh_goon.core.errors import StateConflict
from oh_goon.core.path_authority import ensure_private_directory

_LOCK_SHARDS = 64


@contextmanager
def serialize_run_admission(database_path: Path, identity: str) -> Iterator[None]:
    """Serialize one caller-stable admission identity across threads and processes.

    The lock is deliberately ephemeral kernel authority, not durable Fleet state. A bounded
    shard set avoids one lock file per historical run while still allowing unrelated admissions
    to proceed concurrently. Linux releases ``flock`` ownership automatically on process death.
    """
    if not identity:
        raise StateConflict("Fleet admission serialization requires a non-empty identity")
    lock_dir = ensure_private_directory(database_path.parent / ".admission-locks")
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
