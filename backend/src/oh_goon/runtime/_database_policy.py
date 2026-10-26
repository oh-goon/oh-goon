from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path

from oh_goon.core.sqlite_journal_vocabulary import (
    SQLITE_JOURNAL_AUTO,
    SQLITE_JOURNAL_DELETE,
    SQLITE_JOURNAL_MODE_ENV,
    SQLITE_JOURNAL_WAL,
)
from oh_goon.runtime._sqlite_authority_gate import sqlite_authority_gate
from oh_goon.runtime.local_state_maintenance import LocalStateCompatibility, LocalStateInspection
from oh_goon.runtime.schema_version_policy import DurableSchemaDisposition, DurableSchemaPolicy

# Schema 91 retires cancellation-native claims and unbound physical facts.
DATABASE_SCHEMA_VERSION = 91
DATABASE_SCHEMA_POLICY = DurableSchemaPolicy(
    name="Local oh-goon database", current=DATABASE_SCHEMA_VERSION
)
DEFAULT_DATABASE_NAME = "oh-goon.db"
MIN_SAFE_WAL_SQLITE_VERSION = (3, 51, 3)
_UNSAFE_WAL_FILESYSTEMS = frozenset({"9p", "drvfs", "cifs", "smb3", "nfs", "nfs4"})


def _decode_mountinfo_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _filesystem_type_for_path(path: Path) -> str | None:
    """Return Linux mount filesystem type for an existing path, if provable."""

    if sys.platform != "linux":
        return None
    candidate = path if path.exists() else path.parent
    try:
        resolved = candidate.resolve()
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    best: tuple[int, str] | None = None
    for line in lines:
        if " - " not in line:
            continue
        before, after = line.split(" - ", 1)
        left = before.split()
        right = after.split()
        if len(left) < 5 or not right:
            continue
        mount = Path(_decode_mountinfo_path(left[4]))
        try:
            resolved.relative_to(mount)
        except ValueError:
            continue
        rank = len(mount.parts)
        if best is None or rank > best[0]:
            best = (rank, right[0].lower())
    return best[1] if best is not None else None


def database_filesystem_type(path: Path) -> str | None:
    """Expose the observed filesystem type for database-placement preflight."""

    return _filesystem_type_for_path(path)


def database_filesystem_is_local(path: Path) -> bool:
    """Require conservative local-filesystem semantics for certification control state."""

    filesystem = _filesystem_type_for_path(path)
    if filesystem is None:
        return False
    return filesystem not in _UNSAFE_WAL_FILESYSTEMS and not filesystem.startswith("fuse.")


def _wal_filesystem_safe(path: Path) -> bool:
    filesystem = _filesystem_type_for_path(path)
    if filesystem is None:
        # AUTO never assumes WAL safety when mount semantics cannot be proved.
        return False
    if filesystem in _UNSAFE_WAL_FILESYSTEMS or filesystem.startswith("fuse."):
        return False
    return True


def sqlite_journal_mode_for_path(
    path: Path,
    *,
    requested: str | None = None,
    sqlite_version: tuple[int, int, int] | None = None,
) -> str:
    """Resolve one SQLite journal policy without silently trusting unsafe WAL semantics.

    AUTO uses WAL only when both the SQLite runtime and filesystem are positively
    known safe. Certification explicitly requests DELETE because its outer workflow
    lease already supplies single-controller ownership and does not need WAL.
    """

    raw = (
        requested
        if requested is not None
        else os.environ.get(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_AUTO)
    )
    mode = raw.strip().upper() or SQLITE_JOURNAL_AUTO
    if mode not in {SQLITE_JOURNAL_AUTO, SQLITE_JOURNAL_DELETE, SQLITE_JOURNAL_WAL}:
        raise RuntimeError(f"{SQLITE_JOURNAL_MODE_ENV} must be AUTO, DELETE, or WAL; got {raw!r}")
    version = tuple(sqlite_version or sqlite3.sqlite_version_info[:3])
    safe_wal = version >= MIN_SAFE_WAL_SQLITE_VERSION and _wal_filesystem_safe(path)
    if mode == SQLITE_JOURNAL_AUTO:
        return SQLITE_JOURNAL_WAL if safe_wal else SQLITE_JOURNAL_DELETE
    if mode == SQLITE_JOURNAL_WAL and not safe_wal:
        filesystem = _filesystem_type_for_path(path) or "unproven"
        raise RuntimeError(
            "SQLite WAL mode was explicitly requested but is not admitted on this runtime/storage "
            f"combination (sqlite={'.'.join(map(str, version))}, filesystem={filesystem}); "
            "use DELETE or move state to a proven local filesystem with SQLite >= 3.51.3"
        )
    return mode


