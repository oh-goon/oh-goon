from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.runtime.database import (
    DATABASE_SCHEMA_VERSION,
    SQLITE_JOURNAL_DELETE,
    SQLITE_JOURNAL_WAL,
    DatabasePathIdentityLost,
    LocalDatabase,
    default_database_path,
    sqlite_journal_mode_for_path,
)


def test_default_database_path_prefers_explicit_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    override = tmp_path / "state" / "custom.db"
    monkeypatch.setenv("OH_GOON_DB", str(override))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "ignored"))

    assert default_database_path() == override.resolve()


def test_default_database_path_uses_xdg_data_home_on_linux(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OH_GOON_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert default_database_path() == (tmp_path / "data" / "oh-goon" / "oh-goon.db").resolve()


def test_local_database_rejects_path_replacement_after_initialization(tmp_path: Path) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    database = LocalDatabase(path)
    replacement = tmp_path / "replacement.db"
    LocalDatabase(replacement)
    replacement.replace(path)

    with pytest.raises(DatabasePathIdentityLost, match="replaced after runtime admission"):
        database.durable_revision()


def test_local_database_rejects_in_place_non_sqlite_bytes(tmp_path: Path) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    database = LocalDatabase(path)
    with path.open("r+b") as handle:
        handle.seek(0)
        handle.write(b"NOT SQLITE HEADER")
        handle.flush()

    with pytest.raises(
        DatabasePathIdentityLost,
        match="no longer contains a readable admitted SQLite database",
    ):
        database.durable_revision()


def test_local_database_rejects_same_inode_truncation_as_schema_identity_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    database = LocalDatabase(path)
    admitted_inode = path.stat().st_ino
    path.write_bytes(b"")
    assert path.stat().st_ino == admitted_inode

    with pytest.raises(
        DatabasePathIdentityLost,
        match="no longer contains the admitted SQLite schema",
    ):
        database.durable_revision()


def test_local_database_reproves_unchanged_inode_after_one_transient_notadb(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    database = LocalDatabase(path)
    original = database._open_sqlite_connection
    calls = 0

    def flaky_open(*, admitted: bool):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.DatabaseError("file is not a database")
        return original(admitted=admitted)

    monkeypatch.setattr(database, "_open_sqlite_connection", flaky_open)

    events = []
    database.set_recovery_observer(events.append)
    assert database.durable_revision() == 0
    assert calls == 2
    assert path.is_file()
    assert [event.outcome for event in events] == ["requested", "succeeded"]
    assert all(event.kind == "sqlite_reproof" for event in events)


def test_local_database_reproof_window_survives_two_transient_notadb_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state" / "delayed-transient.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    original = database._open_sqlite_connection
    calls = 0
    delays: list[float] = []

    def flaky_open(*, admitted: bool):
        nonlocal calls
        calls += 1
        if calls <= 2:
            error = sqlite3.DatabaseError("file is not a database")
            error.sqlite_errorcode = getattr(sqlite3, "SQLITE_NOTADB", 26)
            raise error
        return original(admitted=admitted)

    monkeypatch.setattr(database, "_open_sqlite_connection", flaky_open)
    monkeypatch.setattr("oh_goon.runtime.database.time.sleep", delays.append)

    events = []
    database.set_recovery_observer(events.append)
    assert database.durable_revision() == 0
    assert calls == 3
    assert delays == [0.25, 1.0]
    assert [event.outcome for event in events] == ["requested", "requested", "succeeded"]


def test_local_database_confirms_persistent_notadb_before_identity_loss(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    database = LocalDatabase(path)
    calls = 0

    def broken_open(*, admitted: bool):
        nonlocal calls
        calls += 1
        assert admitted is True
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(database, "_open_sqlite_connection", broken_open)
    events = []
    database.set_recovery_observer(events.append)

    with pytest.raises(
        DatabasePathIdentityLost,
        match="after 3 bounded observation-only re-proof attempts",
    ):
        database.durable_revision()
    assert calls == 3
    assert path.is_file()
    assert [event.outcome for event in events] == ["requested", "requested", "failed"]


def test_sqlite_journal_auto_refuses_wal_before_fixed_sqlite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "oh_goon.runtime._database_policy._filesystem_type_for_path", lambda _path: "ext4"
    )

    assert (
        sqlite_journal_mode_for_path(tmp_path / "state.db", sqlite_version=(3, 51, 2))
        == SQLITE_JOURNAL_DELETE
    )


def test_sqlite_journal_auto_refuses_wal_on_unsafe_filesystem(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "oh_goon.runtime._database_policy._filesystem_type_for_path", lambda _path: "9p"
    )

    assert (
        sqlite_journal_mode_for_path(tmp_path / "state.db", sqlite_version=(3, 51, 3))
        == SQLITE_JOURNAL_DELETE
    )


def test_sqlite_journal_auto_allows_wal_only_on_proven_safe_runtime_and_filesystem(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "oh_goon.runtime._database_policy._filesystem_type_for_path", lambda _path: "ext4"
    )

    assert (
        sqlite_journal_mode_for_path(tmp_path / "state.db", sqlite_version=(3, 51, 3))
        == SQLITE_JOURNAL_WAL
    )


def test_explicit_wal_fails_closed_when_storage_is_not_proven_safe(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "oh_goon.runtime._database_policy._filesystem_type_for_path", lambda _path: "drvfs"
    )

    with pytest.raises(RuntimeError, match="WAL mode was explicitly requested"):
        sqlite_journal_mode_for_path(
            tmp_path / "state.db",
            requested="WAL",
            sqlite_version=(3, 51, 3),
        )


def test_local_database_applies_resolved_rollback_journal_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OH_GOON_SQLITE_JOURNAL_MODE", "DELETE")
    path = tmp_path / "state" / "oh-goon.db"

    database = LocalDatabase(path)

    assert database.journal_mode == SQLITE_JOURNAL_DELETE
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].upper() == "DELETE"


def test_admitted_observer_has_query_only_and_authorizer_mutation_fences(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "observer-read-only.db"
    database = LocalDatabase(path, journal_mode="DELETE")

    with database.admitted_observer_connection() as connection:
        assert int(connection.execute("PRAGMA query_only").fetchone()[0]) == 1
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("PRAGMA query_only = OFF")
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("CREATE TABLE should_never_exist(value INTEGER)")

    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_never_exist'"
            ).fetchone()
            is None
        )


