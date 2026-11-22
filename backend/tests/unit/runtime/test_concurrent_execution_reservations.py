"""Bounded admission remains one original SQLite owner and exact Run capability."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from oh_goon.core.errors import DatabaseSchemaMismatch, ExecutionBusy, OwnershipLost, StateConflict
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionTicket
from oh_goon.runtime.execution.sqlite_lease import SQLiteExecutionLeaseStore
from oh_goon.runtime.local_state_maintenance import reset_local_state
from tests.factories.local_state import local_state
from tests.factories.run_concurrency import run_concurrency_admission, run_concurrency_scope


def reserve(database, store, run_id):
    return store.acquire_fleet_with(
        run_id,
        expected_durable_revision=database.durable_revision(),
        concurrency=run_concurrency_admission(run_id),
        deferred_worker_resource_scope=run_concurrency_scope(run_id).to_payload(),
        mutate=lambda _connection: run_id,
    )[0]


def test_two_original_reservations_and_atomic_third_refusal(tmp_path):
    database = LocalDatabase(tmp_path / "state.sqlite")
    store = SQLiteExecutionLeaseStore(database)
    first = reserve(database, store, "run-a")
    second = reserve(database, store, "run-b")
    revision = database.durable_revision()
    callbacks = []
    with pytest.raises(ExecutionBusy, match="capacity is full"):
        store.acquire_fleet_with(
            "run-c",
            expected_durable_revision=revision,
            concurrency=run_concurrency_admission("run-c"),
            deferred_worker_resource_scope=run_concurrency_scope("run-c").to_payload(),
            mutate=lambda _connection: callbacks.append(True),
        )
    assert not callbacks and database.durable_revision() == revision
    with database.transaction() as connection:
        assert set(store.active_leases_in(connection)) == {first, second}
        assert store.active_fleet_lease_in(connection, "run-b") == second
        assert store.active_fleet_lease_in(connection, "unrelated") is None
        with pytest.raises(ExecutionBusy, match="explicit Run"):
            store.active_lease_in(connection)


def test_same_native_budget_cannot_be_adopted_by_another_runtime_session(tmp_path):
    path = tmp_path / "state.sqlite"
    first_db, second_db = LocalDatabase(path), LocalDatabase(path)
    reserve(first_db, SQLiteExecutionLeaseStore(first_db), "run-a")
    with pytest.raises(ExecutionBusy, match="already active"):
        reserve(second_db, SQLiteExecutionLeaseStore(second_db), "run-b")
    with first_db.transaction() as connection:
        assert len(SQLiteExecutionLeaseStore(first_db).retained_leases_in(connection)) == 1


def test_remaining_slot_two_blocks_direct_serial_scope_and_reset_without_cross_cancel(tmp_path):
    database = LocalDatabase(tmp_path / "state.sqlite")
    store = SQLiteExecutionLeaseStore(database)
    remaining = reserve(database, store, "run-b")
    # This is the durable state after another reservation retired from slot1.
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE execution_lease SET singleton=2 WHERE lease_token=?", (remaining.token,)
        )
    with pytest.raises(ExecutionBusy):
        store.acquire_direct_with(
            expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
        )
    with pytest.raises(ExecutionBusy):
        store.acquire_fleet_with(
            "serial",
            expected_durable_revision=database.durable_revision(),
            mutate=lambda _connection: None,
        )
    with pytest.raises(ExecutionBusy, match="Cannot change fleet scope"):
        local_state(database).add_location(tmp_path / "new-location")
    with pytest.raises(StateConflict, match="execution_lease"):
        reset_local_state(database.path)
    revision = database.durable_revision()
    with pytest.raises(OwnershipLost, match="no longer owns"):
        with database.transaction(write=True) as connection:
            store.request_fleet_cancel_in(connection, "run-a")
    assert database.durable_revision() == revision
    with database.transaction(write=True) as connection:
        assert store.active_fleet_lease_in(connection, "run-b") == remaining
        assert (
            connection.execute(
                "SELECT cancel_requested_at FROM execution_lease WHERE fleet_run_id='run-b'"
            ).fetchone()[0]
            is None
        )
        assert store.request_fleet_cancel_in(connection, "run-b") is True
        rows = connection.execute(
            "SELECT fleet_run_id,cancel_requested_at FROM execution_lease"
        ).fetchall()
        assert (
            len(rows) == 1 and rows[0]["fleet_run_id"] == "run-b" and rows[0]["cancel_requested_at"]
        )


def test_closing_run_cancellation_keeps_its_reservation_and_live_sibling_unchanged(tmp_path):
    database = LocalDatabase(tmp_path / "state.sqlite")
    store = SQLiteExecutionLeaseStore(database)
    first, second = reserve(database, store, "run-a"), reserve(database, store, "run-b")
    store.finish_submission_as_owner(first, lambda _connection: None)
    with database.transaction(write=True) as connection:
        before = store.retained_leases_in(connection)
        assert store.request_fleet_cancel_in(connection, "run-a") is False
        assert store.retained_leases_in(connection) == before
        assert store.active_fleet_lease_in(connection, "run-b") == second
        assert (
            connection.execute(
                "SELECT cancel_requested_at FROM execution_lease WHERE fleet_run_id='run-b'"
            ).fetchone()[0]
            is None
        )


def test_dead_worker_cancellation_uses_its_retained_slot_without_reclaiming_sibling(tmp_path):
    database = LocalDatabase(tmp_path / "state.sqlite")
    store = SQLiteExecutionLeaseStore(database)
    first, second = reserve(database, store, "run-a"), reserve(database, store, "run-b")
    ended = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    # Reconstruct the retained crash cut. This establishes no native resource
    # closure or executable claim, and the physical reservation must remain.
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE execution_lease SET custody_phase='WORKER_OWNED', "
            "owner_pid=?, owner_identity='ended-test-worker', owner_session_id='ended-worker' "
            "WHERE lease_token=?",
            (int(ended.stdout.strip()), first.token),
        )
        before = store.retained_leases_in(connection)
        assert store.request_fleet_cancel_in(connection, "run-a") is False
        assert store.retained_leases_in(connection) == before
        assert store.active_fleet_lease_in(connection, "run-b") == second
        assert (
            connection.execute(
                "SELECT cancel_requested_at FROM execution_lease WHERE fleet_run_id='run-b'"
            ).fetchone()[0]
            is None
        )
    with pytest.raises(ExecutionBusy, match="capacity is full"):
        reserve(database, store, "run-c")


def test_logical_finish_retains_nonexecutable_resource_occupancy(tmp_path):
    database = LocalDatabase(tmp_path / "state.sqlite")
    store = SQLiteExecutionLeaseStore(database)
    first, second = reserve(database, store, "run-a"), reserve(database, store, "run-b")
    assert (
        store.finish_submission_as_owner(first, lambda _connection: "logical terminal")
        == "logical terminal"
    )
    with pytest.raises(OwnershipLost):
        store.assert_submission_owner(first)
    with pytest.raises(ExecutionBusy, match="capacity is full"):
        reserve(database, store, "run-c")
    with database.transaction() as connection:
        retained = store.retained_fleet_lease_in(connection, "run-a")
        assert retained is not None and retained.custody.phase == "RESOURCE_RESERVED"
        assert store.active_fleet_lease_in(connection, "run-a") is None
        assert store.active_fleet_lease_in(connection, "run-b") == second


def test_concurrency_payload_removal_cannot_make_original_ticket_release_early(tmp_path):
    database = LocalDatabase(tmp_path / "state.sqlite")
    store = SQLiteExecutionLeaseStore(database)
    lease = reserve(database, store, "run-a")
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE execution_lease SET concurrency_json=json_set(concurrency_json, '$.worker_scope_identity', 'sha256:' || ?) WHERE lease_token=?",
            (
                "0" * 64,
                lease.token,
            ),
        )
    callbacks = []
    with pytest.raises(OwnershipLost):
        store.finish_submission_as_owner(lease, lambda _connection: callbacks.append(True))
    assert not callbacks
    with database.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 1


def test_failed_second_publication_rolls_back_only_its_provisional_slot(tmp_path):
    database = LocalDatabase(tmp_path / "state.sqlite")
    store = SQLiteExecutionLeaseStore(database)
    first = reserve(database, store, "run-a")
    revision = database.durable_revision()

    def reject(_connection):
        raise RuntimeError("Journal publication failed")

    with pytest.raises(RuntimeError, match="Journal publication"):
        store.acquire_fleet_with(
            "run-b",
            expected_durable_revision=revision,
            concurrency=run_concurrency_admission("run-b"),
            deferred_worker_resource_scope=run_concurrency_scope("run-b").to_payload(),
            mutate=reject,
        )
    assert database.durable_revision() == revision
    with database.transaction() as connection:
        assert store.retained_leases_in(connection) == (first,)
    assert reserve(database, store, "run-b").fleet_run_id == "run-b"


def test_schema94_is_preserved_and_explicitly_refused(tmp_path):
    path = tmp_path / "old.sqlite"
    LocalDatabase(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA user_version=94")
        connection.commit()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(DatabaseSchemaMismatch, match="schema 94 is unsupported"):
        LocalDatabase(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_serial_handoff_bytes_stay_v1_and_optin_binds_admission_identity(tmp_path):
    database = LocalDatabase(tmp_path / "state.sqlite")
    coordinator = ExecutionCoordinator(database)
    serial, _ = coordinator.reserve_fleet_with(
        "serial",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )
    payload = serial.export_worker_handoff()
    assert payload["version"] == 1 and "concurrency_identity" not in payload
    assert ExecutionTicket.from_worker_handoff(payload) == serial
    coordinator.activate_submission_failure(serial).finish(lambda _connection: None)
    concurrent, _ = coordinator.reserve_fleet_with(
        "parallel",
        expected_durable_revision=database.durable_revision(),
        concurrency=run_concurrency_admission("parallel"),
        deferred_worker_resource_scope=run_concurrency_scope("parallel").to_payload(),
        mutate=lambda _connection: None,
    )
    payload = concurrent.export_worker_handoff()
    assert payload["version"] == 2
    assert payload["concurrency_identity"] == run_concurrency_admission("parallel").identity
    assert ExecutionTicket.from_worker_handoff(payload) == concurrent


def test_private_native_reader_substitution_is_not_a_new_authority(tmp_path):
    database = LocalDatabase(tmp_path / "state.sqlite")
    store = SQLiteExecutionLeaseStore(database)
    lease = reserve(database, store, "run-a")
    payload = run_concurrency_scope("run-a").to_payload()
    payload["budget"]["journalctl"] = "/fixture/substitute-reader"
    payload["budget"]["journalctl_sha256"] = "e" * 64
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE execution_lease SET deferred_worker_resource_scope_json=? WHERE fleet_run_id=?",
            (json.dumps(payload), "run-a"),
        )
    called = []
    with pytest.raises(OwnershipLost, match="private worker resource intent"):
        store.finish_submission_as_owner(lease, lambda _connection: called.append(True))
    assert not called


def test_fresh_worker_with_matching_passive_envelope_still_cannot_claim_without_native_authority(
    tmp_path,
):
    database = LocalDatabase(tmp_path / "state.sqlite")
    coordinator = ExecutionCoordinator(database)
    deferred = run_concurrency_scope("run-a")
    ticket, _ = coordinator.reserve_fleet_with(
        "run-a",
        expected_durable_revision=database.durable_revision(),
        concurrency=deferred.admission,
        deferred_worker_resource_scope=deferred.to_payload(),
        mutate=lambda _connection: None,
    )
    handoff = tmp_path / "private-capability.json"
    handoff.write_text(json.dumps(ticket.export_worker_handoff()))
    handoff.chmod(0o600)
    script = """
