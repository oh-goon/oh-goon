from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest

from oh_goon.core.errors import (
    OperationNotFound,
    RepositoryEffectQuarantined,
    RuntimeQuiescing,
    StateConflict,
)
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.vocabulary import (
    CommandSafety,
    EffectAttemptStatus,
    EffectBoundaryKind,
    OperationAction,
    ReconciliationOutcome,
)
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.execution.run_supervisor import RunExecutionSupervisor, RunSupervisorState
from oh_goon.runtime.journal import Journal
from oh_goon.services.effect_quarantine import (
    effect_quarantines,
    require_effects_allowed_for_repositories,
)
from tests.factories.capabilities import capability_snapshot
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_effect_runtime import effect_recovery
from tests.factories.repository_state import baseline_for
from tests.factories.runtime_stack import direct_operation_stack
from tests.fakes.capability_runners import ExternalRunnerStub


class _CapturingAdapter(ExternalRunnerStub):
    def __init__(self) -> None:
        self.operation_ids: list[str | None] = []

    def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
        self.operation_ids.append(request.attempt.operation_id)
        return OperationExecution(
            stdout="",
            stderr="",
            report=make_operation_report(
                request.attempt.repository,
                request.attempt.action,
                fleet_run_id=None,
                operation_id=request.attempt.operation_id,
            ),
        )


def _begin_direct(
    database: LocalDatabase, attempts: RepositoryEffectAttemptStore, repository: Path
):
    with database.transaction(write=True) as connection:
        return attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )


def test_supervisor_quiesce_preserves_already_queued_work() -> None:
    supervisor = RunExecutionSupervisor(max_active_runs=1)
    first_entered = Event()
    release_first = Event()
    second_finished = Event()

    def first() -> None:
        first_entered.set()
        release_first.wait(timeout=2)

    supervisor.submit("run-1", first)
    assert first_entered.wait(timeout=2)
    supervisor.submit("run-2", second_finished.set)
    supervisor.quiesce()

    with pytest.raises(RuntimeQuiescing):
        supervisor.submit("run-3", lambda: None)

    release_first.set()
    supervisor.shutdown(wait=True)
    assert second_finished.is_set()
    assert supervisor.state is RunSupervisorState.CLOSED


