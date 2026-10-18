from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutionBusy, OwnershipLost
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution.sqlite_lease import SQLiteExecutionLeaseStore
from tests.unit.runtime.test_execution_lease import FakeLeaseEnvironment


def test_nested_execution_connection_scopes_reuse_one_native_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "nested-scope.db", journal_mode="DELETE")
    original_open = database._open_sqlite_connection
    admitted_opens = 0
    connection_ids: list[int] = []

    def counted_open(*, admitted: bool) -> sqlite3.Connection:
        nonlocal admitted_opens
        if admitted:
            admitted_opens += 1
        return original_open(admitted=admitted)

    monkeypatch.setattr(database, "_open_sqlite_connection", counted_open)

    with database.execution_connection_scope():
        with database.transaction() as connection:
            connection_ids.append(id(connection))
        with database.execution_connection_scope():
            with database.transaction() as connection:
                connection_ids.append(id(connection))
        with database.transaction() as connection:
            connection_ids.append(id(connection))

    assert admitted_opens == 1
    assert len(set(connection_ids)) == 1

    with database.transaction() as connection:
        connection_ids.append(id(connection))
    assert admitted_opens == 2
    assert connection_ids[-1] != connection_ids[0]


def test_recovery_observer_failure_cannot_change_sqlite_reproof_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "observer-failure.db", journal_mode="DELETE")
    original_open = database._open_sqlite_connection
    calls = 0

    def flaky_open(*, admitted: bool) -> sqlite3.Connection:
        nonlocal calls
        calls += 1
        if calls == 1:
            error = sqlite3.DatabaseError("file is not a database")
            error.sqlite_errorcode = getattr(sqlite3, "SQLITE_NOTADB", 26)
            raise error
        return original_open(admitted=admitted)

    def broken_diagnostic_observer(_event: object) -> None:
        raise RuntimeError("diagnostic sink failed")

    monkeypatch.setattr(database, "_open_sqlite_connection", flaky_open)
    monkeypatch.setattr("oh_goon.runtime.database.time.sleep", lambda _delay: None)
    database.set_recovery_observer(broken_diagnostic_observer)

    assert database.durable_revision() == 0
    assert calls == 2


def test_dead_owner_observation_is_read_only_until_mutating_owner_check(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "dead-owner-observation.db", journal_mode="DELETE")
    environment = FakeLeaseEnvironment(pid=111)
    leases = SQLiteExecutionLeaseStore(database, environment=environment)
    leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
    )
    environment.alive.clear()

    assert leases.active_lease() is None
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 1

    with database.transaction(write=True) as connection:
        leases.assert_idle(connection)
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0


def test_live_pid_without_birth_identity_fails_closed(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "owner-without-birth-identity.db", journal_mode="DELETE")
    environment = FakeLeaseEnvironment(pid=222)
    leases = SQLiteExecutionLeaseStore(database, environment=environment)
    lease, _ = leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
    )
    with database.transaction(write=True) as connection:
        connection.execute("UPDATE execution_lease SET owner_identity = NULL WHERE singleton = 1")

    observed = leases.active_lease()
    assert observed == lease
    with pytest.raises(ExecutionBusy, match="Direct execution"):
        leases.acquire_fleet_with(
            "must-not-steal",
            expected_durable_revision=database.durable_revision(),
            mutate=lambda _connection: None,
        )

    leases.finish_as_owner(lease, lambda _connection: None)


def test_fleet_cancel_rejects_different_active_fleet_owner(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "cancel-owner.db", journal_mode="DELETE")
    environment = FakeLeaseEnvironment()
    leases = SQLiteExecutionLeaseStore(database, environment=environment)
    lease, _ = leases.acquire_fleet_with(
        "fleet-a",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )

    with pytest.raises(OwnershipLost, match="no longer owns"):
        with database.transaction(write=True) as connection:
            leases.request_fleet_cancel_in(connection, "fleet-b")

    leases.finish_as_owner(lease, lambda _connection: None)


def test_same_token_with_changed_lease_identity_is_rejected(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "changed-identity.db", journal_mode="DELETE")
    leases = SQLiteExecutionLeaseStore(database)
    lease, _ = leases.acquire_fleet_with(
        "fleet-original",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE execution_lease SET fleet_run_id = ? WHERE singleton = 1",
            ("fleet-replaced",),
        )

    with pytest.raises(OwnershipLost, match="identity changed"):
        leases.assert_owner(lease)

    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM execution_lease WHERE singleton = 1")


