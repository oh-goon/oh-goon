from __future__ import annotations

import multiprocessing
import os
import signal
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Event, Thread

import pytest

from oh_goon.core.errors import DatabaseSchemaMismatch
from oh_goon.runtime.database import DatabasePathIdentityLost, LocalDatabase
from oh_goon.runtime.durable_revision import read_durable_revision


def _overwrite_in_place(path: Path, payload: bytes) -> None:
    inode = path.stat().st_ino
    with path.open("r+b") as handle:
        handle.seek(0)
        handle.write(payload)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    assert path.stat().st_ino == inode


def _sigkill_during_delete_journal_write(database_path: str) -> None:
    connection = sqlite3.connect(database_path, timeout=30.0, isolation_level=None)
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("UPDATE state_metadata SET durable_revision = durable_revision + 100")
    connection.execute(
        "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
        ("/tmp/crash-uncommitted", 99, "2026-09-06T00:00:00Z"),
    )
    os.kill(os.getpid(), signal.SIGKILL)


def test_valid_oh_goon_database_substitution_same_inode_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = iter((111_111, 222_222))
    monkeypatch.setattr(
        "oh_goon.runtime.database._new_database_identity_cookie",
        lambda: next(cookies),
    )
    admitted_path = tmp_path / "admitted.db"
    donor_path = tmp_path / "donor.db"
    admitted = LocalDatabase(admitted_path, journal_mode="DELETE")
    donor = LocalDatabase(donor_path, journal_mode="DELETE")

    assert admitted._schema_cookie == donor._schema_cookie
    assert admitted._database_identity_cookie != donor._database_identity_cookie
    _overwrite_in_place(admitted_path, donor_path.read_bytes())

    with pytest.raises(DatabasePathIdentityLost, match="database instance identity changed"):
        admitted.durable_revision()


def test_durable_revision_proof_cannot_compare_against_future_published_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "revision-authority-cut.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    entered_read = Event()
    release_read = Event()
    publish_finished = Event()
    proof_errors: list[BaseException] = []

    def paused_read(_connection: sqlite3.Connection) -> int:
        entered_read.set()
        assert release_read.wait(timeout=2)
        return 0

    monkeypatch.setattr("oh_goon.runtime.database.read_durable_revision", paused_read)

    with closing(
        sqlite3.connect(path, timeout=30.0, isolation_level=None, check_same_thread=False)
    ) as connection:
        database._configure_common_connection(connection)

        def prove() -> None:
            try:
                database._prove_admitted_sqlite_identity(connection, context="deterministic race")
            except BaseException as exc:  # pragma: no cover - asserted below
                proof_errors.append(exc)

        proof_thread = Thread(target=prove)
        proof_thread.start()
        assert entered_read.wait(timeout=2)

        def publish() -> None:
            database._publish_durable_revision(1)
            publish_finished.set()

        publish_thread = Thread(target=publish)
        publish_thread.start()
        assert not publish_finished.wait(timeout=0.05)
        release_read.set()
        proof_thread.join(timeout=2)
        publish_thread.join(timeout=2)

    assert not proof_errors
    assert publish_finished.is_set()
    assert database._durable_revision_floor == 1


def test_same_database_snapshot_rollback_is_rejected_by_revision_floor(tmp_path: Path) -> None:
    path = tmp_path / "rollback.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    genesis = path.read_bytes()

    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            ("/tmp/revision-one", 1, "2026-09-06T00:00:00Z"),
        )
    assert database._durable_revision_floor == 1

    _overwrite_in_place(path, genesis)
    with pytest.raises(DatabasePathIdentityLost, match="durable revision moved backwards"):
        database.durable_revision()


def test_active_observer_rejects_schema_cookie_drift(tmp_path: Path) -> None:
    path = tmp_path / "observer-schema.db"
    database = LocalDatabase(path, journal_mode="DELETE")

    with database.admitted_observer_connection() as observer:
        database.reprove_admitted_observer(observer)
        with closing(sqlite3.connect(path)) as external:
            external.execute("CREATE TABLE external_schema_change(value INTEGER)")
            external.commit()
        with pytest.raises(DatabasePathIdentityLost, match="schema identity changed"):
            database.reprove_admitted_observer(observer)


def test_active_observer_rejects_database_identity_cookie_drift(tmp_path: Path) -> None:
    path = tmp_path / "observer-identity.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    replacement_cookie = (database._database_identity_cookie or 1) + 1

    with database.admitted_observer_connection() as observer:
        with closing(sqlite3.connect(path, isolation_level=None)) as external:
            external.execute(f"PRAGMA application_id = {replacement_cookie}")
        with pytest.raises(DatabasePathIdentityLost, match="database instance identity changed"):
            database.reprove_admitted_observer(observer)