def test_write_capable_runtime_connection_reasserts_synchronous_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state" / "synchronous-full.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    original_open = database._open_sqlite_connection

    def open_with_bad_synchronous(*, admitted: bool):
        connection = original_open(admitted=admitted)
        connection.execute("PRAGMA synchronous = OFF")
        assert int(connection.execute("PRAGMA synchronous").fetchone()[0]) == 0
        return connection

    monkeypatch.setattr(database, "_open_sqlite_connection", open_with_bad_synchronous)
    with database.transaction() as connection:
        assert int(connection.execute("PRAGMA synchronous").fetchone()[0]) == 2


def test_local_database_rejects_same_inode_schema_cookie_drift_with_same_user_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "schema-cookie.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    admitted_inode = path.stat().st_ino

    with closing(sqlite3.connect(path)) as connection:
        assert (
            int(connection.execute("PRAGMA user_version").fetchone()[0]) == DATABASE_SCHEMA_VERSION
        )
        connection.execute("CREATE TABLE external_schema_drift(value INTEGER)")
        connection.commit()
        assert (
            int(connection.execute("PRAGMA user_version").fetchone()[0]) == DATABASE_SCHEMA_VERSION
        )
    assert path.stat().st_ino == admitted_inode

    with pytest.raises(DatabasePathIdentityLost, match="schema identity changed"):
        database.durable_revision()


