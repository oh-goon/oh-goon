from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutionBusy, OwnershipLost
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import read_durable_revision
from oh_goon.runtime.execution.sqlite_lease import ExecutionLease, SQLiteExecutionLeaseStore
from tests.factories.local_state import local_state


class FakeLeaseEnvironment:
    def __init__(self, *, pid: int = 4242) -> None:
        self.current = datetime(2026, 8, 10, tzinfo=UTC)
        self.pid = pid
        self.alive: set[int] = {pid}
        self.identities: dict[int, str] = {pid: f"birth-{pid}-1"}

    def now(self) -> datetime:
        return self.current

    def process_id(self) -> int:
        return self.pid

    def process_alive(self, pid: int) -> bool:
        return pid in self.alive

    def process_identity(self, pid: int) -> str | None:
        return self.identities.get(pid) if pid in self.alive else None


def test_execution_lease_serializes_independent_process_sessions(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    environment = FakeLeaseEnvironment()
    first_database = LocalDatabase(path)
    second_database = LocalDatabase(path)
    first = SQLiteExecutionLeaseStore(first_database, environment=environment)
    second = SQLiteExecutionLeaseStore(second_database, environment=environment)

    direct = first.acquire_direct_with(
        expected_durable_revision=first_database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    with pytest.raises(ExecutionBusy, match="Direct execution"):
        second.acquire_fleet_with(
            "fleet-2",
            expected_durable_revision=second_database.durable_revision(),
            mutate=lambda _connection: None,
        )

    first.finish_as_owner(direct, lambda _connection: None)
    fleet, _ = second.acquire_fleet_with(
        "fleet-2",
        expected_durable_revision=second_database.durable_revision(),
        mutate=lambda _connection: None,
    )
    second.finish_as_owner(fleet, lambda _connection: None)


def test_fleet_admission_replay_never_adopts_another_runtime_session(
    tmp_path: Path,
) -> None:
    path = tmp_path / "replay-owner.db"
    environment = FakeLeaseEnvironment()
    first_database = LocalDatabase(path)
    second_database = LocalDatabase(path)
    first = SQLiteExecutionLeaseStore(first_database, environment=environment)
    second = SQLiteExecutionLeaseStore(second_database, environment=environment)
    lease, _ = first.acquire_fleet_with(
        "fleet-owned",
        expected_durable_revision=first_database.durable_revision(),
        mutate=lambda _connection: None,
    )

    with first_database.transaction() as connection:
        assert first.owned_fleet_lease_in(connection, "fleet-owned") == lease
    with second_database.transaction() as connection:
        assert second.owned_fleet_lease_in(connection, "fleet-owned") is None

    first.finish_as_owner(lease, lambda _connection: None)


def test_scope_mutation_is_blocked_by_direct_execution_lease(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    leases = SQLiteExecutionLeaseStore(database)
    lease = leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]

    try:
        with pytest.raises(ExecutionBusy, match="Cannot change fleet scope"):
            state.add_location(tmp_path / "repos")
    finally:
        leases.finish_as_owner(lease, lambda _connection: None)


def test_dead_owner_pid_allows_immediate_lease_recovery(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    environment = FakeLeaseEnvironment(pid=1001)
    leases = SQLiteExecutionLeaseStore(database, environment=environment)
    abandoned = leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    environment.alive.clear()
    environment.pid = 2002
    environment.alive.add(2002)

    replacement_database = LocalDatabase(database.path)
    replacement_store = SQLiteExecutionLeaseStore(replacement_database, environment=environment)
    replacement, _ = replacement_store.acquire_fleet_with(
        "fleet-recovered",
        expected_durable_revision=replacement_database.durable_revision(),
        mutate=lambda _connection: None,
    )

    assert replacement.fleet_run_id == "fleet-recovered"
    replacement_store.finish_as_owner(replacement, lambda _connection: None)
    # The dead owner's stale row was reclaimed during replacement admission.
    with pytest.raises(OwnershipLost):
        leases.finish_as_owner(abandoned, lambda _connection: None)


def test_live_owner_is_never_expired_by_elapsed_time(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "deterministic-lease.db")
    environment = FakeLeaseEnvironment()
    first = SQLiteExecutionLeaseStore(database, environment=environment)
    lease = first.acquire_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]

    environment.current += timedelta(days=365)
    second_database = LocalDatabase(database.path)
    second = SQLiteExecutionLeaseStore(second_database, environment=environment)

    with pytest.raises(ExecutionBusy):
        second.acquire_fleet_with(
            "fleet-must-not-steal",
            expected_durable_revision=second_database.durable_revision(),
            mutate=lambda _connection: None,
        )
    first.assert_owner(lease)
    first.finish_as_owner(lease, lambda _connection: None)


def test_ownership_transitions_advance_durable_revision_without_heartbeat_state(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    leases = SQLiteExecutionLeaseStore(database)

    with database.transaction() as connection:
        before = read_durable_revision(connection)

    lease, _ = leases.acquire_fleet_with(
        "fleet-1",
        expected_durable_revision=before,
        mutate=lambda _connection: None,
    )
    assert database.durable_revision() == before + 1
    leases.finish_as_owner(lease, lambda _connection: None)
    assert database.durable_revision() == before + 2

    with database.transaction() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(execution_lease)").fetchall()
        }
    assert "heartbeat_at" not in columns


def test_reused_live_pid_with_different_birth_identity_reclaims_stale_lease(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "pid-reuse.db")
    environment = FakeLeaseEnvironment(pid=1001)
    first = SQLiteExecutionLeaseStore(database, environment=environment)
    stale = first.acquire_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]

    # The numeric PID is live again, but it names a different process birth.
    environment.identities[1001] = "birth-1001-2"
    replacement_database = LocalDatabase(database.path)
    replacement = SQLiteExecutionLeaseStore(replacement_database, environment=environment)
    lease, _ = replacement.acquire_fleet_with(
        "fleet-after-pid-reuse",
        expected_durable_revision=replacement_database.durable_revision(),
        mutate=lambda _connection: None,
    )

    assert lease.fleet_run_id == "fleet-after-pid-reuse"
    replacement.finish_as_owner(lease, lambda _connection: None)
    with pytest.raises(OwnershipLost):
        first.finish_as_owner(stale, lambda _connection: None)


def test_execution_lease_schema_records_process_birth_identity(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "identity.db")
    environment = FakeLeaseEnvironment(pid=777)
    leases = SQLiteExecutionLeaseStore(database, environment=environment)
    lease, _ = leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )

    with database.transaction() as connection:
        row = connection.execute(
            "SELECT owner_pid, owner_identity, lease_token FROM execution_lease WHERE singleton = 1"
        ).fetchone()
    assert row is not None
    assert int(row["owner_pid"]) == 777
    assert row["owner_identity"] == "birth-777-1"
    assert row["lease_token"] == lease.token

    leases.finish_as_owner(lease, lambda _connection: None)


