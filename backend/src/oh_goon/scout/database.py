from __future__ import annotations

import errno
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from oh_goon.core.path_authority import ensure_private_directory


def scout_analytical_database_path(state_database_path: Path | str) -> Path:
    """Return the disposable Scout sidecar path next to canonical local state."""
    path = Path(state_database_path)
    return path.with_name(f"{path.stem}.scout{path.suffix or '.sqlite3'}")


class ScoutAnalyticalDatabase:
    """SQLite boundary for disposable Scout-derived state only."""

    def __init__(self, state_database_path: Path | str) -> None:
        canonical = Path(state_database_path).expanduser().resolve()
        self.operational_path = canonical
        self.path = scout_analytical_database_path(canonical)
        if self.path == canonical:
            raise RuntimeError("Scout analytical database must be separate from canonical state")

    @contextmanager
    def transaction(
        self, *, write: bool = False, track_revision: bool = False
    ) -> Iterator[sqlite3.Connection]:
        del track_revision
        connection = self._connect(write=write)
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
            self._harden_sidecar_files()

    def recover_if_corrupt(self, error: sqlite3.DatabaseError) -> bool:
        """Discard only a genuinely corrupt disposable sidecar so Journal truth can rebuild it."""
        if not _is_corruption(error):
            return False
        self.remove()
        return True

    def remove(self) -> None:
        """Delete disposable Scout files without following attacker-controlled symlinks."""
        self._require_disposable_identity()
        for candidate in reversed(self._sidecar_files()):
            candidate.unlink(missing_ok=True)

    def truncate_wal(self) -> None:
        """Checkpoint retained Scout state so deleted metadata does not linger in the WAL."""
        connection = self._connect(write=True)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
        self._harden_sidecar_files()

    def storage_bytes(self) -> int:
        """Return a race-safe on-disk footprint of the disposable Scout database family."""
        self._require_disposable_identity()
        return sum(
            _regular_file_size_if_present(candidate, transient=candidate != self.path)
            for candidate in self._sidecar_files()
        )

    def _connect(self, *, write: bool) -> sqlite3.Connection:
        ensure_private_directory(self.path.parent)
        self._require_disposable_identity()
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA busy_timeout = 30000")
            if write:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.execute("PRAGMA secure_delete = ON")
            else:
                connection.execute("PRAGMA query_only = ON")
            self._harden_sidecar_files()
            return connection
        except BaseException:
            connection.close()
            raise

    def _require_disposable_identity(self) -> None:
        """Never let a Scout file alias any operational SQLite inode.

        Path spelling and symlink checks are insufficient: a pre-existing hardlink
        can silently turn disposable Scout writes or deletion into operational
        mutations. This check is a pre-effect fence, not protection from a
        hostile process able to race path replacement in a shared directory.
        """
        operational = (
            self.operational_path,
            *(
                Path(f"{self.operational_path}{extension}")
                for extension in ("-wal", "-shm", "-journal")
            ),
        )
        identities: set[tuple[int, int]] = set()
        for path in operational:
            try:
                meta = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(meta.st_mode):
                identities.add((meta.st_dev, meta.st_ino))
        for candidate in self._sidecar_files():
            _reject_unsafe_existing_file(candidate)
            try:
                meta = candidate.lstat()
            except FileNotFoundError:
                continue
            if (meta.st_dev, meta.st_ino) in identities:
                raise RuntimeError("Scout derived file aliases operational SQLite authority")

    def _sidecar_files(self) -> tuple[Path, Path, Path]:
        return self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")

    def _harden_sidecar_files(self) -> None:
        if os.name == "nt":
            return
        for candidate in self._sidecar_files():
            _harden_existing_regular_file(candidate, transient=candidate != self.path)


def _regular_file_size_if_present(path: Path, *, transient: bool = False) -> int:
    """Read size from an opened regular file; transient WAL/SHM removal is normal."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    else:
        _reject_unsafe_existing_file(path)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return 0
    except OSError as error:
        if nofollow and getattr(error, "errno", None) == errno.ELOOP:
            raise RuntimeError(
                f"Scout analytical database path must be a regular non-symlink file: {path}"
            ) from error
        raise
    try:
        metadata = os.fstat(descriptor)
        if transient and stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 0:
            return 0
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError(
                f"Scout analytical database path must be a single-link regular file: {path}"
            )
        return int(metadata.st_size)
    finally:
        os.close(descriptor)


def _harden_existing_regular_file(path: Path, *, transient: bool = False) -> None:
    """Apply owner-only mode without following a concurrently replaced symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    else:
        _reject_unsafe_existing_file(path)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        # SQLite may remove transient WAL/SHM files as another connection closes.
        return
    except OSError as error:
        if nofollow and getattr(error, "errno", None) == errno.ELOOP:
            raise RuntimeError(
                f"Scout analytical database path must be a regular non-symlink file: {path}"
            ) from error
        raise
    try:
        metadata = os.fstat(descriptor)
        if transient and stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 0:
            # Native SQLite closed its last connection after our open. The
            # orphan descriptor is no longer the named WAL/SHM; never reopen
            # the path or harden a replacement inode.
            return
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError(
                f"Scout analytical database path must be a single-link regular file: {path}"
            )
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _reject_unsafe_existing_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise RuntimeError(
            f"Scout analytical database path must be a regular non-symlink, single-link file: {path}"
        )


def _is_corruption(error: sqlite3.DatabaseError) -> bool:
    corrupt_codes = {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}
    code = getattr(error, "sqlite_errorcode", None)
    if code in corrupt_codes:
        return True
    message = str(error).lower()
    return "malformed" in message or "not a database" in message
