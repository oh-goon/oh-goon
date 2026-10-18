from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRunTransition
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.run_events import (
    RepositoryCompletedEvent,
    RepositoryStartedEvent,
    RunAdmittedEvent,
    RunCompletedEvent,
    RunStartedEvent,
    StorageMaintenanceRecordedEvent,
)
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceReceipt
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.run_projection import project_fleet_run_detail
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunEventType, OperationAction
from tests.factories.capabilities import capability_snapshot
from tests.factories.operation_reports import make_operation_report


def _receipt() -> FleetContentMaintenanceReceipt:
    return FleetContentMaintenanceReceipt(
        outcome="MAINTAINED",
        reason=None,
        before_bytes=900,
        after_bytes=700,
        before_objects=9,
        after_objects=7,
        removed_bytes=200,
        removed_objects=2,
        protected_objects=1,
        idle_protected_objects=0,
        budget_satisfied=True,
        removal_limit_reached=False,
        recovered_deletions=0,
        removed_orphan_lifecycle=0,
        max_retained_bytes=1024,
        max_retained_objects=10,
        min_idle_ns=100,
        max_removed_bytes=500,
        max_removed_objects=3,
    )


def _facts(tmp_path: Path):
    run_id = "fleet-storage"
    repository = RepositoryIdentity(name="api", path=tmp_path / "api")
    operation_id = repository_attempt_operation_id(run_id, repository.repository_id)
    report = make_operation_report(
        repository,
        OperationAction.INIT,
        fleet_run_id=run_id,
        operation_id=operation_id,
    )
    now = utc_now()
    admitted = RunAdmittedEvent(
        event_type=FleetRunEventType.RUN_ADMITTED,
        sequence=1,
        fleet_run_id=run_id,
        capability=capability_snapshot(OperationAction.INIT),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        execution=DirectFleetExecutionAdmission(),
        generated_at=now,
        repository_admissions=(),
    )
    started = RunStartedEvent(
        event_type=FleetRunEventType.RUN_STARTED,
        sequence=2,
        fleet_run_id=run_id,
        generated_at=now,
    )
    repository_started = RepositoryStartedEvent(
        event_type=FleetRunEventType.REPOSITORY_STARTED,
        sequence=3,
        fleet_run_id=run_id,
        repository_id=repository.repository_id,
        operation_id=operation_id,
        generated_at=now,
    )
    completed = RepositoryCompletedEvent(
        event_type=FleetRunEventType.REPOSITORY_COMPLETED,
        sequence=4,
        fleet_run_id=run_id,
        operation_id=operation_id,
        generated_at=now,
    )
    maintenance = StorageMaintenanceRecordedEvent(
        event_type=FleetRunEventType.STORAGE_MAINTENANCE_RECORDED,
        sequence=5,
        fleet_run_id=run_id,
        repository_id=repository.repository_id,
        operation_id=operation_id,
        receipt=_receipt(),
        generated_at=now,
    )
    terminal = RunCompletedEvent(
        event_type=FleetRunEventType.RUN_COMPLETED,
        sequence=6,
        fleet_run_id=run_id,
        generated_at=now,
    )
    return report, (admitted, started, repository_started, completed, maintenance, terminal), maintenance


def test_storage_maintenance_survives_run_projection_and_game_tape(tmp_path: Path) -> None:
    report, events, maintenance = _facts(tmp_path)
    detail = project_fleet_run_detail(events, lambda _: report)

    assert len(detail.storage_maintenance) == 1
    fact = detail.storage_maintenance[0]
    assert fact.repository_id == maintenance.repository_id
    assert fact.operation_id == maintenance.operation_id
    assert fact.receipt == maintenance.receipt
    assert detail.game_tape.storage.pressure == "UNDER_BUDGET"
    assert detail.game_tape.storage.latest_retained_bytes == 700


def test_storage_maintenance_must_follow_canonical_operation(tmp_path: Path) -> None:
    report, events, maintenance = _facts(tmp_path)
    out_of_order = maintenance.model_copy(update={"sequence": 4})
    completed = events[3].model_copy(update={"sequence": 5})

    with pytest.raises(InvalidRunTransition, match="must follow"):
        project_fleet_run_detail((events[0], events[1], events[2], out_of_order, completed), lambda _: report)


def test_storage_maintenance_rejects_duplicate_operation_fact(tmp_path: Path) -> None:
    report, events, maintenance = _facts(tmp_path)
    duplicate = maintenance.model_copy(update={"sequence": 6})
    terminal = events[-1].model_copy(update={"sequence": 7})

    with pytest.raises(InvalidRunTransition, match="already recorded"):
        project_fleet_run_detail((*events[:-1], duplicate, terminal), lambda _: report)
