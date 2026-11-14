"""External cancellation reaches only the original launch and SQLite authority."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from oh_goon.core.errors import RunControlOwnerUnavailable, StateConflict
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.models.run_launch import ManagedApplicationLaunchReceipt
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.local_launch_owner import (
    BACKEND_GENERATION,
    BACKEND_SERVICE,
    LocalLaunchOwner,
    built_local_intent,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore
from oh_goon.runtime.run_launch_receipt import current_application_launch_receipt
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.factories.capabilities import capability_snapshot
from tests.factories.journal import admit_run


@pytest.fixture
def cancellation_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FleetRunLifecycle, LocalDatabase, ManagedApplicationLaunchReceipt]]:
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.db"))
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=tmp_path,
        asserted_by="cli-start",
        store=LocalLaunchEventStore.configured(),
    )
    context = owner.install_runtime_context()
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, os.getpid())
    backend = LocalLaunchOwner.load(
        owner.identity.launch_id, asserted_by="backend-runtime", store=owner.store
    )
    backend.startup_complete(BACKEND_SERVICE, BACKEND_GENERATION)
    owner.bound(BACKEND_SERVICE, BACKEND_GENERATION, host="127.0.0.1", port=8787)
    owner.ready(BACKEND_SERVICE, BACKEND_GENERATION)
    owner.launch_ready()
    try:
        receipt = current_application_launch_receipt()
        assert isinstance(receipt, ManagedApplicationLaunchReceipt)
        database = LocalDatabase()
        journal = Journal(database)
        admit_run(
            journal,
            "controlled-run",
            FleetRunAdmissionIntent(
                target=None,
                capability=capability_snapshot(OperationAction.INIT),
                execution=DirectFleetExecutionAdmission(),
                repository_admissions=(),
                application_launch=receipt,
            ),
            fleet_repository_scope(None),
            [],
        )
        lifecycle = FleetRunLifecycle(
            database, journal, ExecutionCoordinator(database), FleetExecutorPool(database, None)
        )
        yield lifecycle, database, receipt
    finally:
        context.clear()


def test_observation_only_capture_and_original_owner_cancel_are_idempotent(
    cancellation_owner: tuple[FleetRunLifecycle, LocalDatabase, ManagedApplicationLaunchReceipt],
) -> None:
    lifecycle, database, receipt = cancellation_owner
    observer_db = LocalDatabase(database.path, read_only=True)
    observer = FleetRunLifecycle(
        observer_db,
        Journal(observer_db),
        ExecutionCoordinator(observer_db),
        FleetExecutorPool(observer_db, None),
    )
    before = database.durable_revision()
    detail, state = observer.cancellation_observation("controlled-run")
    assert detail.application_launch == receipt
    assert state.path == database.path
    assert database.durable_revision() == before
    with observer_db.transaction() as connection:
        assert observer_db.capture_append_context(connection).path == database.path
        with pytest.raises(RuntimeError, match="atomic write cut"):
            observer_db.require_atomic_write_cut(connection)
        with sqlite3.connect(database.path) as foreign:
            foreign.execute("BEGIN")
            with pytest.raises(RuntimeError, match="Original LocalDatabase transaction"):
                observer_db.capture_append_context(foreign)
    with pytest.raises(RuntimeError, match="cannot acquire write authority"):
        with observer_db.transaction(write=True):
            pytest.fail("an observation context must not authorize a writer")
    with pytest.raises(RuntimeError, match="cannot admit a worker"):
        observer_db.capture_worker_context()
    first = lifecycle.cancel("controlled-run", expected_launch=receipt, expected_state=state)
    assert first.status is FleetRunStatus.CANCELLED
    committed = database.durable_revision()
    assert (
        lifecycle.cancel("controlled-run", expected_launch=receipt, expected_state=state) == first
    )
    assert database.durable_revision() == committed


@pytest.mark.parametrize(
    "field,value",
    [
        ("path", Path("/foreign/state.db")),
        ("device", 987654321),
        ("inode", 987654321),
        ("filesystem", "foreign-filesystem"),
        ("schema_cookie", 987654321),
        ("database_identity_cookie", 987654321),
        ("journal_mode", "opposite"),
        ("durable_revision", 987654321),
    ],
)
def test_foreign_state_refuses_before_any_cancel_event(
    cancellation_owner: tuple[FleetRunLifecycle, LocalDatabase, ManagedApplicationLaunchReceipt],
    field: str,
    value: object,
) -> None:
    lifecycle, database, receipt = cancellation_owner
    _, state = lifecycle.cancellation_observation("controlled-run")
    if field == "journal_mode":
        value = "DELETE" if state.journal_mode == "WAL" else "WAL"
    if getattr(state, field) == value:
        pytest.fail("authority mutation needs a distinct valid baseline")
    mutated = state.model_copy(update={field: value})
    before = database.durable_revision()
    with pytest.raises(RunControlOwnerUnavailable):
        lifecycle.cancel("controlled-run", expected_launch=receipt, expected_state=mutated)
    assert database.durable_revision() == before
    assert (
        lifecycle.cancellation_observation("controlled-run")[0].run.status is FleetRunStatus.QUEUED
    )
    assert (
        lifecycle.cancel("controlled-run", expected_launch=receipt, expected_state=state).status
        is FleetRunStatus.CANCELLED
    )


@pytest.mark.parametrize("already_cancelled", [False, True])
def test_stale_launch_never_becomes_success_via_cancelled_commit_replay(
    cancellation_owner: tuple[FleetRunLifecycle, LocalDatabase, ManagedApplicationLaunchReceipt],
    already_cancelled: bool,
) -> None:
    lifecycle, database, receipt = cancellation_owner
    _, state = lifecycle.cancellation_observation("controlled-run")
    if already_cancelled:
        lifecycle.cancel("controlled-run", expected_launch=receipt, expected_state=state)
    stale = receipt.model_copy(update={"owner_generation": receipt.owner_generation + 1})
    before = database.durable_revision()
    with pytest.raises(RunControlOwnerUnavailable):
        lifecycle.cancel("controlled-run", expected_launch=stale, expected_state=state)
    assert database.durable_revision() == before


@pytest.mark.parametrize("birth_changed", [False, True])
def test_recorded_launch_cannot_authorize_another_current_backend_process(
    cancellation_owner: tuple[FleetRunLifecycle, LocalDatabase, ManagedApplicationLaunchReceipt],
    monkeypatch: pytest.MonkeyPatch,
    birth_changed: bool,
) -> None:
    lifecycle, database, receipt = cancellation_owner
    _, state = lifecycle.cancellation_observation("controlled-run")
    monkeypatch.setattr(
        "oh_goon.runtime.run_launch_receipt.observe_process_birth",
        lambda _pid: ProcessBirthIdentity(
            pid=receipt.backend_pid,
            boot_id=receipt.backend_boot_id,
            start_ticks=receipt.backend_start_ticks + 1,
        )
        if birth_changed
        else None,
    )
    before = database.durable_revision()
    with pytest.raises(RunControlOwnerUnavailable):
        lifecycle.cancel("controlled-run", expected_launch=receipt, expected_state=state)
    assert database.durable_revision() == before


def test_recovery_only_custody_is_preserved_by_external_cancel(
    cancellation_owner: tuple[FleetRunLifecycle, LocalDatabase, ManagedApplicationLaunchReceipt],
) -> None:
    lifecycle, database, receipt = cancellation_owner
    _, state = lifecycle.cancellation_observation("controlled-run")
    # Model the existing protected recovery lease; cancellation must refuse before
    # it can reclaim or change that lease, irrespective of current native liveness.
    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO execution_lease (singleton, lease_token, kind, fleet_run_id, "
            "owner_session_id, owner_pid, owner_identity, acquired_at, custody_phase) "
            "VALUES (1, 'recovery-token', 'FLEET', 'controlled-run', 'recovery:original', "
            "?, 'original-birth', '2026-11-14T12:00:00+00:00', 'RECOVERY_OWNED')",
            (os.getpid(),),
        )
    before = database.durable_revision()
    with pytest.raises(StateConflict, match="recovery-only"):
        lifecycle.cancel("controlled-run", expected_launch=receipt, expected_state=state)
    assert database.durable_revision() == before
    with database.transaction() as connection:
        assert connection.execute(
            "SELECT owner_session_id, cancel_requested_at FROM execution_lease"
        ).fetchone()[:] == ("recovery:original", None)
