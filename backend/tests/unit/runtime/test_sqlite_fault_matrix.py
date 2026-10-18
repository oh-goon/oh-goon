from __future__ import annotations

from contextlib import closing
import multiprocessing
import os
import sqlite3
from pathlib import Path

import pytest

from oh_goon.runtime.database import DatabasePathIdentityLost, LocalDatabase


def _overwrite_in_place(path: Path, payload: bytes) -> None:
    identity = (path.stat().st_dev, path.stat().st_ino)
    with path.open("r+b") as handle:
        handle.seek(0)
        handle.write(payload)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    assert (path.stat().st_dev, path.stat().st_ino) == identity


def _donor_payload(path: Path, admitted_cookie: int) -> bytes:
    donor = path.with_name(path.name + ".donor")
    LocalDatabase(donor, journal_mode="DELETE")
    replacement_cookie = admitted_cookie + 1 if admitted_cookie < 0x7FFFFFFF else admitted_cookie - 1
    with closing(sqlite3.connect(donor, isolation_level=None)) as connection:
        connection.execute(f"PRAGMA application_id = {replacement_cookie}")
        assert int(connection.execute("PRAGMA application_id").fetchone()[0]) == replacement_cookie
    return donor.read_bytes()


def _mutate_fault(case: str, database: LocalDatabase, path: Path) -> None:
    if case == "header_notadb":
        payload = bytearray(path.read_bytes())
        payload[:16] = b"NOTADB-SAME-INOD"
        _overwrite_in_place(path, bytes(payload))
        return
    if case == "truncate":
        identity = (path.stat().st_dev, path.stat().st_ino)
        with path.open("r+b") as handle:
            handle.truncate(512)
            handle.flush()
            os.fsync(handle.fileno())
        assert (path.stat().st_dev, path.stat().st_ino) == identity
        return
    if case == "valid_donor_same_inode":
        assert database._database_identity_cookie is not None
        _overwrite_in_place(
            path,
            _donor_payload(path, database._database_identity_cookie),
        )
        return
    if case == "schema_cookie":
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE external_fault_matrix_table(value INTEGER)")
            connection.commit()
        return
    if case == "application_id":
        assert database._database_identity_cookie is not None
        replacement_cookie = (
            database._database_identity_cookie + 1
            if database._database_identity_cookie < 0x7FFFFFFF
            else database._database_identity_cookie - 1
        )
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            connection.execute(f"PRAGMA application_id = {replacement_cookie}")
        return
    if case == "journal_mode":
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            observed = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).upper()
            assert observed == "WAL"
        return
    if case == "revision_rollback":
        raise AssertionError("revision rollback requires its pre-write snapshot")
    if case == "inode_replacement":
        replacement = path.with_name(path.name + ".replacement")
        LocalDatabase(replacement, journal_mode="DELETE")
        replacement.replace(path)
        return
    raise AssertionError(f"unknown fault matrix case: {case}")


FAULT_CASES = (
    pytest.param("header_notadb", "readable admitted SQLite database", id="header-notadb"),
    pytest.param("truncate", "readable admitted SQLite database", id="same-inode-truncate"),
    pytest.param("valid_donor_same_inode", "database instance identity changed", id="valid-donor"),
    pytest.param("schema_cookie", "schema identity changed", id="schema-cookie"),
    pytest.param("application_id", "database instance identity changed", id="application-id"),
    pytest.param("journal_mode", "journal mode changed", id="journal-mode"),
    pytest.param("inode_replacement", "replaced after runtime admission", id="inode-replacement"),
)


@pytest.mark.parametrize(("case", "message"), FAULT_CASES)
def test_runtime_connection_fault_matrix_fails_closed(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    path = tmp_path / f"runtime-{case}.db"
    database = LocalDatabase(path, journal_mode="DELETE")

    _mutate_fault(case, database, path)

    with pytest.raises(DatabasePathIdentityLost, match=message):
        database.durable_revision()


@pytest.mark.parametrize(("case", "message"), FAULT_CASES)
def test_active_observer_fault_matrix_fails_closed_after_cache_release(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    path = tmp_path / f"observer-{case}.db"
    database = LocalDatabase(path, journal_mode="DELETE")

    with database.admitted_observer_connection() as observer:
        database.reprove_admitted_observer(observer)
        _mutate_fault(case, database, path)
        with pytest.raises(DatabasePathIdentityLost, match=message):
            database.reprove_admitted_observer(observer)


def test_runtime_connection_revision_rollback_fault_is_distinct(tmp_path: Path) -> None:
    path = tmp_path / "runtime-revision-rollback.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    genesis = path.read_bytes()
    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            ("/tmp/fault-matrix-runtime", 1, "2026-09-06T00:00:00Z"),
        )
    assert database._durable_revision_floor == 1
    _overwrite_in_place(path, genesis)

    with pytest.raises(DatabasePathIdentityLost, match="durable revision moved backwards"):
        database.durable_revision()


def test_active_observer_revision_rollback_fault_is_distinct(tmp_path: Path) -> None:
    path = tmp_path / "observer-revision-rollback.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    genesis = path.read_bytes()

    with database.admitted_observer_connection() as observer:
        with database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
                ("/tmp/fault-matrix-observer", 1, "2026-09-06T00:00:00Z"),
            )
        database.reprove_admitted_observer(observer)
        assert database._durable_revision_floor == 1
        _overwrite_in_place(path, genesis)
        with pytest.raises(DatabasePathIdentityLost, match="durable revision moved backwards"):
            database.reprove_admitted_observer(observer)


