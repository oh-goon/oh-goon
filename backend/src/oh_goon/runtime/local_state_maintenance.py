from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from oh_goon.core.errors import DatabaseSchemaMismatch, StateConflict
from oh_goon.integrations.exact_file_write import remove_file_durably
from oh_goon.runtime._sqlite_authority_gate import sqlite_authority_gate

SOURCE_RESET_COMMAND = "make local-state-reset"
BUILT_RELEASE_RESET_COMMAND = "oh-goon local-state reset --yes"


class LocalStateCompatibility(StrEnum):
    NEW = "NEW"
    READY = "READY"
    STALE = "STALE"
    FUTURE = "FUTURE"
    UNVERSIONED = "UNVERSIONED"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class LocalStateInspection:
    path: Path
    status: LocalStateCompatibility
    found_schema_version: int | None
    expected_schema_version: int
    detail: str

    @property
    def compatible(self) -> bool:
        return self.status in {LocalStateCompatibility.NEW, LocalStateCompatibility.READY}


def sqlite_state_files(path: Path) -> tuple[Path, ...]:
    """Return the exact local SQLite files owned by one oh-goon database path."""

    return (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    )


def _require_settled_reset_authority(path: Path, connection: sqlite3.Connection) -> None:
    """Inspect existing owners in one read transaction before any deletion."""
    from oh_goon.runtime._database_policy import DATABASE_SCHEMA_VERSION
    from oh_goon.runtime.journal_store import JournalFactStore
    from oh_goon.runtime.journal_view import JournalReadView
    from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
    from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != DATABASE_SCHEMA_VERSION:
        raise DatabaseSchemaMismatch(
            mismatch_message(path, found=version, expected=DATABASE_SCHEMA_VERSION)
        )
    if int(connection.execute("PRAGMA application_id").fetchone()[0]) == 0:
        raise StateConflict(
            "Local-state reset refuses a store without its original application identity"
        )
    integrity = connection.execute("PRAGMA quick_check").fetchone()
    if (
        integrity is None
        or str(integrity[0]).lower() != "ok"
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
    ):
        raise StateConflict(
            "Local-state reset refuses an invalid store; preserve its original evidence"
        )
    view = JournalReadView.from_entries(
        tuple(JournalFactStore.load_entries(connection)), "local-state-reset"
    )
    if view.active_run() is not None:
        raise StateConflict(
            "Local-state reset refuses active or unresolved Runs; settle them through the original lifecycle"
        )
    if any(item.terminal_decision is None for item in view.campaigns()):
        raise StateConflict(
            "Local-state reset refuses an incomplete campaign controller; retain its original Journal"
        )
    if any(
        operation.resource_settlement == "UNRESOLVED"
        for detail in view.run_details()
        for operation in detail.operations
    ):
        raise StateConflict(
            "Local-state reset refuses unresolved original Step cleanup; retain its canonical process/resource evidence"
        )
    if ScheduleExecutionStore.active_in(connection):
        raise StateConflict(
            "Local-state reset refuses active Schedule occurrences; settle their original children first"
        )
    # Presence retains authority even when a process/lease is old. Only the
    # original lifecycle/recovery owners may settle these durable records.
    for table in (
        "execution_lease",
        "executor_placement_queue",
        "executor_capacity_reservations",
        "repository_effect_attempts",
    ):
        if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
            raise StateConflict(
                f"Local-state reset refuses unsettled {table} authority; use its original lifecycle owner"
            )
    RepositoryWorkspaceAuthority.require_reset_safe_in(connection)


def reset_local_state(path: Path) -> tuple[Path, ...]:
    """Delete an explicitly selected, compatible and proven-settled control store."""
    selected = path.expanduser().absolute()
    if selected.is_symlink() or selected.parent.resolve() != selected.parent:
        raise StateConflict(
            "Local-state reset requires its exact regular, non-symlink database path"
        )
    files = sqlite_state_files(selected)
    if not any(candidate.exists() or candidate.is_symlink() for candidate in files):
        return ()
    deleted: list[Path] = []
    with sqlite_authority_gate(selected, write=True):
        if not selected.is_file() or any(candidate.is_symlink() for candidate in files):
            raise StateConflict(
                "Local-state reset cannot prove the original database/sidecar authority; nothing was deleted"
            )
        try:
            from oh_goon.runtime.database import LocalDatabase

            with LocalDatabase.observe_existing_for_reset(selected) as connection:
                _require_settled_reset_authority(selected, connection)
                # Keep the original gate and SQLite read transaction throughout
                # deletion so no product writer can cross the inspected cut.
                for candidate in files:
                    if remove_file_durably(candidate):
                        deleted.append(candidate)
        except sqlite3.Error as exc:
            raise StateConflict(
                "Local-state reset could not verify its original store; preserve all state and use the matching runtime"
            ) from exc
    return tuple(deleted)


def mismatch_message(path: Path, *, found: int | str, expected: int) -> str:
    return (
        f"Local oh-goon database schema mismatch: found {found}; expected {expected}.\n"
        f"Database: {path}\n"
        "This build intentionally does not migrate or reset incompatible local state.\n"
        "Stop the original runtime and preserve this database with its SQLite sidecars.\n"
        "Use the matching original runtime to settle its Runs, effects and workspace writers before archival or replacement.\n"
        "A fresh state path does not settle an original unresolved writer.\n"
        "Reset commands apply only to compatible, settled state:\n"
        f"  Source checkout: {SOURCE_RESET_COMMAND}\n"
        f"  Built release:   {BUILT_RELEASE_RESET_COMMAND}\n"
        "Do not mix a global/older oh-goon binary with a source checkout."
    )
