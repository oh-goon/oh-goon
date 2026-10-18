from __future__ import annotations

import sqlite3

from oh_goon.core.errors import RuntimeSafetyStale, StateMetadataMissing


def ensure_durable_revision_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS state_metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            durable_revision INTEGER NOT NULL CHECK (durable_revision >= 0)
        );
        INSERT OR IGNORE INTO state_metadata(singleton, durable_revision) VALUES (1, 0);
        """
    )


def read_durable_revision(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT durable_revision FROM state_metadata WHERE singleton = 1"
    ).fetchone()
    if row is None:
        raise StateMetadataMissing("oh-goon state metadata is missing")
    return int(row[0])


def require_durable_revision(
    connection: sqlite3.Connection, expected_durable_revision: int
) -> None:
    current = read_durable_revision(connection)
    if current != expected_durable_revision:
        raise RuntimeSafetyStale(
            f"Durable state changed after runtime safety proof: expected revision {expected_durable_revision}, current {current}"
        )


def advance_durable_revision(connection: sqlite3.Connection) -> int:
    connection.execute(
        "UPDATE state_metadata SET durable_revision = durable_revision + 1 WHERE singleton = 1"
    )
    return read_durable_revision(connection)