def test_local_database_rejects_external_journal_mode_drift_after_admission(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "journal-drift.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    assert database.journal_mode == SQLITE_JOURNAL_DELETE

    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        observed = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).upper()
        assert observed == "WAL"

    with pytest.raises(DatabasePathIdentityLost, match="journal mode changed"):
        database.durable_revision()


def test_admitted_read_reopens_after_transient_notadb_during_observation_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state" / "observer-cut-transient.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    original_prove = database._prove_admitted_sqlite_identity
    proofs = 0
    opens = 0
    original_open = database._open_admitted_observer_connection

    def counted_open():
        nonlocal opens
        opens += 1
        return original_open()

    def flaky_prove(connection, *, context: str):
        nonlocal proofs
        proofs += 1
        if proofs == 2:
            raise sqlite3.DatabaseError("file is not a database")
        return original_prove(connection, context=context)

    events = []
    database.set_recovery_observer(events.append)
    monkeypatch.setattr(database, "_open_admitted_observer_connection", counted_open)
    monkeypatch.setattr(database, "_prove_admitted_sqlite_identity", flaky_prove)

    observed = database.observe_admitted_read(
        lambda connection: int(connection.execute("SELECT 41 + 1").fetchone()[0])
    )

    assert observed == 42
    assert opens == 2
    assert [event.outcome for event in events] == ["requested", "succeeded"]
    assert all(event.kind == "sqlite_reproof" for event in events)


def test_admitted_read_confirms_persistent_notadb_during_observation_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state" / "observer-cut-persistent.db"
    database = LocalDatabase(path, journal_mode="DELETE")
    original_prove = database._prove_admitted_sqlite_identity
    proofs = 0
    opens = 0
    original_open = database._open_admitted_observer_connection

    def counted_open():
        nonlocal opens
        opens += 1
        return original_open()

    def broken_observation_prove(connection, *, context: str):
        nonlocal proofs
        proofs += 1
        # Every fresh handle passes its opening proof, then the actual authority
        # observation reports NOTADB. That is the runtime shape that previously
        # escalated after a single observation instead of fresh-handle re-proof.
        if proofs % 2 == 0:
            raise sqlite3.DatabaseError("file is not a database")
        return original_prove(connection, context=context)

    events = []
    database.set_recovery_observer(events.append)
    monkeypatch.setattr(database, "_open_admitted_observer_connection", counted_open)
    monkeypatch.setattr(database, "_prove_admitted_sqlite_identity", broken_observation_prove)

    with pytest.raises(
        DatabasePathIdentityLost,
        match="after 3 bounded observation-only re-proof attempts during preparation observation",
    ):
        database.observe_admitted_read(lambda connection: connection.execute("SELECT 1").fetchone())

    assert opens == 3
    assert [event.outcome for event in events] == ["requested", "requested", "failed"]


