from __future__ import annotations

from pathlib import Path

from oh_goon.core.vocabulary import FleetRunEventType, FleetRunStatus, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_admission import FleetAdmissionPlan
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler
from tests.fakes.fleet_worker import SimulatedRunWorker


class _EmptyAdmission:
    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def plan(self, *args: object, **kwargs: object) -> FleetAdmissionPlan:
        del args, kwargs
        return FleetAdmissionPlan(
            durable_revision=self._database.durable_revision(), targets=(), repository_admissions=()
        )


class _UnusedExecutor:
    pass


def test_cancel_before_worker_begin_does_not_append_run_started(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "cancel-before-begin.db")
    journal = Journal(database)
    live_output = GameTapeLiveOutputStore()
    run_id = "fleet-cancel-before-begin"

    def worker_execution(
        worker_database: LocalDatabase, worker: ExecutionCoordinator
    ) -> FleetRunExecution:
        # The canonical claim has committed, but execution has not begun yet.
        cancelling = lifecycle.cancel(run_id)
        assert cancelling.status is FleetRunStatus.CANCELLING
        worker_lifecycle = FleetRunLifecycle(
            worker_database,
            Journal(worker_database),
            worker,
            FleetExecutorPool(worker_database, None),
        )
        return FleetRunExecution(
            worker_lifecycle,
            FleetTargetScheduler(_UnusedExecutor()),  # type: ignore[arg-type]
            live_output,
        )

    supervisor = SimulatedRunWorker(database, worker_execution)
    execution = ExecutionCoordinator(database, lease_environment=supervisor.parent_environment)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    manager = FleetRunControlPlane(
        _EmptyAdmission(database),  # type: ignore[arg-type]
        journal,
        lifecycle,
        FleetRunExecution(
            lifecycle,
            FleetTargetScheduler(_UnusedExecutor()),  # type: ignore[arg-type]
            live_output,
        ),
        supervisor,
        live_output,
        worker_configuration=supervisor.configuration,
    )
    try:
        manager.start(OperationAction.INIT, fleet_run_id=run_id)
        terminal = manager.wait_for_terminal(run_id, timeout=5).run
        assert terminal.status is FleetRunStatus.CANCELLED
        assert [event.event_type for event in journal.run_events(run_id).events] == [
            FleetRunEventType.RUN_ADMITTED,
            FleetRunEventType.RUN_CANCEL_REQUESTED,
            FleetRunEventType.RUN_CANCELLED,
        ]
        assert execution.active_lease() is None
    finally:
        manager.shutdown(wait=True)
