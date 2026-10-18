"""M184: queue-admin ingress must not initialize or publish strict-placement authority."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oh_goon.api import dependencies
from oh_goon.api.errors import domain_exception_handler
from oh_goon.api.routes import fleet_executors
from oh_goon.core.errors import ExecutorQueueEntryNotFound, FleetDomainError, StateConflict
from oh_goon.core.models.fleet_executor import FleetExecutorIdentity
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
)
from oh_goon.runtime._executor_placement_queue import ExecutorPlacementQueue
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.executor_capacity import ExecutorCapacitySettings
from oh_goon.runtime.executor_pool import FleetExecutorPool
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _pool(tmp_path: Path) -> tuple[LocalDatabase, FleetExecutorPool, ExecutorPlacementQueue]:
    database = LocalDatabase(tmp_path / "admin-queue.db")
    candidate = ConfiguredFleetExecutor(
        priority=0,
        backend=SimpleNamespace(
            capabilities=SimpleNamespace(backend_id="backend-a"),
            configuration_identity=lambda: "config-a",
        ),
        capacity=ExecutorCapacitySettings(
            executor=FleetExecutorIdentity(executor_id="executor-a", generation=2),
            cpu_millis=1000,
            memory_mib=1024,
            max_processes=256,
        ),
    )
    configuration = ExecutorPoolConfiguration(
        placement_wait_seconds=0,
        placement_queue_limit=64,
        path_authority=TEST_PATH_AUTHORITY,
        executors=(candidate,),
    )
    with database.transaction(write=True, track_revision=False) as connection:
        connection.execute(
            "INSERT INTO executor_configured_generation(executor_id, generation) VALUES (?, ?)",
            ("executor-a", 1),
        )
    return database, FleetExecutorPool(database, configuration), ExecutorPlacementQueue(database)


def _generation(database: LocalDatabase) -> int:
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT generation FROM executor_configured_generation WHERE executor_id='executor-a'"
        ).fetchone()
        assert row is not None
        return int(row[0])


def test_rejected_cancel_has_no_executor_generation_or_queue_side_effect(tmp_path: Path) -> None:
    database, pool, queue = _pool(tmp_path)
    ticket = queue.enter("queued", max_entries=64)
    before = pool.placement_queue()
    assert _generation(database) == 1
    assert pool._placement_authority is None
    with pytest.raises(StateConflict, match="queue entry changed"):
        pool.cancel_queued_admission("queued", expected_sequence=ticket.sequence + 1)
    assert pool.placement_queue() == before
    assert _generation(database) == 1
    assert pool._placement_authority is None


def test_rejected_reprioritization_does_not_publish_generation(tmp_path: Path) -> None:
    database, pool, queue = _pool(tmp_path)
    queue.enter("queued", max_entries=64)
    before = pool.placement_queue()
    with pytest.raises(StateConflict, match="scheduling revision changed"):
        pool.reprioritize_queued_admission(
            "queued", admitted_priority=SchedulingPriority.HIGH,
            expected_revision=37, reason="stale admin caller",
        )
    assert pool.placement_queue() == before
    assert _generation(database) == 1
    assert pool._placement_authority is None


def test_unknown_admin_target_does_not_initialize_strict_placement(tmp_path: Path) -> None:
    database, pool, _ = _pool(tmp_path)
    with pytest.raises(ExecutorQueueEntryNotFound):
        pool.cancel_queued_admission("unknown", expected_sequence=1)
    assert pool.placement_queue().entries == ()
    assert pool._placement_authority is None
    assert _generation(database) == 1


def test_accepted_queue_admin_cas_uses_shared_queue_without_publishing_generation(
    tmp_path: Path,
) -> None:
    database, pool, queue = _pool(tmp_path)
    ticket = queue.enter("queued", max_entries=64)
    updated = pool.reprioritize_queued_admission(
        "queued", admitted_priority=SchedulingPriority.HIGH,
        expected_revision=0, reason="operator adjustment",
    )
    assert updated.sequence == ticket.sequence
    assert updated.scheduling_revision == 1
    assert updated.admitted_priority is SchedulingPriority.HIGH
    canceled = pool.cancel_queued_admission("queued", expected_sequence=ticket.sequence)
    assert canceled.state == "cancellation_requested"
    assert pool._placement_authority is None
    assert _generation(database) == 1
    assert pool._placement()._queue is pool._placement_queue()
    assert _generation(database) == 2  # Genuine strict placement still publishes generation.


def test_http_queue_admin_rejection_never_publishes_generation(tmp_path: Path) -> None:
    database, pool, queue = _pool(tmp_path)
    ticket = queue.enter("queued", max_entries=64)
    app = FastAPI()
    app.add_exception_handler(FleetDomainError, domain_exception_handler)
    app.include_router(fleet_executors.router, prefix="/api")
    app.dependency_overrides[dependencies.executor_pool] = lambda: pool
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/fleet/executors/placement-queue/queued/cancel",
        json={"expected_sequence": ticket.sequence + 1},
    )
    assert response.status_code == 409
    assert _generation(database) == 1
    assert pool.placement_queue().entries[0].state == "waiting"
    assert pool._placement_authority is None
