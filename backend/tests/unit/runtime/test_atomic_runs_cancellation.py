from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.models.repository import (
    RepositoryCatalogEntry,
)
from oh_goon.core.models.run_admission import (
    ExplicitRepositoryScope,
    FleetRepositoryScope,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator, RunExecutionSupervisor
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_admission import FleetAdmissionPlan
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler


class _EmptyAdmission:
    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def plan(self, *args: object, **kwargs: object) -> FleetAdmissionPlan:
        del args, kwargs
        return FleetAdmissionPlan(
            durable_revision=self._database.durable_revision(), targets=(), repository_admissions=()
        )


class _ScopeAdmission:
    def __init__(
        self, database: LocalDatabase, targets: tuple[RepositoryCatalogEntry, ...]
    ) -> None:
        self._database = database
        self._targets = targets

    def plan(
        self, *args: object, repository_scope: FleetRepositoryScope, **kwargs: object
    ) -> FleetAdmissionPlan:
        del args, kwargs
        repository_ids = (
            set(repository_scope.repository_ids)
            if isinstance(repository_scope, ExplicitRepositoryScope)
            else None
        )
        selected = (
            self._targets
            if repository_ids is None
            else tuple(
                item for item in self._targets if item.repository.repository_id in repository_ids
            )
        )
        return FleetAdmissionPlan(
            durable_revision=self._database.durable_revision(),
            targets=selected,
            repository_admissions=(),
        )


class _UnusedExecutor:
    pass


def _fleet_manager(
    database: LocalDatabase,
    journal: Journal,
    execution: ExecutionCoordinator,
    admission: object,
    *,
    supervisor: object | None = None,
) -> FleetRunControlPlane:
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    live_output = GameTapeLiveOutputStore()
    run_execution = FleetRunExecution(
        lifecycle,
        FleetTargetScheduler(_UnusedExecutor()),  # type: ignore[arg-type]
        live_output,
    )
    return FleetRunControlPlane(
        admission,  # type: ignore[arg-type]
        journal,
        lifecycle,
        run_execution,
        supervisor or RunExecutionSupervisor(),  # type: ignore[arg-type]
        live_output,
    )


class _DeadLeaseEnvironment:
    def __init__(self) -> None:
        self.pid = 4242
        self.alive = True

    def now(self):
        return datetime(2026, 8, 12, tzinfo=UTC)

    def process_id(self) -> int:
        return self.pid

    def process_alive(self, pid: int) -> bool:
        return self.alive and pid == self.pid

    def process_identity(self, pid: int) -> str | None:
        return f"birth-{pid}-1" if self.process_alive(pid) else None


def test_cancel_before_worker_begin_does_not_append_run_started(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "cancel-before-begin.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    scope = fleet_repository_scope(None)

    ticket, _state = lifecycle.admit(
        "fleet-cancel-before-begin",
        intent,
        scope,
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    cancelling = lifecycle.cancel("fleet-cancel-before-begin")
    assert cancelling.status is FleetRunStatus.CANCELLING

    session = lifecycle.activate(ticket)
    lifecycle.begin(session)
    terminal = lifecycle.finish(
        session,
        FleetRunTerminalIntent.CANCEL,
    )

    assert terminal.status is FleetRunStatus.CANCELLED
    assert [
        event.event_type for event in journal.run_events("fleet-cancel-before-begin").events
    ] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_CANCEL_REQUESTED,
        FleetRunEventType.RUN_CANCELLED,
    ]
    assert execution.active_lease() is None