def test_unknown_mutating_effect_recovery_quarantines_repository(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = tmp_path / "repo"
    repository.mkdir()
    attempt = _begin_direct(database, attempts, repository)
    with database.transaction(write=True) as connection:
        attempts.start_effect_in(connection, attempt.operation_id)
        attempts.mark_unknown_in(connection, attempt.operation_id, note="process ended")
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")

    effect_recovery(
        database,
        attempts,
        journal,
        ExecutionCoordinator(database),
        GitService(),
    ).recover()

    recovered = attempts.get(attempt.operation_id)
    assert recovered.status is EffectAttemptStatus.PARTIALLY_APPLIED
    quarantines = effect_quarantines(attempts.all())
    with pytest.raises(RepositoryEffectQuarantined):
        require_effects_allowed_for_repositories(
            CommandSafety.WORKTREE_MUTATION,
            [recovered.repository.repository_id],
            quarantines,
        )
    require_effects_allowed_for_repositories(
        CommandSafety.READ_ONLY,
        [recovered.repository.repository_id],
        quarantines,
    )


def test_committed_report_retires_coordination_state_instead_of_storing_completed_attempt(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    adapter = _CapturingAdapter()
    operations, attempts, _, _ = direct_operation_stack(database, journal, adapter)
    repository = tmp_path / "repo"
    repository.mkdir()

    result = operations.run(repository, OperationAction.UPDATE)
    operation_id = result.report.provenance.operation_id

    assert adapter.operation_ids == [operation_id]
    assert journal.require_operation(operation_id) == result.report
    with pytest.raises(OperationNotFound):
        attempts.get(operation_id)
    assert attempts.all() == []


def test_supervisor_shutdown_does_not_cancel_pending_future() -> None:
    supervisor = RunExecutionSupervisor(max_active_runs=1)
    entered = Event()
    release = Event()
    pending_ran = Event()

    supervisor.submit("active", lambda: (entered.set(), release.wait(timeout=2)))
    assert entered.wait(timeout=2)
    supervisor.submit("pending", pending_ran.set)
    supervisor.quiesce()
    release.set()
    supervisor.shutdown(wait=True)

    assert pending_ran.is_set()


class _CancellableRunner:
    def __init__(self) -> None:
        self.entered = Event()

    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested=None,
    ) -> OperationExecution:
        assert cancel_requested is not None
        self.entered.set()
        for _ in range(1_000):
            if cancel_requested():
                from oh_goon.engine.errors import OperationCancelledError

                raise OperationCancelledError("shutdown cancellation observed")
            Event().wait(0.001)
        raise RuntimeError("shutdown cancellation was not observed")


def _make_repository(root: Path, name: str) -> None:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n")
    (repository / ".git").mkdir()


def test_quiesced_manager_cancels_active_run_and_releases_lease(tmp_path: Path) -> None:
    from oh_goon.core.models.run import FleetRunStatus
    from tests.factories.runtime_stack import fleet_stack

    root = tmp_path / "repos"
    _make_repository(root, "a")
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(root)
    runner = _CancellableRunner()
    _, manager, _, execution = fleet_stack(state, runner)

    started = manager.start(OperationAction.INIT)
    assert runner.entered.wait(timeout=1)

    manager.quiesce()
    manager.cancel_active_for_shutdown()
    manager.shutdown(wait=True)

    assert manager.get(started.fleet_run_id).run.status is FleetRunStatus.CANCELLED
    replacement = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    execution.activate(replacement).finish(lambda _connection: None)


def test_every_capability_explicitly_declares_effect_safety() -> None:
    from oh_goon.core.capability_registry import capability_definitions

    assert capability_definitions()
    assert all(item.safety in set(CommandSafety) for item in capability_definitions())


class _FailingAdapter(ExternalRunnerStub):
    def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
        raise RuntimeError("adapter failed after attempt admission")


def test_direct_executor_failure_preserves_unknown_attempt_and_blocks_replacement(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    operations, attempts, _, _ = direct_operation_stack(database, journal, _FailingAdapter())
    repository = tmp_path / "repo"
    repository.mkdir()

    with pytest.raises(RuntimeError, match="adapter failed"):
        operations.run(repository, OperationAction.UPDATE)

    unresolved = effect_quarantines(attempts.all())
    assert len(unresolved) == 1
    assert unresolved[0].capability.action is OperationAction.UPDATE
    with pytest.raises(RepositoryEffectQuarantined):
        operations.run(repository, OperationAction.UPDATE)


def test_runtime_lifecycle_is_one_way() -> None:
    from oh_goon.runtime.lifecycle import RuntimeLifecycle, RuntimeLifecycleState

    lifecycle = RuntimeLifecycle()
    assert lifecycle.state is RuntimeLifecycleState.BUILT
    assert lifecycle.mark_running() is RuntimeLifecycleState.RUNNING
    assert lifecycle.begin_quiesce() is True
    assert lifecycle.state is RuntimeLifecycleState.QUIESCING
    lifecycle.close()
    assert lifecycle.state is RuntimeLifecycleState.CLOSED
    assert lifecycle.begin_quiesce() is False
    assert lifecycle.mark_running() is RuntimeLifecycleState.CLOSED


def test_effect_attempt_reconciliation_is_idempotent_and_acknowledgement_requires_evidence(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "repo"
    repository.mkdir()
    attempt = _begin_direct(database, attempts, repository)
    with database.transaction(write=True) as connection:
        attempts.start_effect_in(connection, attempt.operation_id)
        unknown = attempts.mark_unknown_in(connection, attempt.operation_id, note="crash")
    with database.transaction(write=True) as connection:
        assert attempts.mark_unknown_in(connection, attempt.operation_id, note="ignored") == unknown
    recovery = effect_recovery(
        database,
        attempts,
        Journal(database),
        ExecutionCoordinator(database),
        GitService(),
    )
    with pytest.raises(StateConflict):
        recovery.acknowledge(attempt.operation_id, note="must reconcile first")
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
    reconciled = recovery.reconcile(attempt.operation_id)
    assert reconciled.reconciliation.outcome is ReconciliationOutcome.PARTIALLY_APPLIED
    acknowledged = recovery.acknowledge(attempt.operation_id, note="inspected")
    assert acknowledged.note == "inspected"
    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)
    with pytest.raises(OperationNotFound):
        attempts.get("op-missing")


def test_application_background_work_cannot_restart_after_quiesce() -> None:
    from oh_goon.application import ApplicationRuntime
    from oh_goon.runtime.lifecycle import RuntimeLifecycle

    class SchedulerStub:
        def __init__(self) -> None:
            self.starts = 0

        def start(self) -> None:
            self.starts += 1

    runtime = object.__new__(ApplicationRuntime)
    object.__setattr__(runtime, "_lifecycle", RuntimeLifecycle())
    scheduler = SchedulerStub()
    object.__setattr__(runtime, "_schedule_scheduler", scheduler)

    runtime.start_background()
    assert scheduler.starts == 1
    assert runtime._lifecycle.begin_quiesce() is True
    with pytest.raises(RuntimeQuiescing):
        runtime.start_background()
    assert scheduler.starts == 1


def test_cli_direct_quarantine_is_a_clean_domain_failure(tmp_path: Path, capsys) -> None:
    from types import SimpleNamespace

    from oh_goon.cli_runtime import run_stateful_command

    class OperationStub:
        def run(
            self, repository: Path, action: OperationAction, *, operation_id: str | None = None
        ):
            del repository, action, operation_id
            raise RepositoryEffectQuarantined("inspect unresolved attempt before retry")

    runtime = SimpleNamespace(operations=OperationStub())
    args = SimpleNamespace(
        command="operation",
        repository=tmp_path / "repo",
        action=OperationAction.UPDATE.value,
        as_json=False,
    )
    assert run_stateful_command(runtime, args) == 2
    assert "inspect unresolved attempt before retry" in capsys.readouterr().out


def test_database_write_transaction_rolls_back_baseexception_without_revision_change(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "baseexception-rollback.db")
    before = database.durable_revision()

    with pytest.raises(KeyboardInterrupt):
        with database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
                (str(tmp_path / "interrupted"), 0, "2026-01-01T00:00:00+00:00"),
            )
            raise KeyboardInterrupt()

    assert database.durable_revision() == before
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0
