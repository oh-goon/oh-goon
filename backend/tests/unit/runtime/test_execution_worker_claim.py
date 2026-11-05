from __future__ import annotations

import multiprocessing
import os
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from oh_goon.core.errors import OwnershipLost
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.runtime.database import LocalDatabase, WorkerDatabaseContext
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionTicket
from oh_goon.runtime.execution.lease_environment import SystemLeaseEnvironment
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import capability_snapshot


class _ProcessEnvironment:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.birth: str | None = f"birth-{pid}"

    def now(self) -> datetime:
        return datetime(2026, 11, 5, tzinfo=UTC)

    def process_id(self) -> int:
        return self.pid

    def process_alive(self, pid: int) -> bool:
        return pid == self.pid

    def process_identity(self, pid: int) -> str | None:
        return self.birth if pid == self.pid else None


def _admit(
    database: LocalDatabase, coordinator: ExecutionCoordinator, run_id: str = "worker-run"
) -> ExecutionTicket:
    journal = Journal(database)
    intent = FleetRunAdmissionIntent(
        capability=capability_snapshot(OperationAction.INIT),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    ticket, _ = coordinator.reserve_fleet_with(
        run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: journal.admit_run_in(
            connection, run_id, intent, fleet_repository_scope(None), []
        ),
    )
    return ticket


def _owners(
    tmp_path: Path,
) -> tuple[
    LocalDatabase, ExecutionCoordinator, ExecutionTicket, LocalDatabase, ExecutionCoordinator
]:
    parent_database = LocalDatabase(tmp_path / "state.sqlite")
    parent = ExecutionCoordinator(parent_database, lease_environment=_ProcessEnvironment(101))
    ticket = _admit(parent_database, parent)
    worker_database = LocalDatabase(parent_database.path)
    worker = ExecutionCoordinator(worker_database, lease_environment=_ProcessEnvironment(202))
    return parent_database, parent, ticket, worker_database, worker


def test_worker_claim_transfers_custody_once_without_changing_admitted_work(tmp_path: Path) -> None:
    database, parent, ticket, worker_database, worker = _owners(tmp_path)
    admitted = Journal(database).read_view().required_run_detail("worker-run")
    revision = database.durable_revision()
    claimed = worker.claim_fleet_worker(ticket)

    assert claimed._lease.token == ticket._lease.token
    assert claimed._lease.custody is not None
    assert claimed._lease.custody.phase == "WORKER_OWNED"
    assert claimed._lease.custody.owner_pid == 202
    assert claimed._lease.custody.owner_session_id == worker_database.session_id
    assert claimed.require_fleet_run_id() == "worker-run"
    assert database.durable_revision() == revision + 1
    assert Journal(database).read_view().required_run_detail("worker-run") == admitted
    assert [
        event.event_type for event in Journal(database).read_view().run_events("worker-run").events
    ] == [FleetRunEventType.RUN_ADMITTED]
    worker.require_claimed_fleet_worker(claimed)
    worker.activate(claimed).assert_owner()

    for coordinator in (parent, worker):
        with pytest.raises(OwnershipLost):
            coordinator.claim_fleet_worker(ticket)
        with pytest.raises(OwnershipLost):
            coordinator.activate(ticket)
        with pytest.raises(OwnershipLost):
            coordinator.activate_submission_failure(ticket)
    with pytest.raises(OwnershipLost):
        parent.activate(claimed)
    with pytest.raises(OwnershipLost):
        parent.require_claimed_fleet_worker(claimed)
    assert database.durable_revision() == revision + 1


@pytest.mark.parametrize(
    "attack", ["run", "token", "session", "pid", "birth", "phase", "missing-custody"]
)
def test_claim_rejects_inexact_admission_before_any_durable_effect(
    tmp_path: Path, attack: str
) -> None:
    database, _parent, ticket, _worker_database, worker = _owners(tmp_path)
    lease = ticket._lease
    custody = lease.custody
    assert custody is not None
    if attack == "run":
        lease = replace(lease, fleet_run_id="another-run")
    elif attack == "token":
        lease = replace(lease, token="wrong-token")
    elif attack == "session":
        lease = replace(lease, custody=replace(custody, owner_session_id="another-session"))
    elif attack == "pid":
        lease = replace(lease, custody=replace(custody, owner_pid=303))
    elif attack == "birth":
        lease = replace(lease, custody=replace(custody, owner_identity="another-birth"))
    elif attack == "phase":
        lease = replace(lease, custody=replace(custody, phase="WORKER_OWNED"))
    else:
        lease = replace(lease, custody=None)
    revision = database.durable_revision()
    before = Journal(database).read_view().required_run_detail("worker-run")
    with pytest.raises(OwnershipLost):
        worker.claim_fleet_worker(ExecutionTicket(lease))
    assert database.durable_revision() == revision
    assert Journal(database).read_view().required_run_detail("worker-run") == before


@pytest.mark.parametrize("attack", ["same-process", "same-session", "unknown-birth"])
def test_claim_requires_a_fresh_proven_worker_process_and_database_session(
    tmp_path: Path, attack: str
) -> None:
    database, _parent, ticket, worker_database, _worker = _owners(tmp_path)
    environment = _ProcessEnvironment(101 if attack == "same-process" else 202)
    if attack == "unknown-birth":
        environment.birth = None
    worker = ExecutionCoordinator(
        database if attack == "same-session" else worker_database,
        lease_environment=environment,
    )
    revision = database.durable_revision()
    with pytest.raises(OwnershipLost):
        worker.claim_fleet_worker(ticket)
    assert database.durable_revision() == revision


@pytest.mark.parametrize("phase", ["started", "cancelled", "terminal"])
def test_claim_requires_the_same_uncancelled_queued_journal_phase(
    tmp_path: Path, phase: str
) -> None:
    database, parent, ticket, _worker_database, worker = _owners(tmp_path)
    journal = Journal(database)
    with database.transaction(write=True) as connection:
        if phase == "started":
            journal.start_run_in(connection, "worker-run")
        elif phase == "cancelled":
            parent.request_fleet_cancel_in(connection, "worker-run")
            journal.request_cancel_in(connection, "worker-run")
        else:
            journal.terminalize_run_in(connection, "worker-run", FleetRunTerminalIntent.CANCEL)
    revision = database.durable_revision()
    with pytest.raises(OwnershipLost):
        worker.claim_fleet_worker(ticket)
    assert database.durable_revision() == revision


def test_submission_owner_can_only_settle_the_still_unclaimed_admission(tmp_path: Path) -> None:
    database, parent, ticket, _worker_database, _worker = _owners(tmp_path)
    settlement = parent.activate_submission_failure(ticket)
    with pytest.raises(OwnershipLost):
        parent.activate(ticket)
    with pytest.raises(OwnershipLost):
        settlement.observe(lambda _connection: pytest.fail("must not observe execution"))
    with pytest.raises(OwnershipLost):
        settlement.mutate(lambda _connection: pytest.fail("must not execute"))
    with pytest.raises(OwnershipLost):
        with settlement.execution_connection_scope():
            pytest.fail("must not acquire an execution scope")
    state = settlement.finish(
        lambda connection: Journal(database).terminalize_run_in(
            connection, "worker-run", FleetRunTerminalIntent.CANCEL
        )
    )
    assert state.status is FleetRunStatus.CANCELLED
    assert parent.active_lease() is None
    with pytest.raises(OwnershipLost):
        settlement.finish(lambda _connection: pytest.fail("must not settle twice"))


def test_worker_mutations_reprove_current_process_birth_and_session(tmp_path: Path) -> None:
    database, _parent, ticket, worker_database, worker = _owners(tmp_path)
    claimed = worker.claim_fleet_worker(ticket)
    session = worker.activate(claimed)
    environment = worker._leases._environment
    assert isinstance(environment, _ProcessEnvironment)
    environment.birth = "reused-pid"
    with pytest.raises(OwnershipLost):
        session.mutate(lambda _connection: pytest.fail("must not mutate under reused PID"))
    with pytest.raises(OwnershipLost):
        session.finish(lambda _connection: pytest.fail("must not finish under reused PID"))
    environment.birth = "birth-202"
    foreign = ExecutionCoordinator(
        LocalDatabase(worker_database.path), lease_environment=_ProcessEnvironment(202)
    )
    with pytest.raises(OwnershipLost):
        foreign.activate(claimed)
    assert Journal(database).read_view().required_run_detail("worker-run").run.status is (
        FleetRunStatus.QUEUED
    )


@pytest.mark.parametrize(
    "change",
    [
        "custody_phase='DIRECT_OWNED'",
        "custody_phase='RECOVERY_OWNED'",
        "custody_phase='WORKER_OWNED',owner_identity=NULL",
        "custody_phase='UNKNOWN'",
        "owner_session_id='recovery:foreign'",
        "owner_pid=0",
        "fleet_run_id=''",
        "owner_identity=''",
    ],
)
def test_storage_rejects_contradictory_execution_custody(tmp_path: Path, change: str) -> None:
    database, _parent, _ticket, _worker_database, _worker = _owners(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction(write=True) as connection:
            connection.execute(f"UPDATE execution_lease SET {change} WHERE singleton=1")


def test_capability_reprs_do_not_disclose_the_lease_token(tmp_path: Path) -> None:
    _database, _parent, ticket, _worker_database, _worker = _owners(tmp_path)
    assert ticket._lease.token not in repr(ticket)
    assert ticket._lease.token not in repr(ticket._lease)


def test_worker_handoff_preserves_the_original_single_use_admission(tmp_path: Path) -> None:
    database, _parent, ticket, _worker_database, worker = _owners(tmp_path)
    handoff = ticket.export_worker_handoff()
    restored = ExecutionTicket.from_worker_handoff(handoff)
    assert restored == ticket
    assert restored.export_worker_handoff() == handoff
    claimed = worker.claim_fleet_worker(restored)
    worker.activate(claimed).assert_owner()
    with pytest.raises(OwnershipLost):
        worker.claim_fleet_worker(ExecutionTicket.from_worker_handoff(handoff))
    assert Journal(database).read_view().required_run_detail("worker-run").run.status is (
        FleetRunStatus.QUEUED
    )


@pytest.mark.parametrize(
    "attack",
    (
        "extra",
        "missing",
        "version",
        "boolean-version",
        "run-type",
        "empty-token",
        "predecessor-type",
        "extra-predecessor",
        "missing-predecessor",
        "worker-phase",
        "recovery-session",
        "boolean-pid",
        "empty-birth",
    ),
)
def test_worker_handoff_rejects_invalid_shape_without_echoing_capability(
    tmp_path: Path, attack: str
) -> None:
    database, _parent, ticket, _worker_database, _worker = _owners(tmp_path)
    handoff = deepcopy(ticket.export_worker_handoff())
    predecessor = handoff["predecessor"]
    assert isinstance(predecessor, dict)
    if attack == "extra":
        handoff["recovery_only"] = False
    elif attack == "missing":
        del handoff["lease_token"]
    elif attack == "version":
        handoff["version"] = 2
    elif attack == "boolean-version":
        handoff["version"] = True
    elif attack == "run-type":
        handoff["fleet_run_id"] = ["worker-run"]
    elif attack == "empty-token":
        handoff["lease_token"] = ""
    elif attack == "predecessor-type":
        handoff["predecessor"] = []
    elif attack == "extra-predecessor":
        predecessor["kind"] = "FLEET"
    elif attack == "missing-predecessor":
        del predecessor["owner_pid"]
    elif attack == "worker-phase":
        predecessor["phase"] = "WORKER_OWNED"
    elif attack == "recovery-session":
        predecessor["owner_session_id"] = "recovery:foreign"
    elif attack == "boolean-pid":
        predecessor["owner_pid"] = True
    else:
        predecessor["owner_identity"] = ""
    revision = database.durable_revision()
    with pytest.raises(OwnershipLost) as captured:
        ExecutionTicket.from_worker_handoff(handoff)
    assert str(captured.value) == "invalid protected Fleet worker capability"
    assert ticket._lease.token not in str(captured.value)
    assert database.durable_revision() == revision


@pytest.mark.parametrize("kind", ("direct", "claimed", "recovery", "unknown-birth"))
def test_worker_handoff_never_exports_another_custody_capability(tmp_path: Path, kind: str) -> None:
    _database, _parent, ticket, _worker_database, worker = _owners(tmp_path)
    if kind == "direct":
        ticket = ExecutionTicket(
            replace(ticket._lease, kind="DIRECT", fleet_run_id=None, custody=None)
        )
    elif kind == "claimed":
        ticket = worker.claim_fleet_worker(ticket)
    elif kind == "recovery":
        ticket = replace(ticket, recovery_only=True)
    else:
        custody = ticket._lease.custody
        assert custody is not None
        ticket = ExecutionTicket(
            replace(ticket._lease, custody=replace(custody, owner_identity=None))
        )
    with pytest.raises(OwnershipLost) as captured:
        ticket.export_worker_handoff()
    assert ticket._lease.token not in str(captured.value)


def test_direct_execution_retains_its_existing_unknown_birth_behavior(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "direct.sqlite")
    environment = _ProcessEnvironment(101)
    environment.birth = None
    coordinator = ExecutionCoordinator(database, lease_environment=environment)
    ticket, _ = coordinator.reserve_direct_with(
        expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
    )
    coordinator.activate(ticket).finish(lambda _connection: None)
    assert coordinator.active_lease() is None


def _race_claimant(
    authority: WorkerDatabaseContext,
    ticket: ExecutionTicket,
    ready: Connection,
    start: Connection,
    result: Connection,
    release: Connection,
) -> None:
    try:
        database = LocalDatabase.open_worker(authority)
        coordinator = ExecutionCoordinator(database)
        ready.send(os.getpid())
        if not start.poll(12):
            raise AssertionError("claim start signal unavailable")
        start.recv()
        try:
            claimed = coordinator.claim_fleet_worker(ticket)
        except OwnershipLost:
            result.send(("REJECTED", os.getpid()))
            return
        try:
            coordinator.claim_fleet_worker(ticket)
        except OwnershipLost:
            pass
        else:
            raise AssertionError("claimed admission could be replayed")
        session = coordinator.activate(claimed)
        session.mutate(
            lambda connection: connection.execute(
                "INSERT INTO locations(path,position,added_at) VALUES ('claim-winner',1,'now')"
            )
        )
        result.send(("CLAIMED", os.getpid()))
        if not release.poll(12):
            raise AssertionError("claim release signal unavailable")
        release.recv()
        session.finish(
            lambda connection: Journal(database).terminalize_run_in(
                connection, "worker-run", FleetRunTerminalIntent.CANCEL
            )
        )
    finally:
        for channel in (ready, start, result, release):
            channel.close()


@pytest.mark.process
def test_two_fresh_processes_claim_one_admission_with_one_executable_winner(tmp_path: Path) -> None:
    if SystemLeaseEnvironment().process_identity(os.getpid()) is None:
        pytest.skip("positive native process-birth identity unavailable")
    database = LocalDatabase(tmp_path / "race.sqlite")
    parent = ExecutionCoordinator(database)
    ticket = _admit(database, parent)
    authority = database.capture_worker_context()
    context = multiprocessing.get_context("spawn")
    workers = []
    try:
        for _ in range(2):
            ready_read, ready_write = context.Pipe(duplex=False)
            start_read, start_write = context.Pipe(duplex=False)
            result_read, result_write = context.Pipe(duplex=False)
            release_read, release_write = context.Pipe(duplex=False)
            process = context.Process(
                target=_race_claimant,
                args=(authority, ticket, ready_write, start_read, result_write, release_read),
            )
            process.start()
            for channel in (ready_write, start_read, result_write, release_read):
                channel.close()
            workers.append((process, ready_read, start_write, result_read, release_write))
        for _process, ready, _start, _result, _release in workers:
            assert ready.poll(15), "fresh claimant failed before readiness"
            ready.recv()
        for _process, _ready, start, _result, _release in workers:
            start.send("claim")
        outcomes = []
        for _process, _ready, _start, result, _release in workers:
            assert result.poll(15), "claimant failed without an outcome"
            outcomes.append(result.recv())
        assert sorted(status for status, _pid in outcomes) == ["CLAIMED", "REJECTED"]
        winner_pid = next(pid for status, pid in outcomes if status == "CLAIMED")
        current = parent.active_lease()
        assert current is not None and current.custody is not None
        assert current.custody.owner_pid == winner_pid
        assert current.token == ticket._lease.token
        with pytest.raises(OwnershipLost):
            parent.activate(ExecutionTicket(current))
        with pytest.raises(OwnershipLost):
            parent.claim_fleet_worker(ticket)
        with database.transaction() as connection:
            assert connection.execute("SELECT count(*) FROM locations").fetchone()[0] == 1
        assert [
            event.event_type
            for event in Journal(database).read_view().run_events("worker-run").events
        ] == [FleetRunEventType.RUN_ADMITTED]
        for outcome, (_process, _ready, _start, _result, release) in zip(
            outcomes, workers, strict=True
        ):
            if outcome[0] == "CLAIMED":
                release.send("settle")
        for process, _ready, _start, _result, _release in workers:
            process.join(15)
            assert not process.is_alive(), "claimant did not leave its bounded lifetime"
            assert process.exitcode == 0
        reopened = LocalDatabase(database.path)
        assert ExecutionCoordinator(reopened).active_lease() is None
        assert Journal(reopened).read_view().required_run_detail("worker-run").run.status is (
            FleetRunStatus.CANCELLED
        )
    finally:
        for process, ready, start, result, release in workers:
            if process.is_alive():
                process.terminate()
                process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            process.close()
            for channel in (ready, start, result, release):
                channel.close()
