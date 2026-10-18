from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from oh_goon.core import engine_policy_defaults as policy_defaults
from oh_goon.core.path_authority import navigation_path


def ensure_exclusion_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS exclusions (
            path TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            excluded_at TEXT NOT NULL
        );
        """
    )


def read_exclusions(connection: sqlite3.Connection) -> list[dict[str, str]]:
    rows = connection.execute(
        "SELECT path, reason, excluded_at FROM exclusions ORDER BY path COLLATE NOCASE"
    ).fetchall()
    return [
        {"path": str(row[0]), "reason": str(row[1]), "excluded_at": str(row[2])} for row in rows
    ]


def read_exclusion(connection: sqlite3.Connection, repository: Path | str) -> dict[str, str] | None:
    row = connection.execute(
        "SELECT path, reason, excluded_at FROM exclusions WHERE path = ?",
        (str(navigation_path(repository)),),
    ).fetchone()
    if row is None:
        return None
    return {"path": str(row[0]), "reason": str(row[1]), "excluded_at": str(row[2])}


def write_exclusion(
    connection: sqlite3.Connection, repository: Path | str, reason: str | None
) -> dict[str, str]:
    key = str(navigation_path(repository))
    record = {
        "reason": (
            reason.strip()
            if reason and reason.strip()
            else policy_defaults.default_exclusion_reason()
        ),
        "excluded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    connection.execute(
        """
        INSERT INTO exclusions(path, reason, excluded_at)
        VALUES (?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            reason = excluded.reason,
            excluded_at = excluded.excluded_at
        """,
        (key, record["reason"], record["excluded_at"]),
    )
    return {"path": key, **record}


def delete_exclusion(connection: sqlite3.Connection, repository: Path | str) -> bool:
    cursor = connection.execute(
        "DELETE FROM exclusions WHERE path = ?",
        (str(navigation_path(repository)),),
    )
    return cursor.rowcount > 0