def test_observer_cache_release_detects_in_place_header_corruption_without_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "observer-cache-header.db"
    database = LocalDatabase(path, journal_mode="DELETE")

    with database.admitted_observer_connection() as observer:
        monkeypatch.setattr(
            database,
            "_open_admitted_observer_connection",
            lambda: (_ for _ in ()).throw(AssertionError("observer reproof must not reopen SQLite")),
        )
        payload = bytearray(path.read_bytes())
        payload[:16] = b"NOTADB-SAME-INOD"
        _overwrite_in_place(path, bytes(payload))
        with pytest.raises(DatabasePathIdentityLost, match="readable admitted SQLite database"):
            database.reprove_admitted_observer(observer)


def test_observer_cache_release_detects_valid_donor_overwrite_without_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "observer-cache-donor.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    assert database._database_identity_cookie is not None

    with database.admitted_observer_connection() as observer:
        monkeypatch.setattr(
            database,
            "_open_admitted_observer_connection",
            lambda: (_ for _ in ()).throw(AssertionError("observer reproof must not reopen SQLite")),
        )
        _overwrite_in_place(path, _donor_payload(path, database._database_identity_cookie))
        with pytest.raises(DatabasePathIdentityLost, match="database instance identity changed"):
            database.reprove_admitted_observer(observer)


def test_explicit_write_rollback_preserves_identity_and_revision(tmp_path: Path) -> None:
    path = tmp_path / "explicit-rollback.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    cookie = database._database_identity_cookie
    schema_cookie = database._schema_cookie

    with pytest.raises(RuntimeError, match="force rollback"):
        with database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
                ("/tmp/explicit-rollback", 1, "2026-09-06T00:00:00Z"),
            )
            raise RuntimeError("force rollback")

    assert database.durable_revision() == 0
    assert database._database_identity_cookie == cookie
    assert database._schema_cookie == schema_cookie
    with database.transaction() as connection:
        assert connection.execute(
            "SELECT 1 FROM locations WHERE path = ?", ("/tmp/explicit-rollback",)
        ).fetchone() is None
        assert str(connection.execute("PRAGMA integrity_check").fetchone()[0]).lower() == "ok"


def _committed_child_write(database_path: str) -> None:
    database = LocalDatabase(Path(database_path), journal_mode="DELETE")
    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            ("/tmp/process-commit", 1, "2026-09-06T00:00:00Z"),
        )


@pytest.mark.process
def test_committed_child_process_write_advances_parent_revision_without_identity_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "process-commit.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    cookie = database._database_identity_cookie
    assert database.durable_revision() == 0

    process = multiprocessing.get_context("spawn").Process(
        target=_committed_child_write,
        args=(str(path),),
    )
    process.start()
    process.join(timeout=15)
    assert process.exitcode == 0

    assert database.durable_revision() == 1
    assert database._database_identity_cookie == cookie
    with database.transaction() as connection:
        assert connection.execute(
            "SELECT 1 FROM locations WHERE path = ?", ("/tmp/process-commit",)
        ).fetchone() is not None


def test_complete_process_restart_has_no_external_revision_anchor_for_old_same_instance_snapshot(
    tmp_path: Path,
) -> None:
    """Document the bounded limit: rollback detection is admission-scoped, not external-ledger backed.

    A complete process restart necessarily establishes its revision floor from the SQLite
    instance it is given. Detecting a same-instance whole-file rollback *across* that restart
    would require a second trusted monotonic authority outside SQLite. Oh-Goon intentionally
    does not invent that dual authority here; within one admitted runtime the rollback floor is
    strict and tested above.
    """

    path = tmp_path / "restart-limit.db"
    first = LocalDatabase(path, journal_mode="DELETE")
    genesis = path.read_bytes()
    cookie = first._database_identity_cookie
    with first.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            ("/tmp/revision-before-restart", 1, "2026-09-06T00:00:00Z"),
        )
    assert first.durable_revision() == 1

    _overwrite_in_place(path, genesis)
    restarted = LocalDatabase(path, journal_mode="DELETE")
    assert restarted._database_identity_cookie == cookie
    assert restarted.durable_revision() == 0


def test_observer_transient_notadb_reopens_and_reproves_without_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "observer-transient-notadb.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    original_open = database._open_admitted_observer_connection
    opens = 0

    def flaky_open():
        nonlocal opens
        opens += 1
        if opens == 1:
            raise sqlite3.DatabaseError("file is not a database")
        return original_open()

    monkeypatch.setattr(database, "_open_admitted_observer_connection", flaky_open)
    with database.admitted_observer_connection() as observer:
        assert database.reprove_admitted_observer(observer) is None
    assert opens == 2
    assert database.durable_revision() >= 0


def test_observer_persistent_notadb_still_fails_closed_after_bounded_reproof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "observer-persistent-notadb.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    opens = 0

    def broken_open():
        nonlocal opens
        opens += 1
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(database, "_open_admitted_observer_connection", broken_open)
    with pytest.raises(DatabasePathIdentityLost, match="3 bounded observation-only re-proof attempts"):
        with database.admitted_observer_connection():
            pass
    assert opens == 3