def test_active_observer_rejects_journal_mode_drift(tmp_path: Path) -> None:
    path = tmp_path / "observer-journal.db"
    database = LocalDatabase(path, journal_mode="DELETE")

    with database.admitted_observer_connection() as observer:
        with closing(sqlite3.connect(path, isolation_level=None)) as external:
            observed = str(external.execute("PRAGMA journal_mode = WAL").fetchone()[0]).upper()
            assert observed == "WAL"
        with pytest.raises(DatabasePathIdentityLost, match="journal mode changed"):
            database.reprove_admitted_observer(observer)


def test_active_observer_rejects_path_replacement_while_connection_remains_open(
    tmp_path: Path,
) -> None:
    path = tmp_path / "observer-path.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    replacement = tmp_path / "replacement.db"
    LocalDatabase(replacement, journal_mode="DELETE")

    with database.admitted_observer_connection() as observer:
        replacement.replace(path)
        with pytest.raises(DatabasePathIdentityLost, match="replaced after runtime admission"):
            database.reprove_admitted_observer(observer)


def test_active_observer_accepts_legitimate_writer_and_sees_revision_advance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "observer-legitimate-writer.db"
    database = LocalDatabase(path, journal_mode="DELETE")

    with database.admitted_observer_connection() as observer:
        database.reprove_admitted_observer(observer)
        with database.transaction(write=True) as writer:
            writer.execute(
                "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
                ("/tmp/legitimate", 1, "2026-09-06T00:00:00Z"),
            )
        assert database._durable_revision_floor == 1
        database.reprove_admitted_observer(observer)
        assert database._durable_revision_floor == 1


def test_observer_can_read_while_delete_journal_writer_holds_reserved_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "observer-reserved-lock.db"
    database = LocalDatabase(path, journal_mode="DELETE")

    with database.admitted_observer_connection() as observer:
        with database.transaction(write=True, track_revision=False) as writer:
            writer.execute("UPDATE state_metadata SET durable_revision = durable_revision")
            database.reprove_admitted_observer(observer)
            assert (
                observer.execute(
                    "SELECT durable_revision FROM state_metadata WHERE singleton = 1"
                ).fetchone()[0]
                == 0
            )


def test_runtime_connections_enable_sqlite_defensive_integrity_controls(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "defensive.db", journal_mode="DELETE")

    with database.transaction() as connection:
        assert int(connection.execute("PRAGMA cell_size_check").fetchone()[0]) == 1
        assert int(connection.execute("PRAGMA trusted_schema").fetchone()[0]) == 0
        if hasattr(connection, "getconfig"):
            assert connection.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE) is True
            assert connection.getconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA) is False
            assert connection.getconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION) is False
            assert connection.getconfig(sqlite3.SQLITE_DBCONFIG_DQS_DDL) is False
            assert connection.getconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML) is False

    with database.admitted_observer_connection() as observer:
        assert int(observer.execute("PRAGMA query_only").fetchone()[0]) == 1
        assert int(observer.execute("PRAGMA cell_size_check").fetchone()[0]) == 1
        assert int(observer.execute("PRAGMA trusted_schema").fetchone()[0]) == 0


def test_database_identity_cookie_persists_across_normal_reopen(tmp_path: Path) -> None:
    path = tmp_path / "identity-reopen.db"
    first = LocalDatabase(path, journal_mode="DELETE")
    cookie = first._database_identity_cookie
    assert cookie is not None and cookie > 0

    second = LocalDatabase(path, journal_mode="DELETE")
    assert second._database_identity_cookie == cookie
    assert second.durable_revision() == first.durable_revision()


def test_active_observer_rejects_same_database_snapshot_rollback(tmp_path: Path) -> None:
    path = tmp_path / "observer-rollback.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    genesis = path.read_bytes()

    with database.admitted_observer_connection() as observer:
        with database.transaction(write=True) as writer:
            writer.execute(
                "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
                ("/tmp/observer-revision-one", 1, "2026-09-06T00:00:00Z"),
            )
        database.reprove_admitted_observer(observer)
        assert database._durable_revision_floor == 1
        _overwrite_in_place(path, genesis)
        with pytest.raises(DatabasePathIdentityLost, match="durable revision moved backwards"):
            database.reprove_admitted_observer(observer)