import json,sys
from pathlib import Path
from oh_goon.core.errors import OwnershipLost
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator,ExecutionTicket
database=LocalDatabase(Path(sys.argv[1]))
owner=ExecutionCoordinator(database)
ticket=ExecutionTicket.from_worker_handoff(json.loads(Path(sys.argv[2]).read_text()))
with database.transaction() as connection:
 admission=owner.concurrency_in(connection,"run-a")
envelope=RunResourceEnvelopeEvidence(subject="worker",fleet_run_id="run-a",unit_name=admission.worker_unit_name,
 invocation_id="b"*32,handoff_sha256="sha256:"+"f"*64,process_birth=observe_process_birth(),
 resources=admission.policy.worker_resources,cgroup_device=1,cgroup_inode=4,cpu_max="100000 100000",
 memory_max_bytes=384*1024*1024,memory_swap_max_bytes=0,pids_max=48,oom_policy="kill",memory_oom_group=True)
try:
 owner.claim_fleet_worker(ticket,resource_envelope=envelope)
except OwnershipLost as error:
 assert "resource claim has no original verifier" in str(error)
 print("refused")
else:
 raise AssertionError("Passive fields claimed executable custody")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(database.path), str(handoff)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    with database.transaction() as connection:
        retained = coordinator.retained_fleet_lease_in(connection, "run-a")
        assert retained.custody.phase == "ADMITTED_UNCLAIMED"
