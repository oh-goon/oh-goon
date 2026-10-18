from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from oh_goon.core.errors import LeaguePreferencesInvalid, StateConflict
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import read_durable_revision, require_durable_revision
from oh_goon.runtime.exclusions import (
    delete_exclusion,
    read_exclusion,
    read_exclusions,
    write_exclusion,
)
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.journal_view import JournalReadView
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.runtime.scope_mutation_guard import DurableScopeMutationGuard


@dataclass(frozen=True, slots=True)
class LocalStateReadView:
    """One transactional Fleet-state cut over locations, exclusions, and Journal facts."""

    durable_revision: int
    locations: tuple[Path, ...]
    exclusions: Mapping[str, Mapping[str, str]]
    journal: JournalReadView


def locations_revision(locations: tuple[Path, ...]) -> str:
    payload = "\0".join(str(path) for path in locations).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class LocalState:
    """SQLite runtime-state owner with a projection of durable League-folder preferences."""

    def __init__(
        self,
        database: LocalDatabase,
        *,
        journal: Journal,
        mutation_guard: DurableScopeMutationGuard,
        league_folders: LeagueFolderStore,
    ) -> None:
        self.database = database
        self.journal = journal
        self._mutation_guard = mutation_guard
        self._league_folders = league_folders

    def read_view(self) -> LocalStateReadView:
        with self.database.transaction() as connection:
            return self.read_view_in(connection)

    def read_view_in(
        self,
        connection: sqlite3.Connection,
        *,
        journal: JournalReadView | None = None,
    ) -> LocalStateReadView:
        """Read Fleet scope and Journal facts from an existing durable transaction."""
        durable_revision = read_durable_revision(connection)
        locations = tuple(self._locations(connection))
        exclusions = {
            record["path"]: MappingProxyType(
                {"reason": record["reason"], "excluded_at": record["excluded_at"]}
            )
            for record in read_exclusions(connection)
        }
        captured_journal = journal or self.journal.read_view_from_connection(connection)
        return LocalStateReadView(
            durable_revision=durable_revision,
            locations=locations,
            exclusions=MappingProxyType(exclusions),
            journal=captured_journal,
        )

    def reconcile_league_folders(self) -> list[Path]:
        """Restore the disposable runtime projection from durable League preferences."""
        with self._league_folders.write_lock():
            folders = self._league_folders.list()
            try:
                with self.database.transaction(write=True) as connection:
                    projected = tuple(self._locations(connection))
                    if projected != folders:
                        self._replace_locations(connection, folders)
            except BaseException:
                if self._projection_after_ambiguous_preference_write() == folders:
                    return list(folders)
                raise
            return list(folders)

    def add_location(self, path: Path) -> list[Path]:
        resolved = path.expanduser().resolve()
        return self._commit_league_folders(
            lambda folders: folders if resolved in folders else (*folders, resolved)
        )

    def remove_location(self, path: Path, *, expected_revision: str) -> list[Path]:
        resolved = path.expanduser().resolve()
        return self._commit_league_folders(
            lambda folders: tuple(item for item in folders if item != resolved),
            expected_revision=expected_revision,
        )

    def _commit_league_folders(
        self,
        change: Callable[[tuple[Path, ...]], tuple[Path, ...]],
        *,
        expected_revision: str | None = None,
    ) -> list[Path]:
        """Publish one League-folder preference change only with its SQLite projection."""
        previous_folders: tuple[Path, ...] = ()
        preference_published = False
        with self._league_folders.write_lock():
            try:
                with self.database.transaction(write=True) as connection:
                    self._mutation_guard.require_idle(connection)
                    previous_folders = self._league_folders.list()
                    if (
                        expected_revision is not None
                        and locations_revision(previous_folders) != expected_revision
                    ):
                        raise StateConflict(
                            "Configured locations changed since they were displayed"
                        )
                    folders = change(previous_folders)
                    projected = tuple(self._locations(connection))
                    if projected != folders:
                        self._replace_locations(connection, folders)
                    if folders != previous_folders:
                        self._league_folders.replace_locked(folders)
                        preference_published = True
            except BaseException as exc:
                if not preference_published:
                    raise
                committed_projection = self._projection_after_ambiguous_preference_write()
                if committed_projection == folders:
                    return list(folders)
                if committed_projection != previous_folders:
                    raise LeaguePreferencesInvalid(
                        "League preference/runtime projection diverged after an ambiguous SQLite response; "
                        "league.yml remains authoritative, so restart before admitting more work"
                    ) from exc
                try:
                    self._league_folders.replace_locked(previous_folders)
                except BaseException as restore_exc:
                    raise LeaguePreferencesInvalid(
                        "League preference rollback failed after the runtime projection write failed; "
                        "restart before admitting more work"
                    ) from restore_exc
                raise
        return list(folders)

    def _projection_after_ambiguous_preference_write(self) -> tuple[Path, ...]:
        """Verify which SQLite projection committed without promoting it to preference authority."""
        try:
            with self.database.transaction() as connection:
                return tuple(self._locations(connection))
        except BaseException as verify_exc:
            raise LeaguePreferencesInvalid(
                "Cannot verify League preference publication after an ambiguous SQLite response; "
                "league.yml remains authoritative, so restart before admitting more work"
            ) from verify_exc

    def exclude(
        self,
        repository: Path | str,
        reason: str | None = None,
        *,
        expected_durable_revision: int,
    ) -> dict[str, str]:
        record: dict[str, str] | None = None
        try:
            with self.database.transaction(write=True) as connection:
                require_durable_revision(connection, expected_durable_revision)
                self._mutation_guard.require_idle(connection)
                record = write_exclusion(connection, repository, reason)
        except BaseException:
            if record is None:
                raise
            revision, canonical = self._scope_mutation_after_ambiguous_write(repository)
            if revision == expected_durable_revision + 1 and canonical == record:
                return record
            raise
        return record

    def include(
        self,
        repository: Path | str,
        *,
        expected_durable_revision: int,
    ) -> bool:
        deleted: bool | None = None
        try:
            with self.database.transaction(write=True) as connection:
                require_durable_revision(connection, expected_durable_revision)
                self._mutation_guard.require_idle(connection)
                deleted = delete_exclusion(connection, repository)
        except BaseException:
            if deleted is not True:
                raise
            revision, canonical = self._scope_mutation_after_ambiguous_write(repository)
            if revision == expected_durable_revision + 1 and canonical is None:
                return True
            raise
        return bool(deleted)

    def _scope_mutation_after_ambiguous_write(
        self, repository: Path | str
    ) -> tuple[int, dict[str, str] | None]:
        """Read exact exclusion state only to resolve one ambiguous committed mutation."""
        with self.database.transaction() as connection:
            return read_durable_revision(connection), read_exclusion(connection, repository)

    def exclusions(self) -> list[dict[str, str]]:
        with self.database.transaction() as connection:
            return read_exclusions(connection)

    @staticmethod
    def _replace_locations(connection: sqlite3.Connection, folders: tuple[Path, ...]) -> None:
        connection.execute("DELETE FROM locations")
        added_at = datetime.now(UTC).isoformat(timespec="seconds")
        connection.executemany(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            ((str(path), position, added_at) for position, path in enumerate(folders, start=1)),
        )

    @staticmethod
    def _locations(connection: sqlite3.Connection) -> list[Path]:
        rows = connection.execute("SELECT path FROM locations ORDER BY position").fetchall()
        return [Path(str(row[0])) for row in rows]