def test_caller_owned_fleet_admission_transaction_mints_exact_lease(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "caller-owned-admission.db", journal_mode="DELETE")
    leases = SQLiteExecutionLeaseStore(database)
    expected_revision = database.durable_revision()

    with database.transaction(write=True) as connection:
        lease = leases.acquire_fleet_in(
            connection,
            "fleet-caller-owned",
            expected_durable_revision=expected_revision,
        )

    assert lease.kind == "FLEET"
    assert lease.fleet_run_id == "fleet-caller-owned"
    leases.assert_fleet_owner(lease, "fleet-caller-owned")
    with pytest.raises(OwnershipLost, match="does not own"):
        leases.assert_fleet_owner(lease, "fleet-other")
    leases.finish_as_owner(lease, lambda _connection: None)


def test_scoped_transaction_reproof_error_does_not_destroy_connection_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "scoped-reproof-error.db", journal_mode="DELETE")
    original_prove = database._prove_admitted_sqlite_identity

    with database.execution_connection_scope():

        def fail_proof(_connection: sqlite3.Connection, *, context: str) -> None:
            assert context == "scoped runtime transaction"
            raise sqlite3.DatabaseError("synthetic scoped proof failure")

        monkeypatch.setattr(database, "_prove_admitted_sqlite_identity", fail_proof)
        with pytest.raises(sqlite3.DatabaseError, match="synthetic scoped proof failure"):
            with database.transaction():
                raise AssertionError("transaction body must not start")

        monkeypatch.setattr(database, "_prove_admitted_sqlite_identity", original_prove)
        with database.transaction() as connection:
            assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_observer_open_closes_handle_when_path_identity_is_lost_during_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "observer-close-identity.db", journal_mode="DELETE")
    original_open = database._open_admitted_observer_connection
    opened: list[sqlite3.Connection] = []

    def tracked_open() -> sqlite3.Connection:
        connection = original_open()
        opened.append(connection)
        return connection

    def lose_identity(_connection: sqlite3.Connection) -> None:
        from oh_goon.runtime.database import DatabasePathIdentityLost

        raise DatabasePathIdentityLost("synthetic observer identity loss")

    monkeypatch.setattr(database, "_open_admitted_observer_connection", tracked_open)
    monkeypatch.setattr(database, "_configure_common_connection", lose_identity)

    from oh_goon.runtime.database import DatabasePathIdentityLost

    with pytest.raises(DatabasePathIdentityLost, match="synthetic observer identity loss"):
        with database.admitted_observer_connection():
            raise AssertionError("observer body must not start")

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")


def test_observer_open_closes_handle_on_non_identity_sqlite_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "observer-close-generic.db", journal_mode="DELETE")
    original_open = database._open_admitted_observer_connection
    opened: list[sqlite3.Connection] = []

    def tracked_open() -> sqlite3.Connection:
        connection = original_open()
        opened.append(connection)
        return connection

    def fail_configuration(_connection: sqlite3.Connection) -> None:
        raise sqlite3.DatabaseError("synthetic observer configuration failure")

    monkeypatch.setattr(database, "_open_admitted_observer_connection", tracked_open)
    monkeypatch.setattr(database, "_configure_common_connection", fail_configuration)

    with pytest.raises(sqlite3.DatabaseError, match="synthetic observer configuration failure"):
        with database.admitted_observer_connection():
            raise AssertionError("observer body must not start")

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")


def test_sqlite_access_diagnostics_reject_unknown_metric() -> None:
    from oh_goon.runtime._sqlite_access_diagnostics import SQLiteAccessDiagnostics

    diagnostics = SQLiteAccessDiagnostics()
    diagnostics.set_enabled(True)
    with pytest.raises(ValueError, match="unknown SQLite diagnostic metric"):
        diagnostics.record("test", "not-a-metric")


def test_sqlite_access_diagnostics_ignore_non_reproof_and_unknown_outcomes() -> None:
    from oh_goon.runtime._sqlite_access_diagnostics import SQLiteAccessDiagnostics
    from oh_goon.runtime.recovery_evidence import ExecutionRecoveryEvent

    diagnostics = SQLiteAccessDiagnostics()
    diagnostics.set_enabled(True)
    diagnostics.record_recovery(
        ExecutionRecoveryEvent(
            kind="other",
            action="ignored",
            reason_code="not_sqlite",
            outcome="requested",
        ),
        "test",
    )
    diagnostics.record_recovery(
        ExecutionRecoveryEvent(
            kind="sqlite_reproof",
            action="ignored",
            reason_code="unknown_outcome",
            outcome="other",
        ),
        "test",
    )

    assert diagnostics.snapshot() == ()
