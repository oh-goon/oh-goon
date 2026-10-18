from __future__ import annotations

import sqlite3


def sqlite_identity_error(exc: sqlite3.DatabaseError) -> bool:
    """Return whether SQLite reports loss of the admitted database format identity."""

    code = getattr(exc, "sqlite_errorcode", None)
    identity_codes = {
        value
        for value in (
            getattr(sqlite3, "SQLITE_NOTADB", None),
            getattr(sqlite3, "SQLITE_CORRUPT", None),
        )
        if value is not None
    }
    if code in identity_codes:
        return True
    text = str(exc).lower()
    return "file is not a database" in text or "database disk image is malformed" in text