def test_owner_critical_mutation_rejects_stale_token_before_write(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    leases = SQLiteExecutionLeaseStore(database)
    lease = leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    stale = ExecutionLease(token="lease-wrong", kind="DIRECT", fleet_run_id=None)

    with pytest.raises(OwnershipLost):
        leases.mutate_as_owner(
            stale,
            lambda connection: connection.execute(
                "INSERT INTO locations(path) VALUES (?)",
                (str(tmp_path / "must-not-write"),),
            ),
        )

    with database.transaction() as connection:
        count = connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    assert count == 0
    leases.finish_as_owner(lease, lambda _connection: None)


def test_owner_observation_is_read_only_and_rejects_stale_token(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "owner-observe.db")
    leases = SQLiteExecutionLeaseStore(database)
    lease = leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
    )[0]
    revision = database.durable_revision()

    assert leases.observe_as_owner(lease, read_durable_revision) == revision
    assert database.durable_revision() == revision
    stale = ExecutionLease(token="lease-wrong", kind="DIRECT", fleet_run_id=None)
    with pytest.raises(OwnershipLost):
        leases.observe_as_owner(stale, read_durable_revision)

    leases.finish_as_owner(lease, lambda _connection: None)


def test_finish_as_owner_persists_terminal_mutation_and_releases_lease_atomically(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    leases = SQLiteExecutionLeaseStore(database)
    lease = leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]

    leases.finish_as_owner(
        lease,
        lambda connection: connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            ("/tmp/lease-finish-probe", 1, "2026-09-06T00:00:00Z"),
        ),
    )

    assert leases.active_lease() is None
    with database.transaction() as connection:
        values = [
            row[0]
            for row in connection.execute(
                "SELECT path FROM locations WHERE path = ?", ("/tmp/lease-finish-probe",)
            ).fetchall()
        ]
    assert values == ["/tmp/lease-finish-probe"]
    with pytest.raises(OwnershipLost):
        leases.mutate_as_owner(lease, lambda _connection: None)
