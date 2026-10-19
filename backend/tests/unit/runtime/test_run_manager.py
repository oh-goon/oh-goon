from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutionBusy, StateConflict
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.run import FleetRunStatus
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunSource, OperationAction
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.runtime.database import LocalDatabase
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.runtime_stack import fleet_stack


class BlockingReadyRunner:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        self.entered.set()
        assert self.release.wait(timeout=1)
        report = make_operation_report(repository, action, fleet_run_id=fleet_run_id)
        return OperationExecution(stdout="", stderr="", report=report)


class PartiallyFailingRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        if repository.name == "a":
            raise RuntimeError("intentional failure")
        report = make_operation_report(repository, action, fleet_run_id=fleet_run_id)
        return OperationExecution(stdout="", stderr="", report=report)


class CancellableRunner:
    def __init__(self) -> None:
        self.entered = threading.Event()

    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> OperationExecution:
        assert cancel_requested is not None
        self.entered.set()
        for _ in range(1_000):
            if cancel_requested():
                raise OperationCancelledError("cancelled")
            threading.Event().wait(0.001)
        raise RuntimeError("cancellation was never observed")


def make_repository(root: Path, name: str) -> None:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n")
    (repository / ".git").mkdir()


def test_generic_fleet_start_rejects_unprepared_plan_execution(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    make_repository(root, "a")
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(root)
    _, manager, _, _ = fleet_stack(state, PartiallyFailingRunner())

    with pytest.raises(StateConflict, match="FleetPlanService"):
        manager.start(OperationAction.EXECUTE_PLAN)


def test_manager_owns_one_async_fleet_run_and_returns_journal_detail(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    make_repository(root, "a")
    make_repository(root, "b")
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(root)
    runner = BlockingReadyRunner()
    fleet, manager, _, _ = fleet_stack(state, runner)

    started = manager.start(OperationAction.INIT)
    assert runner.entered.wait(timeout=1)
    with pytest.raises(ExecutionBusy, match="already"):
        manager.start(OperationAction.INIT)
    runner.release.set()

    detail = manager.wait_for_terminal(started.fleet_run_id, timeout=1)

    assert detail.run.status is FleetRunStatus.COMPLETED
    assert detail.run.progress.completed == 2
    assert len(detail.operations) == 2
    assert fleet.snapshot().summary.ready_for_update == 2


def test_parallel_failure_retains_every_successful_journal_result_in_run_detail(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    make_repository(root, "a")
    make_repository(root, "b")
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(root)
    fleet, manager, _, _ = fleet_stack(state, PartiallyFailingRunner())

    started = manager.start(OperationAction.INIT)
    detail = manager.wait_for_terminal(started.fleet_run_id, timeout=3)

    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.run.progress.completed == 1
    assert len(detail.operations) == 1
    assert detail.operations[0].repository.name == "b"
    assert detail.operations[0].provenance.fleet_run_id == started.fleet_run_id


def test_manager_cancels_running_fleet_and_releases_execution_lease(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    make_repository(root, "a")
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(root)
    runner = CancellableRunner()
    fleet, manager, _, execution = fleet_stack(state, runner)

    started = manager.start(OperationAction.INIT)
    assert runner.entered.wait(timeout=1)

    cancelling = manager.cancel(started.fleet_run_id)
    assert cancelling.status is FleetRunStatus.CANCELLING

    detail = manager.wait_for_terminal(started.fleet_run_id, timeout=1)
    assert detail.run.status is FleetRunStatus.CANCELLED
    assert manager.cancel(started.fleet_run_id).status is FleetRunStatus.CANCELLED
    assert detail.operations == ()
    event_types = [event.event_type for event in manager.events(started.fleet_run_id).events]
    assert event_types[-2:] == [
        FleetRunEventType.RUN_CANCEL_REQUESTED,
        FleetRunEventType.RUN_CANCELLED,
    ]
    replacement = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
    )[0]
    execution.activate(replacement).finish(lambda _connection: None)


def test_manager_rerun_uses_durable_run_authority_under_new_identity(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    make_repository(root, "b")
    database = LocalDatabase(tmp_path / "rerun.db")
    state = local_state(database)
    state.add_location(root)
    fleet, manager, _, _ = fleet_stack(state, PartiallyFailingRunner())

    original = manager.start(OperationAction.INIT, fleet_run_id="fleet-rerun-original")
    original_detail = manager.wait_for_terminal(original.fleet_run_id, timeout=2)

    rerun = manager.rerun(original.fleet_run_id, new_fleet_run_id="fleet-rerun-child")
    rerun_detail = manager.wait_for_terminal(rerun.fleet_run_id, timeout=2)

    assert rerun_detail.run.fleet_run_id == "fleet-rerun-child"
    assert rerun_detail.run.capability == original_detail.run.capability
    assert rerun_detail.run.target == original_detail.run.target
    assert rerun_detail.run.repository_scope == original_detail.run.repository_scope
    assert rerun_detail.run.source is FleetRunSource.MANUAL
    assert [target.repository_id for target in rerun_detail.run.targets] == [
        target.repository_id for target in original_detail.run.targets
    ]
    history_item = next(
        item for item in manager.history().items if item.run.fleet_run_id == rerun.fleet_run_id
    )
    assert history_item.execution == rerun_detail.execution
    assert history_item.execution.rerun_from_run_id == original.fleet_run_id

    with pytest.raises(StateConflict, match="own identity"):
        manager.rerun(original.fleet_run_id, new_fleet_run_id=original.fleet_run_id)


def test_manager_rerun_does_not_inherit_system_execution_source(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    make_repository(root, "b")
    database = LocalDatabase(tmp_path / "rerun-source.db")
    state = local_state(database)
    state.add_location(root)
    _, manager, _, _ = fleet_stack(state, PartiallyFailingRunner())

    original = manager.start(OperationAction.INIT, fleet_run_id="fleet-rerun-source")
    manager.wait_for_terminal(original.fleet_run_id, timeout=2)
    detail = manager._journal.read_view().required_run_detail(original.fleet_run_id)
    # Model a canonical system-owned parent without changing its execution authority.
    system_parent = detail.run.model_copy(update={"source": FleetRunSource.SCHEDULED})
    original_get = manager._journal.read_view

    class _View:
        def __init__(self, delegate):
            self._delegate = delegate

        def required_run_detail(self, run_id):
            current = self._delegate.required_run_detail(run_id)
            return current.model_copy(update={"run": system_parent})

        def run_detail(self, run_id):
            return self._delegate.run_detail(run_id)

    manager._journal.read_view = lambda: _View(original_get())  # type: ignore[method-assign]
    captured = {}
    manager._start_intent = lambda intent, **kwargs: captured.update(kwargs) or system_parent  # type: ignore[method-assign]

    manager.rerun(original.fleet_run_id, new_fleet_run_id="fleet-rerun-child")

    assert captured["source"] is FleetRunSource.MANUAL


def test_manager_rerun_replays_an_existing_matching_child_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    make_repository(root, "b")
    database = LocalDatabase(tmp_path / "rerun-existing-child.db")
    state = local_state(database)
    state.add_location(root)
    _, manager, _, _ = fleet_stack(state, PartiallyFailingRunner())

    original = manager.start(OperationAction.INIT, fleet_run_id="fleet-rerun-source")
    manager.wait_for_terminal(original.fleet_run_id, timeout=2)
    first = manager.rerun(original.fleet_run_id, new_fleet_run_id="fleet-rerun-child")
    manager.wait_for_terminal(first.fleet_run_id, timeout=2)

    replay = manager.rerun(original.fleet_run_id, new_fleet_run_id="fleet-rerun-child")
    events = manager.events("fleet-rerun-child").events

    assert replay.fleet_run_id == first.fleet_run_id == "fleet-rerun-child"
    assert sum(event.event_type is FleetRunEventType.RUN_ADMITTED for event in events) == 1


def test_manager_rerun_child_identity_is_bound_to_exact_parent(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    make_repository(root, "b")
    database = LocalDatabase(tmp_path / "rerun-parent-binding.db")
    state = local_state(database)
    state.add_location(root)
    _, manager, _, _ = fleet_stack(state, PartiallyFailingRunner())

    parent_a = manager.start(OperationAction.INIT, fleet_run_id="fleet-parent-a")
    manager.wait_for_terminal(parent_a.fleet_run_id, timeout=2)
    parent_b = manager.start(OperationAction.INIT, fleet_run_id="fleet-parent-b")
    manager.wait_for_terminal(parent_b.fleet_run_id, timeout=2)

    child = manager.rerun(parent_a.fleet_run_id, new_fleet_run_id="fleet-rerun-child")
    manager.wait_for_terminal(child.fleet_run_id, timeout=2)

    with pytest.raises(StateConflict, match="rerun child identity"):
        manager.rerun(parent_b.fleet_run_id, new_fleet_run_id="fleet-rerun-child")


def test_manager_rerun_rejects_an_existing_child_identity_with_different_authority(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    make_repository(root, "b")
    database = LocalDatabase(tmp_path / "rerun-existing-child-conflict.db")
    state = local_state(database)
    state.add_location(root)
    _, manager, _, _ = fleet_stack(state, PartiallyFailingRunner())

    original = manager.start(OperationAction.INIT, fleet_run_id="fleet-rerun-source")
    manager.wait_for_terminal(original.fleet_run_id, timeout=2)
    existing = manager.start(OperationAction.VERIFY, fleet_run_id="fleet-existing-run")
    manager.wait_for_terminal(existing.fleet_run_id, timeout=2)

    with pytest.raises(StateConflict, match="rerun child identity"):
        manager.rerun(original.fleet_run_id, new_fleet_run_id=existing.fleet_run_id)


def test_manager_rerun_rejects_non_terminal_run(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    make_repository(root, "a")
    database = LocalDatabase(tmp_path / "rerun-active.db")
    state = local_state(database)
    state.add_location(root)
    runner = BlockingReadyRunner()
    _, manager, _, _ = fleet_stack(state, runner)

    original = manager.start(OperationAction.INIT, fleet_run_id="fleet-rerun-active")
    assert runner.entered.wait(timeout=1)
    try:
        with pytest.raises(StateConflict, match="terminal"):
            manager.rerun(original.fleet_run_id, new_fleet_run_id="fleet-rerun-too-early")
    finally:
        runner.release.set()
        manager.wait_for_terminal(original.fleet_run_id, timeout=2)


def test_active_wait_progress_uses_process_local_owners_without_sqlite_reconnects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`plan run --wait` must not poll durable SQLite while the worker is still active.

    Certification preparation can spend minutes under heavy immutable-input I/O. Before
    1267.0.88 each one-second wait/progress slice reopened the journal several times,
    recreating the very reconnect pressure removed from the worker cancellation loop.
    """

    root = tmp_path / "repos"
    make_repository(root, "a")
    database = LocalDatabase(tmp_path / "wait-progress.db")
    state = local_state(database)
    state.add_location(root)
    runner = BlockingReadyRunner()
    _, manager, _, _ = fleet_stack(state, runner)

    started = manager.start(OperationAction.INIT, fleet_run_id="fleet-wait-progress")
    assert runner.entered.wait(timeout=1)

    original_connect = database._connect
    connects = 0

    def counted_connect():
        nonlocal connects
        connects += 1
        return original_connect()

    monkeypatch.setattr(database, "_connect", counted_connect)
    try:
        for _ in range(20):
            with pytest.raises(TimeoutError):
                manager.wait_for_terminal(started.fleet_run_id, timeout=0.001)
            manager.transient_run_activity(started.fleet_run_id)
            manager.game_tape_live_output(started.fleet_run_id)
        assert connects == 0
    finally:
        monkeypatch.setattr(database, "_connect", original_connect)
        runner.release.set()
        manager.wait_for_terminal(started.fleet_run_id, timeout=2)


def test_wait_for_terminal_surfaces_exact_run_scoped_background_failure(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "repos"
    make_repository(root, "a")
    database = LocalDatabase(tmp_path / "worker-failure.db")
    state = local_state(database)
    state.add_location(root)
    _, manager, _, _ = fleet_stack(state, PartiallyFailingRunner())

    def fail_before_lifecycle(_ticket) -> None:
        raise RuntimeError("injected worker publication failure")

    monkeypatch.setattr(manager._run_execution, "execute", fail_before_lifecycle)
    started = manager.start(OperationAction.INIT, fleet_run_id="fleet-worker-failure")

    with pytest.raises(
        StateConflict,
        match="background worker failed before durable terminal state.*injected worker publication failure",
    ):
        manager.wait_for_terminal(started.fleet_run_id, timeout=2)

    assert manager._supervisor.take_failure(started.fleet_run_id) is None
    manager.shutdown()
