from __future__ import annotations

import errno
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from oh_goon.core.path_authority import ensure_private_directory
from oh_goon.runtime._sqlite_authority_gate import sqlite_authority_gate

_STORAGE_ERRNOS = {
    errno.EIO,
    errno.ENOSPC,
    errno.EROFS,
    getattr(errno, "EDQUOT", -1),
}
_SQLITE_STORAGE_MESSAGES = (
    "unable to open database file",
    "attempt to write a readonly database",
    "attempt to write a read-only database",
    "database or disk is full",
    "disk i/o error",
    "readonly database",
    "read-only database",
)


DEFAULT_STORAGE_SOFT_FREE_BYTES = 20 * 1024**3
DEFAULT_STORAGE_HARD_FREE_BYTES = 5 * 1024**3
STORAGE_SOFT_FREE_BYTES_ENV = "OH_GOON_STORAGE_SOFT_FREE_BYTES"
STORAGE_HARD_FREE_BYTES_ENV = "OH_GOON_STORAGE_HARD_FREE_BYTES"


@dataclass(frozen=True, slots=True)
class StorageCapacityPolicy:
    soft_free_bytes: int
    hard_free_bytes: int


@dataclass(frozen=True, slots=True)
class StorageCapacity:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    pressure: str


def _positive_storage_bytes(environment: dict[str, str], name: str, default: int) -> int:
    raw = environment.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer byte count") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer byte count")
    return value


def resolve_storage_capacity_policy(environment: dict[str, str]) -> StorageCapacityPolicy:
    """Resolve the machine-safety floor once before heavy local materialization."""

    soft = _positive_storage_bytes(
        environment, STORAGE_SOFT_FREE_BYTES_ENV, DEFAULT_STORAGE_SOFT_FREE_BYTES
    )
    hard = _positive_storage_bytes(
        environment, STORAGE_HARD_FREE_BYTES_ENV, DEFAULT_STORAGE_HARD_FREE_BYTES
    )
    if hard > soft:
        raise ValueError(f"{STORAGE_HARD_FREE_BYTES_ENV} must be <= {STORAGE_SOFT_FREE_BYTES_ENV}")
    return StorageCapacityPolicy(soft_free_bytes=soft, hard_free_bytes=hard)


def observe_storage_capacity(path: Path, policy: StorageCapacityPolicy) -> StorageCapacity:
    usage = shutil.disk_usage(path)
    if usage.free < policy.hard_free_bytes:
        pressure = "HARD_FLOOR"
    elif usage.free < policy.soft_free_bytes:
        pressure = "SOFT_PRESSURE"
    else:
        pressure = "HEALTHY"
    return StorageCapacity(
        total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free, pressure=pressure
    )


def require_storage_capacity(path: Path, policy: StorageCapacityPolicy) -> StorageCapacity:
    capacity = observe_storage_capacity(path, policy)
    if capacity.pressure == "HARD_FLOOR":
        raise OSError(
            errno.ENOSPC,
            "Oh-Goon storage safety floor blocks new heavy materialization: "
            f"free={capacity.free_bytes} hard_floor={policy.hard_free_bytes}",
            str(path),
        )
    return capacity


def storage_unavailable_reason(exc: BaseException) -> str | None:
    """Classify only concrete host-storage loss; never reinterpret arbitrary app errors."""

    if isinstance(exc, OSError) and exc.errno in _STORAGE_ERRNOS:
        return str(exc) or exc.__class__.__name__
    if isinstance(exc, sqlite3.OperationalError):
        message = str(exc).strip()
        lowered = message.lower()
        if any(token in lowered for token in _SQLITE_STORAGE_MESSAGES):
            return message or exc.__class__.__name__
    return None


def probe_writable_directory(path: Path) -> None:
    """Prove one already-owned mutable state directory can create and persist a byte.

    This is a liveness/admission probe, not durable evidence.  It intentionally does not
    create policy about *where* state lives; the caller remains the path owner.
    """

    root = path.resolve()
    probe = root / f".oh-goon-storage-probe-{os.getpid()}-{uuid4().hex}"
    descriptor = -1
    try:
        descriptor = os.open(
            probe,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        os.write(descriptor, b"1")
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            # Preserve the primary create/write/fsync failure. If the probe succeeded but
            # cleanup fails, surface that failure because the state owner also lacks delete
            # authority and therefore is not healthy enough for certification.
            if descriptor >= 0:
                raise


def _probe_existing_sqlite(database: Path) -> None:
    if database.is_symlink() or not database.is_file():
        raise sqlite3.DatabaseError(
            "database path is not a regular non-symlink file for prelaunch write proof"
        )
    uri = f"file:{database.as_posix()}?mode=rw"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
    finally:
        connection.close()


def _probe_missing_sqlite(parent: Path) -> None:
    probe = parent / f".oh-goon-sqlite-prelaunch-{os.getpid()}-{uuid4().hex}.db"
    sidecars = (probe, Path(f"{probe}-journal"), Path(f"{probe}-wal"), Path(f"{probe}-shm"))
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(probe, timeout=5.0)
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("CREATE TABLE prelaunch_probe(value TEXT NOT NULL)")
        connection.execute("INSERT INTO prelaunch_probe(value) VALUES ('oh-goon')")
        connection.commit()
        connection.close()
        connection = None

        uri = f"file:{probe.as_posix()}?mode=rw"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        row = connection.execute("SELECT value FROM prelaunch_probe").fetchone()
        if row != ("oh-goon",):
            raise sqlite3.DatabaseError("SQLite prelaunch sibling round-trip lost committed data")
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]).lower() != "ok":
            raise sqlite3.DatabaseError(
                "SQLite prelaunch sibling quick_check failed: "
                + (str(quick[0]) if quick else "no result")
            )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.DatabaseError(
                "SQLite prelaunch sibling foreign_key_check returned a violation"
            )
    finally:
        if connection is not None:
            connection.close()
        cleanup_error: OSError | None = None
        for candidate in sidecars:
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise cleanup_error


def probe_sqlite_prelaunch(path: Path) -> None:
    """Prove SQLite write/lock semantics without mutating the selected database."""

    database = path.expanduser().resolve()
    parent = ensure_private_directory(database.parent)
    probe_writable_directory(parent)
    with sqlite_authority_gate(database, write=True):
        if database.exists():
            _probe_existing_sqlite(database)
        else:
            _probe_missing_sqlite(parent)
