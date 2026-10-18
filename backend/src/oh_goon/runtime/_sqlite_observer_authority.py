from __future__ import annotations

import sqlite3

_DENIED_SQLITE_ACTIONS = frozenset(
    action
    for name in (
        "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE",
        "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW", "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW", "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE",
        "SQLITE_ATTACH", "SQLITE_DETACH",
    )
    if (action := getattr(sqlite3, name, None)) is not None
)


def admitted_observer_authorizer(
    action: int,
    _arg1: str | None,
    _arg2: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    """Deny SQL mutation on a rollback-capable admitted observer handle.

    The observer intentionally owns a non-creating native rw handle so SQLite can
    perform rollback-journal recovery when necessary. Mutation authority is nevertheless
    closed here: DML/DDL is denied and PRAGMA assignments are denied. Read-only PRAGMAs
    used by the identity proof remain allowed.
    """

    if action in _DENIED_SQLITE_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == getattr(sqlite3, "SQLITE_PRAGMA", -1) and _arg2 is not None:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK
