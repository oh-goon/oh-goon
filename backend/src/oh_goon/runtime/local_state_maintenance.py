from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

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


def reset_local_state(path: Path) -> tuple[Path, ...]:
    """Delete only the explicitly selected database and its SQLite sidecar files."""

    files = sqlite_state_files(path)
    if not any(candidate.exists() or candidate.is_symlink() for candidate in files):
        return ()
    deleted: list[Path] = []
    with sqlite_authority_gate(path, write=True):
        for candidate in files:
            if remove_file_durably(candidate):
                deleted.append(candidate)
    return tuple(deleted)


def mismatch_message(path: Path, *, found: int | str, expected: int) -> str:
    return (
        f"Local oh-goon database schema mismatch: found {found}; expected {expected}.\n"
        f"Database: {path}\n"
        "This pre-live build intentionally does not migrate old local state.\n"
        "Reset the disposable local database with the command owned by the path you are running:\n"
        f"  Source checkout: {SOURCE_RESET_COMMAND}\n"
        f"  Built release:   {BUILT_RELEASE_RESET_COMMAND}\n"
        "Do not mix a global/older oh-goon binary with a source checkout."
    )