def database_path_for_environment(environment: Mapping[str, str]) -> Path:
    """Resolve the operational database path from one explicit environment.

    This is the canonical path-selection authority for maintenance code that must not
    temporarily mutate process-global ``os.environ`` just to inspect another XDG root.
    """

    override = environment.get("OH_GOON_DB")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and environment.get("LOCALAPPDATA"):
        base = Path(environment["LOCALAPPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        data_home = environment.get("XDG_DATA_HOME")
        base = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return (base / "oh-goon" / DEFAULT_DATABASE_NAME).resolve()


def default_database_path() -> Path:
    """Return the configured local operational database path."""

    return database_path_for_environment(os.environ)


def _require_database_integrity(
    connection: sqlite3.Connection,
    *,
    quick_check: bool,
    integrity_check: bool,
    foreign_key_check: bool,
) -> None:
    if integrity_check:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
        if len(rows) != 1 or str(rows[0][0]).lower() != "ok":
            detail = "; ".join(str(row[0]) for row in rows[:5]) or "no result"
            raise sqlite3.DatabaseError("SQLite integrity_check failed: " + detail)
    elif quick_check:
        row = connection.execute("PRAGMA quick_check").fetchone()
        if row is None or str(row[0]).lower() != "ok":
            raise sqlite3.DatabaseError(
                "SQLite quick_check failed: " + (str(row[0]) if row else "no result")
            )
    if foreign_key_check:
        violations = connection.execute("PRAGMA foreign_key_check").fetchmany(5)
        if violations:
            detail = "; ".join(
                f"table={row[0]} rowid={row[1]} parent={row[2]} fk={row[3]}" for row in violations
            )
            raise sqlite3.DatabaseError("SQLite foreign_key_check failed: " + detail)


def _read_database_inspection(
    path: Path, *, quick_check: bool, integrity_check: bool, foreign_key_check: bool
) -> tuple[int, object | None]:
    if path.is_symlink() or not path.is_file():
        raise sqlite3.DatabaseError("database path is not a regular non-symlink file")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        existing_tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()
        _require_database_integrity(
            connection,
            quick_check=quick_check,
            integrity_check=integrity_check,
            foreign_key_check=foreign_key_check,
        )
        return version, existing_tables
    finally:
        connection.close()


def _new_local_state(path: Path, expected_schema_version: int) -> LocalStateInspection:
    return LocalStateInspection(
        path=path,
        status=LocalStateCompatibility.NEW,
        found_schema_version=None,
        expected_schema_version=expected_schema_version,
        detail="No local database exists yet; startup will create it.",
    )


def inspect_database_path(
    path: Path,
    *,
    expected_schema_version: int = DATABASE_SCHEMA_VERSION,
    quick_check: bool = False,
    integrity_check: bool = False,
    foreign_key_check: bool = False,
) -> LocalStateInspection:
    """Read one exact SQLite path under the canonical shared authority gate."""

    path = path.expanduser().resolve()
    if not path.exists():
        return _new_local_state(path, expected_schema_version)
    try:
        with sqlite_authority_gate(path, write=False):
            if not path.exists():
                return _new_local_state(path, expected_schema_version)
            version, existing_tables = _read_database_inspection(
                path,
                quick_check=quick_check,
                integrity_check=integrity_check,
                foreign_key_check=foreign_key_check,
            )
    except (OSError, sqlite3.Error) as exc:
        return LocalStateInspection(
            path=path,
            status=LocalStateCompatibility.INVALID,
            found_schema_version=None,
            expected_schema_version=expected_schema_version,
            detail=f"SQLite could not read the local database: {exc}",
        )

    policy = (
        DATABASE_SCHEMA_POLICY
        if expected_schema_version == DATABASE_SCHEMA_VERSION
        else DurableSchemaPolicy(name="Local oh-goon database", current=expected_schema_version)
    )
    disposition = policy.classify(version)
    if disposition is DurableSchemaDisposition.CURRENT:
        return LocalStateInspection(
            path=path,
            status=LocalStateCompatibility.READY,
            found_schema_version=version,
            expected_schema_version=expected_schema_version,
            detail="Local database schema matches this build.",
        )
    if version == 0 and existing_tables is not None:
        return LocalStateInspection(
            path=path,
            status=LocalStateCompatibility.UNVERSIONED,
            found_schema_version=version,
            expected_schema_version=expected_schema_version,
            detail="The local database has tables but no supported pre-live schema epoch.",
        )
    if version == 0:
        return LocalStateInspection(
            path=path,
            status=LocalStateCompatibility.NEW,
            found_schema_version=version,
            expected_schema_version=expected_schema_version,
            detail="The local database is empty and can be initialized by this build.",
        )
    if disposition is DurableSchemaDisposition.FUTURE:
        return LocalStateInspection(
            path=path,
            status=LocalStateCompatibility.FUTURE,
            found_schema_version=version,
            expected_schema_version=expected_schema_version,
            detail=policy.version_status_text(version),
        )
    return LocalStateInspection(
        path=path,
        status=LocalStateCompatibility.STALE,
        found_schema_version=version,
        expected_schema_version=expected_schema_version,
        detail=policy.version_status_text(version),
    )


def inspect_local_database(
    *,
    expected_schema_version: int = DATABASE_SCHEMA_VERSION,
    quick_check: bool = False,
    integrity_check: bool = False,
    foreign_key_check: bool = False,
) -> LocalStateInspection:
    """Read only local SQLite compatibility without constructing the application runtime."""

    return inspect_database_path(
        default_database_path(),
        expected_schema_version=expected_schema_version,
        quick_check=quick_check,
        integrity_check=integrity_check,
        foreign_key_check=foreign_key_check,
    )
