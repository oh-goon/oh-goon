from __future__ import annotations

import sqlite3
from pathlib import Path

from oh_goon.runtime._sqlite_authority_gate import sqlite_authority_gate


def configure_common_connection(path: Path, connection: sqlite3.Connection) -> None:
    """Apply connection-local SQLite policy under canonical read authority."""

    with sqlite_authority_gate(path, write=False):
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA cell_size_check = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.enable_load_extension(False)
        for name, enabled in (
            ("SQLITE_DBCONFIG_DEFENSIVE", True),
            ("SQLITE_DBCONFIG_TRUSTED_SCHEMA", False),
            ("SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION", False),
            ("SQLITE_DBCONFIG_DQS_DDL", False),
            ("SQLITE_DBCONFIG_DQS_DML", False),
        ):
            option = getattr(sqlite3, name, None)
            if option is not None and hasattr(connection, "setconfig"):
                connection.setconfig(option, enabled)