def test_sqlite_access_diagnostics_are_opt_in_and_grouped_by_logical_operation(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state" / "diagnostics.db", journal_mode="DELETE")

    assert database.durable_revision() == 0
    assert database.sqlite_diagnostics.snapshot() == ()

    database.sqlite_diagnostics.set_enabled(True)
    assert database.durable_revision() == 0

    diagnostics = {item.logical_operation: item for item in database.sqlite_diagnostics.snapshot()}
    runtime = diagnostics["runtime transaction"]
    assert runtime.native_opens == 1
    assert runtime.transactions == 1
    assert runtime.observer_opens == 0
    assert runtime.reproof_requested == 0
    assert runtime.reproof_succeeded == 0
    assert runtime.reproof_failed == 0


def test_sqlite_access_diagnostics_count_reproof_without_changing_recovery_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = LocalDatabase(tmp_path / "state" / "diagnostic-reproof.db", journal_mode="DELETE")
    original_prove = database._prove_admitted_sqlite_identity
    calls = 0

    def transient_prove(connection: sqlite3.Connection, *, context: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            error = sqlite3.DatabaseError("file is not a database")
            error.sqlite_errorcode = getattr(sqlite3, "SQLITE_NOTADB", 26)
            raise error
        original_prove(connection, context=context)

    monkeypatch.setattr(database, "_prove_admitted_sqlite_identity", transient_prove)
    monkeypatch.setattr("oh_goon.runtime.database.time.sleep", lambda _delay: None)
    database.sqlite_diagnostics.set_enabled(True)

    assert database.durable_revision() == 0

    diagnostics = {item.logical_operation: item for item in database.sqlite_diagnostics.snapshot()}
    runtime = diagnostics["runtime transaction"]
    assert runtime.native_opens == 2
    assert runtime.transactions == 1
    assert runtime.reproof_requested == 1
    assert runtime.reproof_succeeded == 1
    assert runtime.reproof_failed == 0


def _sqlite_economics_snapshot(database: LocalDatabase) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            item.logical_operation,
            item.native_opens,
            item.observer_opens,
            item.transactions,
            item.reproof_requested,
            item.reproof_succeeded,
            item.reproof_failed,
        )
        for item in database.sqlite_diagnostics.snapshot()
    )


def test_sqlite_access_diagnostics_observer_effect_is_semantically_neutral(tmp_path: Path) -> None:
    """J-05/X14: enabling passive economics counters cannot change durable semantics."""

    def exercise(
        path: Path, *, diagnostics: bool
    ) -> tuple[int, int, tuple[tuple[object, ...], ...]]:
        database = LocalDatabase(path, journal_mode="DELETE")
        database.sqlite_diagnostics.set_enabled(diagnostics)
        before = database.durable_revision()
        after = database.durable_revision()
        return before, after, _sqlite_economics_snapshot(database)

    disabled = exercise(tmp_path / "disabled.db", diagnostics=False)
    enabled = exercise(tmp_path / "enabled.db", diagnostics=True)

    assert disabled[:2] == enabled[:2] == (0, 0)
    assert disabled[2] == ()
    assert enabled[2]
    assert sum(row[1] for row in enabled[2]) == 2
    assert sum(row[3] for row in enabled[2]) == 2
    assert sum(row[4] + row[5] + row[6] for row in enabled[2]) == 0


def test_sqlite_access_economics_distinguish_scoped_reuse_from_reconnect_churn(
    tmp_path: Path,
) -> None:
    """X13: one execution scope may cut many transactions without N+1 native opens."""
    database = LocalDatabase(tmp_path / "scoped-economics.db", journal_mode="DELETE")
    database.sqlite_diagnostics.set_enabled(True)

    with database.execution_connection_scope():
        for _ in range(5):
            assert database.durable_revision() == 0

    diagnostics = {item.logical_operation: item for item in database.sqlite_diagnostics.snapshot()}
    scope = diagnostics["execution connection scope"]
    transactions = diagnostics["scoped runtime transaction"]
    assert scope.native_opens == 1
    assert transactions.transactions == 5
    assert transactions.native_opens == 0
    assert scope.reproof_requested == scope.reproof_succeeded == scope.reproof_failed == 0


def test_sqlite_access_economics_keep_observer_opens_separate_from_native_authority(
    tmp_path: Path,
) -> None:
    """X13: read-only preparation observation is visible without becoming DB authority."""
    database = LocalDatabase(tmp_path / "observer-economics.db", journal_mode="DELETE")
    database.sqlite_diagnostics.set_enabled(True)

    with database.admitted_observer_connection() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    diagnostics = {item.logical_operation: item for item in database.sqlite_diagnostics.snapshot()}
    observation = diagnostics["preparation observation"]
    assert observation.observer_opens == 1
    assert observation.native_opens == 0
    assert observation.transactions == 0
    assert observation.reproof_requested == 0
    assert observation.reproof_succeeded == 0
    assert observation.reproof_failed == 0
