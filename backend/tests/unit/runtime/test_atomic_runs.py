from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import (
    JournalConflict,
    RuntimeSafetyStale,
    ScheduleExecutionActive,
    ScheduleExecutionStale,
    StateConflict,
)
from oh_goon.core.product_identity import repository_attempt_operation_id, schedule_repository_fleet_run_id
from oh_goon.core.models.repository import (
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
    RepositoryIdentity,
)
from oh_goon.core.models.run_admission import (
    ExplicitRepositoryScope,
    FleetRepositoryScope,
    fleet_repository_scope,
)
from oh_goon.core.models.schedule import ScheduleDeleteRequest, ScheduleFailure, ScheduleReplace
from oh_goon.core.vocabulary import (
    EffectBoundaryKind,
    FleetRunEventType,
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    FleetScope,
    OperationAction,
    ScheduleExecutionStatus,
    ScheduleFailureStage,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, RunExecutionSupervisor
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.fleet_admission import FleetAdmissionPlan
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.schedules import ScheduleService
from tests.factories.capabilities import admit_test_run, capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.journal import start_run, terminalize_run
from tests.factories.repository_state import baseline_for
from tests.factories.schedules import persist_active_schedule, schedule_create, scheduled_plan


class _EmptyAdmission:
    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def plan(self, *args: object, **kwargs: object) -> FleetAdmissionPlan:
        del args, kwargs
        return FleetAdmissionPlan(durable_revision=self._database.durable_revision(), targets=(), repository_admissions=())


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
            durable_revision=self._database.durable_revision(), targets=selected, repository_admissions=()
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


def test_schedule_definition_admits_only_one_active_execution_under_concurrency(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "schedule-single-active.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create("schedule-single-active", schedule_create(name="Single active"))

    def begin(_: int) -> str:
        try:
            return persist_active_schedule(database, executions, definition).schedule_run_id
        except (ScheduleExecutionActive, RuntimeSafetyStale):
            return "busy"

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(begin, range(24)))

    admitted = [result for result in results if result != "busy"]
    assert len(admitted) == 1
    assert len(executions.active()) == 1
    assert executions.active()[0].schedule_run_id == admitted[0]


def test_fixed_schedule_run_identity_replays_existing_execution_and_rejects_rebinding(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "schedule-command-identity.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    first = definitions.create("schedule-first", schedule_create(name="First"))
    second = definitions.create("schedule-second", schedule_create(name="Second"))
    admitted = persist_active_schedule(database, executions, first)

    class _MustNotRun:
        def __getattr__(self, name: str):
            raise AssertionError(f"{name} must not run when caller identity is already durable")

    service = ScheduleService(
        database,
        definitions,
        executions,
        _MustNotRun(),  # type: ignore[arg-type]
        _MustNotRun(),  # type: ignore[arg-type]
        _MustNotRun(),  # type: ignore[arg-type]
    )

    replayed = service.start(
        first.schedule_id,
        admitted.schedule_run_id,
        expected_generation_id=first.generation_id,
        expected_record_version=first.record_version,
    )
    assert replayed.schedule_run_id == admitted.schedule_run_id
    assert replayed.schedule_id == first.schedule_id
    with pytest.raises(StateConflict, match="definition changed"):
        service.start(
            first.schedule_id,
            admitted.schedule_run_id,
            expected_generation_id=first.generation_id,
            expected_record_version=first.record_version + 1,
        )
    with pytest.raises(StateConflict, match="already bound to another Schedule"):
        service.start(
            second.schedule_id,
            admitted.schedule_run_id,
            expected_generation_id=second.generation_id,
            expected_record_version=second.record_version,
        )


def test_schedule_fleet_run_identity_is_pure_under_concurrent_reads(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "schedule-fleet-id.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create("schedule-atomic", schedule_create(name="Atomic"))
    execution = persist_active_schedule(database, executions, definition)

    repository_id = execution.intent.repository_scope.repository_ids[0]
    expected = schedule_repository_fleet_run_id(execution.schedule_run_id, repository_id)
    with ThreadPoolExecutor(max_workers=12) as pool:
        observed = list(
            pool.map(
                lambda _: schedule_repository_fleet_run_id(
                    execution.schedule_run_id, repository_id
                ),
                range(24),
            )
        )

    assert observed == [expected] * 24
    stored = executions.get(execution.schedule_run_id)
    assert stored.repository_runs[0].fleet_run_id is None
    assert stored.record_version == 0
    assert stored.status is ScheduleExecutionStatus.RUNNING
    assert "step_index" not in type(stored).model_fields


def test_schedule_compare_and_set_rejects_stale_writer(tmp_path: Path) -> None:
    from oh_goon.core.errors import ErrorKind
    from oh_goon.core.schedule_transitions import fail_schedule_run
    from oh_goon.core.time import utc_now

    database = LocalDatabase(tmp_path / "schedule-cas.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create("schedule-cas", schedule_create(name="CAS"))
    execution = persist_active_schedule(database, executions, definition)
    first = executions.get(execution.schedule_run_id)
    stale = executions.get(execution.schedule_run_id)
    failure = ScheduleFailure(
        code="TEST_FAILURE",
        kind=ErrorKind.INTERNAL,
        retryable=False,
        stage=ScheduleFailureStage.ADVANCE,
        message="test",
    )

    saved = executions.compare_and_set(
        first,
        fail_schedule_run(first, failure, finished_at=utc_now()),
    )
    assert saved.record_version == 1
    with pytest.raises(ScheduleExecutionStale):
        executions.compare_and_set(
            stale,
            fail_schedule_run(stale, failure, finished_at=utc_now()),
        )
    assert executions.get(execution.schedule_run_id).record_version == 1


def test_schedule_intent_is_immutable_and_active_definition_cannot_disappear(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "schedule-intent.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create("schedule-original", schedule_create(name="Original"))
    execution = persist_active_schedule(database, executions, definition)
    changed_plan = scheduled_plan("changed")

    changed = definitions.replace(
        definition.schedule_id,
        ScheduleReplace(
            name="Changed",
            repository_ids=definition.repository_ids,
            repository_instance_ids=definition.repository_instance_ids,
            target=definition.target.model_copy(
                update={
                    "plan": changed_plan,
                    "identity": structural_execution_identity(changed_plan),
                }
            ),
            interval_minutes=120,
            state=definition.state,
            generation_id=definition.generation_id,
            record_version=definition.record_version,
        ),
    )
    with pytest.raises(ScheduleExecutionActive):
        definitions.delete(
            definition.schedule_id,
            ScheduleDeleteRequest(
                generation_id=changed.generation_id,
                record_version=changed.record_version,
            ),
        )

    stored = executions.get(execution.schedule_run_id)
    assert stored.intent == execution.intent
    assert stored.intent.target.plan == definition.target.plan
    assert changed.target.plan != definition.target.plan
    assert changed.record_version == definition.record_version + 1


def test_fleet_run_admission_is_one_atomic_fact(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "admission.db"))
    repository = RepositoryIdentity(name="api", path=tmp_path / "api")

    state = admit_test_run(journal, "fleet-atomic", OperationAction.INIT, [repository])
    batch = journal.run_events("fleet-atomic")

    assert state.status is FleetRunStatus.QUEUED
    assert state.targets == (repository,)
    assert journal.read_view().revision == 1
    assert [event.event_type for event in batch.events] == [FleetRunEventType.RUN_ADMITTED]


def test_fixed_fleet_run_identity_is_idempotent_after_first_admission(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "fixed-run.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    manager = _fleet_manager(database, journal, execution, _EmptyAdmission(database))
    try:
        first = manager.start(OperationAction.INIT, fleet_run_id="fleet-fixed")
        detail = manager.wait_for_terminal("fleet-fixed", timeout=2)
        assert detail.run.status.terminal
        second = manager.start(OperationAction.INIT, fleet_run_id="fleet-fixed")
        events = manager.events("fleet-fixed").events
    finally:
        manager.shutdown(wait=True)

    assert first.fleet_run_id == second.fleet_run_id == "fleet-fixed"
    assert sum(event.event_type is FleetRunEventType.RUN_ADMITTED for event in events) == 1
    assert sum(event.event_type is FleetRunEventType.RUN_STARTED for event in events) == 1
    assert sum(event.event_type is FleetRunEventType.RUN_COMPLETED for event in events) == 1


class _RejectingSupervisor:
    def submit(self, fleet_run_id: str, work: object) -> None:
        del fleet_run_id, work
        raise RuntimeError("submission failed")

    def shutdown(self, *, wait: bool = False) -> None:
        del wait

    def snapshot(self, fleet_run_id: str) -> None:
        del fleet_run_id


class _InterruptingSupervisor:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def submit(self, fleet_run_id: str, work: object) -> None:
        del fleet_run_id, work
        raise self._error

    def shutdown(self, *, wait: bool = False) -> None:
        del wait

    def snapshot(self, fleet_run_id: str) -> None:
        del fleet_run_id


def test_fixed_fleet_run_identity_rejects_different_operation_intent(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "fixed-run-conflict.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    manager = _fleet_manager(database, journal, execution, _EmptyAdmission(database))
    try:
        manager.start(OperationAction.INIT, fleet_run_id="fleet-fixed-conflict")
        manager.shutdown(wait=True)
        with pytest.raises(JournalConflict, match="another operation intent"):
            manager.start(OperationAction.VERIFY, fleet_run_id="fleet-fixed-conflict")
    finally:
        manager.shutdown(wait=True)


def test_fixed_fleet_run_identity_rejects_different_explicit_repository_scope(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "fixed-run-scope-conflict.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    first_repository = IncludedRepositoryCatalogEntry(
        scope=FleetScope.INCLUDED, repository=RepositoryIdentity(path=tmp_path / "first")
    )
    second_repository = IncludedRepositoryCatalogEntry(
        scope=FleetScope.INCLUDED, repository=RepositoryIdentity(path=tmp_path / "second")
    )
    manager = _fleet_manager(
        database,
        journal,
        execution,
        _ScopeAdmission(database, (first_repository, second_repository)),
        supervisor=_RejectingSupervisor(),
    )
    first_id = first_repository.repository.repository_id
    second_id = second_repository.repository.repository_id

    with pytest.raises(RuntimeError, match="submission failed"):
        manager.start(
            OperationAction.INIT,
            repository_ids={first_id},
            fleet_run_id="fleet-fixed-scope",
        )

    with pytest.raises(JournalConflict, match="another repository scope"):
        manager.start(
            OperationAction.INIT,
            repository_ids={second_id},
            fleet_run_id="fleet-fixed-scope",
        )

    with pytest.raises(JournalConflict, match="another repository scope"):
        manager.start(
            OperationAction.INIT,
            repository_ids=None,
            fleet_run_id="fleet-fixed-scope",
        )


def test_submission_failure_terminalizes_once_and_releases_lease(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "submission-failure.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    manager = _fleet_manager(
        database,
        journal,
        execution,
        _EmptyAdmission(database),
        supervisor=_RejectingSupervisor(),
    )

    with pytest.raises(RuntimeError, match="submission failed"):
        manager.start(OperationAction.INIT, fleet_run_id="fleet-submit-failure")

    detail = manager.get("fleet-submit-failure")
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure is not None
    assert detail.run.failure.stage.value == "SUBMISSION"
    assert [event.event_type for event in manager.events("fleet-submit-failure").events] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_FAILED,
    ]
    replacement = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
    )[0]
    execution.activate(replacement).finish(lambda _connection: None)


@pytest.mark.parametrize(
    "error",
    [KeyboardInterrupt("submission interrupted"), SystemExit("submission exited")],
)
def test_submission_base_exception_terminalizes_and_releases_lease(
    tmp_path: Path, error: BaseException
) -> None:
    database = LocalDatabase(tmp_path / f"submission-{type(error).__name__}.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    manager = _fleet_manager(
        database,
        journal,
        execution,
        _EmptyAdmission(database),
        supervisor=_InterruptingSupervisor(error),
    )
    fleet_run_id = f"fleet-submit-{type(error).__name__.lower()}"

    with pytest.raises(type(error)):
        manager.start(OperationAction.INIT, fleet_run_id=fleet_run_id)

    detail = manager.get(fleet_run_id)
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure is not None
    assert detail.run.failure.stage.value == "SUBMISSION"
    assert [event.event_type for event in manager.events(fleet_run_id).events] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_FAILED,
    ]
    assert execution.active_lease() is None


def test_repository_effect_claim_has_one_durable_owner(tmp_path: Path) -> None:
    import sqlite3

    database = LocalDatabase(tmp_path / "repository-claim.db")
    attempts = RepositoryEffectAttemptStore(database)
    repository_path = tmp_path / "api"
    repository_path.mkdir()
    repository = RepositoryIdentity(name="api", path=repository_path)
    operation_id = repository_attempt_operation_id("fleet-repo", repository.repository_id)

    def claim(_: int) -> bool:
        try:
            with database.transaction(write=True) as connection:
                attempts.begin_fleet_in(
                    connection,
                    repository_path,
                    capability_snapshot(OperationAction.INIT),
                    fleet_run_id="fleet-repo",
                    operation_id=operation_id,
                    target=None,
                    baseline=baseline_for(repository),
                    effect_boundary=EffectBoundaryKind.EXTERNAL,
                )
            return True
        except sqlite3.IntegrityError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(claim, range(20)))

    assert sum(results) == 1
    stored = attempts.get(operation_id)
    assert stored.operation_id == operation_id
    assert stored.fleet_run_id == "fleet-repo"


def test_terminal_intents_are_serialized_to_one_terminal_fact(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "terminal-race.db"))
    admit_test_run(journal, "fleet-terminal", OperationAction.INIT, [])
    start_run(journal, "fleet-terminal")

    intents = [FleetRunTerminalIntent.COMPLETE, FleetRunTerminalIntent.CANCEL] * 10
    with ThreadPoolExecutor(max_workers=12) as pool:
        states = list(
            pool.map(lambda intent: terminalize_run(journal, "fleet-terminal", intent), intents)
        )

    final = journal.read_view().run_detail("fleet-terminal")
    assert final is not None and final.run.status.terminal
    assert {state.status for state in states} <= {
        FleetRunStatus.COMPLETED,
        FleetRunStatus.CANCELLED,
    }
    terminal_events = [
        event
        for event in journal.run_events("fleet-terminal").events
        if event.event_type
        in {
            FleetRunEventType.RUN_COMPLETED,
            FleetRunEventType.RUN_CANCELLED,
            FleetRunEventType.RUN_FAILED,
        }
    ]
    assert len(terminal_events) == 1


def test_cancel_request_and_lease_flag_commit_or_rollback_together(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "cancel-atomic.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    ticket = execution.reserve_fleet_with(
        "fleet-cancel-atomic",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    admit_test_run(journal, "fleet-cancel-atomic", OperationAction.INIT, [])
    start_run(journal, "fleet-cancel-atomic")

    session = execution.activate(ticket)

    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    original_request_cancel = journal.request_cancel_in

    def fail_cancel(connection, fleet_run_id):
        original_request_cancel(connection, fleet_run_id)
        raise RuntimeError("rollback")

    journal.request_cancel_in = fail_cancel  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="rollback"):
        lifecycle.cancel("fleet-cancel-atomic")
    journal.request_cancel_in = original_request_cancel  # type: ignore[method-assign]
    assert (
        journal.read_view().run_detail("fleet-cancel-atomic").run.status is FleetRunStatus.RUNNING
    )  # type: ignore[union-attr]
    assert session.cancel_requested() is False

    state = lifecycle.cancel("fleet-cancel-atomic")
    assert state.status is FleetRunStatus.CANCELLING
    assert session.cancel_requested() is True
    session.finish(lambda _connection: None)


def test_cancel_after_lease_loss_serializes_directly_to_cancelled(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "cancel-after-lease-loss.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    manager = _fleet_manager(database, journal, execution, _EmptyAdmission(database))
    admit_test_run(journal, "fleet-lost-lease", OperationAction.INIT, [])
    start_run(journal, "fleet-lost-lease")

    cancelled = manager.cancel("fleet-lost-lease")

    assert cancelled.status is FleetRunStatus.CANCELLED
    terminal_events = [
        event
        for event in manager.events("fleet-lost-lease").events
        if event.event_type
        in {
            FleetRunEventType.RUN_COMPLETED,
            FleetRunEventType.RUN_CANCELLED,
            FleetRunEventType.RUN_FAILED,
        }
    ]
    assert [event.event_type for event in terminal_events] == [FleetRunEventType.RUN_CANCELLED]


def test_orphan_cancel_reclaims_dead_lease_and_terminalizes_in_one_commit(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "cancel-dead-owner.db")
    journal = Journal(database)
    environment = _DeadLeaseEnvironment()
    execution = ExecutionCoordinator(database, lease_environment=environment)
    execution.reserve_fleet_with(
        "fleet-dead-owner",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )
    admit_test_run(journal, "fleet-dead-owner", OperationAction.INIT, [])
    start_run(journal, "fleet-dead-owner")
    environment.alive = False

    cancelled = FleetRunLifecycle(
        database, journal, execution, FleetExecutorPool(database, None)
    ).cancel("fleet-dead-owner")

    assert cancelled.status is FleetRunStatus.CANCELLED
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
    assert [event.event_type for event in journal.run_events("fleet-dead-owner").events] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_STARTED,
        FleetRunEventType.RUN_CANCEL_REQUESTED,
        FleetRunEventType.RUN_CANCELLED,
    ]


