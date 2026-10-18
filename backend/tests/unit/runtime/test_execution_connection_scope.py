from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION, LocalDatabase


def test_execution_connection_scope_reuses_one_native_handle_for_many_transactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "scoped.db", journal_mode="DELETE")
    original_open = database._open_sqlite_connection
    admitted_opens = 0

    def counted_open(*, admitted: bool):
        nonlocal admitted_opens
        if admitted:
            admitted_opens += 1
        return original_open(admitted=admitted)

    monkeypatch.setattr(database, "_open_sqlite_connection", counted_open)

    with database.execution_connection_scope():
        for _ in range(200):
            with database.transaction() as connection:
                assert (
                    int(connection.execute("PRAGMA user_version").fetchone()[0])
                    == DATABASE_SCHEMA_VERSION
                )

    assert admitted_opens == 1
    with database.transaction() as connection:
        assert (
            int(connection.execute("PRAGMA user_version").fetchone()[0]) == DATABASE_SCHEMA_VERSION
        )
    assert admitted_opens == 2


def test_execution_connection_scope_is_thread_local_not_cross_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "parallel-scoped.db", journal_mode="DELETE")
    original_open = database._open_sqlite_connection
    opens_by_thread: dict[str, int] = {}
    lock = threading.Lock()

    def counted_open(*, admitted: bool):
        if admitted:
            with lock:
                name = threading.current_thread().name
                opens_by_thread[name] = opens_by_thread.get(name, 0) + 1
        return original_open(admitted=admitted)

    monkeypatch.setattr(database, "_open_sqlite_connection", counted_open)
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with database.execution_connection_scope():
                barrier.wait(timeout=2)
                for _ in range(50):
                    with database.transaction() as connection:
                        connection.execute("SELECT 1").fetchone()
                barrier.wait(timeout=2)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, name=f"target-{index}") for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert opens_by_thread == {"target-0": 1, "target-1": 1}


def test_execution_scope_removes_fresh_reconnect_surface_after_first_admitted_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the native 188 failure shape without weakening SQLite identity proof.

    The first admitted open succeeds. Any second admitted native open raises SQLITE_NOTADB,
    matching the host/storage reconnect symptom seen during certification. A complete Fleet
    execution scope must therefore keep hundreds of durable cuts on the first native handle
    while each transaction still re-proves admitted SQLite identity.
    """

    database = LocalDatabase(tmp_path / "native-reconnect-regression.db", journal_mode="DELETE")
    original_open = database._open_sqlite_connection
    admitted_opens = 0

    def fail_second_admitted_open(*, admitted: bool):
        nonlocal admitted_opens
        if admitted:
            admitted_opens += 1
            if admitted_opens > 1:
                error = sqlite3.DatabaseError("file is not a database")
                error.sqlite_errorcode = sqlite3.SQLITE_NOTADB
                error.sqlite_errorname = "SQLITE_NOTADB"
                raise error
        return original_open(admitted=admitted)

    monkeypatch.setattr(database, "_open_sqlite_connection", fail_second_admitted_open)

    with database.execution_connection_scope():
        for _ in range(500):
            with database.transaction() as connection:
                assert (
                    int(connection.execute("PRAGMA user_version").fetchone()[0])
                    == DATABASE_SCHEMA_VERSION
                )

    assert admitted_opens == 1