@pytest.mark.process
def test_delete_journal_sigkill_recovery_preserves_admitted_identity(tmp_path: Path) -> None:
    path = tmp_path / "sigkill-recovery.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    cookie = database._database_identity_cookie
    schema_cookie = database._schema_cookie
    assert database.durable_revision() == 0

    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_sigkill_during_delete_journal_write,
        args=(str(path),),
    )
    process.start()
    process.join(timeout=15)
    assert process.exitcode == -signal.SIGKILL

    # SQLite hot-journal recovery is native SQLite authority. Oh-Goon must accept the
    # recovered original instance, not misclassify a legitimate rollback as replacement.
    assert database.durable_revision() == 0
    assert database._database_identity_cookie == cookie
    assert database._schema_cookie == schema_cookie
    with database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM locations WHERE path = ?", ("/tmp/crash-uncommitted",)
            ).fetchone()
            is None
        )
        assert str(connection.execute("PRAGMA integrity_check").fetchone()[0]).lower() == "ok"


def test_independent_local_database_instances_share_identity_and_revision_monotonicity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "multi-owner.db"
    first = LocalDatabase(path, journal_mode="DELETE")
    second = LocalDatabase(path, journal_mode="DELETE")
    assert first._database_identity_cookie == second._database_identity_cookie

    with first.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            ("/tmp/first-owner", 1, "2026-09-06T00:00:00Z"),
        )
    assert second.durable_revision() == 1
    assert second._durable_revision_floor == 1

    with second.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            ("/tmp/second-owner", 2, "2026-09-06T00:00:01Z"),
        )
    assert first.durable_revision() == 2
    assert first._durable_revision_floor == 2


def test_current_database_without_identity_cookie_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-cookie.db"
    first = LocalDatabase(path, journal_mode="DELETE")
    with first.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            ("/tmp/preserved-before-identity-loss", 1, "2026-09-06T00:00:00Z"),
        )
    with closing(sqlite3.connect(path, isolation_level=None)) as external:
        external.execute("PRAGMA application_id = 0")
        assert int(external.execute("PRAGMA application_id").fetchone()[0]) == 0

    before = path.read_bytes()
    with pytest.raises(
        DatabaseSchemaMismatch, match="missing its required application_id identity"
    ):
        LocalDatabase(path, journal_mode="WAL")

    assert path.read_bytes() == before
    with closing(sqlite3.connect(path, isolation_level=None)) as external:
        assert int(external.execute("PRAGMA application_id").fetchone()[0]) == 0
        assert (
            external.execute(
                "SELECT 1 FROM locations WHERE path = ?",
                ("/tmp/preserved-before-identity-loss",),
            ).fetchone()
            is not None
        )


def test_defensive_mode_refuses_writable_schema_enablement(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "defensive-writable-schema.db", journal_mode="DELETE")
    with database.transaction() as connection:
        assert int(connection.execute("PRAGMA writable_schema").fetchone()[0]) == 0
        connection.execute("PRAGMA writable_schema = ON")
        assert int(connection.execute("PRAGMA writable_schema").fetchone()[0]) == 0


def test_observer_snapshot_cut_precedes_concurrent_revision_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coherent older observer snapshot must not be compared with a future floor."""

    path = tmp_path / "observer-snapshot-publication-race.db"
    # This host may intentionally reject WAL for product use; the regression needs
    # WAL snapshot mechanics to deterministically reproduce the native admitted-WAL race.
    monkeypatch.setattr(
        "oh_goon.runtime.database.sqlite_journal_mode_for_path",
        lambda *_args, **_kwargs: "WAL",
    )
    database = LocalDatabase(path, journal_mode="WAL")
    original_prove = database._prove_admitted_sqlite_identity
    original_publish = database._publish_durable_revision
    snapshot_established = Event()
    writer_committed = Event()
    writer_finished = Event()

    def prove_after_snapshot(connection: sqlite3.Connection, *, context: str) -> None:
        assert read_durable_revision(connection) == 0
        snapshot_established.set()
        assert writer_committed.wait(timeout=2)
        original_prove(connection, context=context)

    def publish_after_commit(committed_revision: int) -> None:
        writer_committed.set()
        original_publish(committed_revision)

    def write() -> None:
        assert snapshot_established.wait(timeout=2)
        with closing(sqlite3.connect(path, timeout=30.0, isolation_level=None)) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE state_metadata SET durable_revision = durable_revision + 1")
            connection.execute(
                "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
                ("/tmp/concurrent-revision", 1, "2026-09-10T00:00:00Z"),
            )
            connection.commit()
        publish_after_commit(1)
        writer_finished.set()

    with database.admitted_observer_connection() as observer:
        monkeypatch.setattr(database, "_prove_admitted_sqlite_identity", prove_after_snapshot)
        monkeypatch.setattr(database, "_publish_durable_revision", publish_after_commit)
        writer_thread = Thread(target=write)
        writer_thread.start()
        database.reprove_admitted_observer(observer)
        assert writer_committed.is_set()
        writer_thread.join(timeout=2)

    assert writer_finished.is_set()
    assert database._durable_revision_floor == 1
