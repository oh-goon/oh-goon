from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from oh_goon.core.errors import DatabaseSchemaMismatch
from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION, LocalDatabase


def test_prelive_database_uses_one_genesis_schema(tmp_path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    assert DATABASE_SCHEMA_VERSION == 89
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION


def test_previous_prelive_schema_epoch_is_rejected_instead_of_reused(tmp_path) -> None:
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA user_version = 5")
        connection.commit()
    with pytest.raises(DatabaseSchemaMismatch, match="Source checkout: make local-state-reset"):
        LocalDatabase(path)


def test_unversioned_nonempty_prelive_database_is_rejected(tmp_path) -> None:
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE stale_local_state (id INTEGER PRIMARY KEY)")
        connection.commit()
    with pytest.raises(DatabaseSchemaMismatch, match="unversioned pre-live database"):
        LocalDatabase(path)


def test_stale_prelive_database_is_rejected_instead_of_migrated(tmp_path) -> None:
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA user_version = 6")
        connection.commit()
    with pytest.raises(DatabaseSchemaMismatch, match="Source checkout: make local-state-reset"):
        LocalDatabase(path)


@pytest.mark.parametrize("original_mode,requested_mode", [("DELETE", "WAL"), ("WAL", "DELETE")])
def test_incompatible_store_is_refused_before_persistent_mode_or_schema_mutation(
    tmp_path,
    original_mode,
    requested_mode,
):
    path = tmp_path / "old-state.db"
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute(f"PRAGMA journal_mode = {original_mode}").fetchone()[0].upper()
            == original_mode
        )
        connection.execute("CREATE TABLE old_writer_evidence(value TEXT NOT NULL)")
        connection.execute("INSERT INTO old_writer_evidence VALUES ('unresolved original effect')")
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(DatabaseSchemaMismatch):
        LocalDatabase(path, journal_mode=requested_mode)
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].upper() == original_mode
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION - 1
        )
        assert (
            connection.execute("SELECT value FROM old_writer_evidence").fetchone()[0]
            == "unresolved original effect"
        )
