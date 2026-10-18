"""DB2: native SQLite FULL/identity/cut rules, without another database owner.

The SQLITE_FULL test uses SQLite's own max_page_count to force an actual pager
failure and rollback. It is NOT an OS ENOSPC/fsync or native WSL fault receipt.
"""
from __future__ import annotations

import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from oh_goon.runtime.database import DatabasePathIdentityLost, LocalDatabase


def _write_contended(path: str, worker: int, count: int) -> None:
    """Separate process and connection; SQLite + existing gate own concurrency."""
    database = LocalDatabase(Path(path), journal_mode="DELETE")
    for index in range(count):
        with database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
                (f"/tmp/db2-{worker}-{index}", worker * count + index, "2026-10-14T00:00:00Z"),
            )


def test_reused_operational_handle_refuses_downgraded_synchronous_before_work(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "durability.db", journal_mode="DELETE")
    with database.execution_connection_scope():
        scoped = database._connection_scope.connection
        scoped.execute("PRAGMA synchronous = OFF")
        entered = False
        with pytest.raises(DatabasePathIdentityLost, match="synchronous durability changed"):
            with database.transaction(write=True) as connection:
                entered = True
                connection.execute("DELETE FROM locations")
        assert entered is False
        assert not scoped.in_transaction
        scoped.execute("PRAGMA synchronous = FULL")
        with database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO locations(path, position, added_at) VALUES ('/tmp/db2-safe', 1, 'now')"
            )
    assert LocalDatabase(database.path, journal_mode="DELETE").durable_revision() == 1


@pytest.mark.parametrize("misconfiguration", ["PRAGMA foreign_keys = OFF", "PRAGMA query_only = ON"])
def test_reused_handle_rejects_connection_policy_drift_before_work(
    tmp_path: Path, misconfiguration: str
) -> None:
    database = LocalDatabase(tmp_path / "connection-policy.db", journal_mode="DELETE")
    with database.execution_connection_scope():
        scoped = database._connection_scope.connection
        scoped.execute(misconfiguration)
        entered = False
        with pytest.raises(DatabasePathIdentityLost, match="connection .* changed|query-only"):
            with database.transaction(write=True):
                entered = True
        assert entered is False
        assert not scoped.in_transaction
    assert database.durable_revision() == 0


def test_filesystem_mount_classification_drift_rejects_admitted_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "mount.db", journal_mode="DELETE")
    import oh_goon.runtime.database as original_owner

    admitted = database._path_identity
    assert admitted is not None
    unexpected = "drifted-9p" if admitted.filesystem != "drifted-9p" else "drifted-nfs"
    monkeypatch.setattr(original_owner, "database_filesystem_type", lambda _path: unexpected)
    entered = False
    with pytest.raises(DatabasePathIdentityLost, match="filesystem admission changed"):
        with database.transaction(write=True):
            entered = True
    assert entered is False


def test_real_sqlite_full_rolls_back_entire_original_owner_cut(tmp_path: Path) -> None:
    path = tmp_path / "sqlite-full.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    with database.execution_connection_scope():
        connection = database._connection_scope.connection
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        maximum = int(connection.execute(f"PRAGMA max_page_count = {page_count}").fetchone()[0])
        assert maximum == page_count
        with pytest.raises(sqlite3.DatabaseError) as error:
            with database.transaction(write=True) as writer:
                writer.execute(
                    "INSERT INTO locations(path, position, added_at) VALUES ('/tmp/staged', 1, 'now')"
                )
                writer.execute(
                    "INSERT INTO locations(path, position, added_at) VALUES (?, 2, 'now')",
                    ("X" * (2 * 1024 * 1024),),
                )
        assert getattr(error.value, "sqlite_errorname", "") == "SQLITE_FULL"
    # Reopen with SQLite, not a synthetic counter or mock, to prove zero effects.
    reopened = LocalDatabase(path, journal_mode="DELETE")
    with reopened.transaction() as reader:
        assert reader.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0
        assert reader.execute(
            "SELECT durable_revision FROM state_metadata WHERE singleton=1"
        ).fetchone()[0] == 0


def test_concurrent_independent_processes_preserve_single_serial_commit_history(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.db"
    LocalDatabase(path, journal_mode="DELETE")
    ctx = multiprocessing.get_context("spawn")
    worker_count = 3
    each = 9
    processes = [ctx.Process(target=_write_contended, args=(str(path), w, each))
                 for w in range(worker_count)]
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join(15)
        assert all(process.exitcode == 0 for process in processes)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(2)
    after = LocalDatabase(path, journal_mode="DELETE")
    assert after.durable_revision() == worker_count * each
    with after.transaction() as reader:
        assert reader.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == worker_count * each
        assert reader.execute("SELECT COUNT(DISTINCT path) FROM locations").fetchone()[0] == worker_count * each
