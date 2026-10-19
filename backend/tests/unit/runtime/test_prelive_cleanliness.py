from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from oh_goon.core.errors import DatabaseSchemaMismatch
from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION, LocalDatabase


def test_prelive_database_uses_one_genesis_schema(tmp_path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    assert DATABASE_SCHEMA_VERSION == 88
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
