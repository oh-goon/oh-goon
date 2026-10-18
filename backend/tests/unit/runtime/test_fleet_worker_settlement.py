from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

import oh_goon.services.fleet_run_execution as fleet_run_execution_module
from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.runtime.database import DatabasePathIdentityLost, LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import start_run


class _NoFleetTargets:
    def execute(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("target execution must not start")


class _DatabaseIdentityLosingTargets:
    def execute(self, *args: object, **kwargs: object) -> None:
        raise DatabasePathIdentityLost("primary fleet database identity lost")


def test_fleet_database_identity_loss_skips_durable_terminalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lifecycle must not use an untrusted DB to record failure about itself."""

    database = LocalDatabase(tmp_path / "fleet-db-identity-loss.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _ = lifecycle.admit(
        "fleet-db-identity-loss",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    finish_calls = 0
    original_finish = lifecycle.finish

    def counted_finish(*args, **kwargs):
        nonlocal finish_calls
        finish_calls += 1
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "finish", counted_finish)

    with pytest.raises(DatabasePathIdentityLost, match="primary fleet database identity lost"):
        FleetRunExecution(
            lifecycle,
            _DatabaseIdentityLosingTargets(),
            GameTapeLiveOutputStore(),
        ).execute(ticket)

    assert finish_calls == 0


def _lose_next_write_commit_response(database: LocalDatabase) -> None:
    original = database.transaction
    armed = True

    @contextmanager
    def ambiguous_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal armed
        with original(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and armed:
            armed = False
            raise RuntimeError("commit response lost after commit")

    database.transaction = ambiguous_transaction  # type: ignore[method-assign]


class _AmbiguousFleetFinishSession:
    def __init__(self, session, *, replacement=None) -> None:
        self._session = session
        self._replacement = replacement

    def require_fleet_run_id(self):
        return self._session.require_fleet_run_id()

    def mutate(self, mutate):
        return self._session.mutate(mutate)

    def finish(self, mutate):
        self._session.finish(self._replacement or mutate)
        raise RuntimeError("terminal response lost after commit")


class _AmbiguousFleetMutateSession:
    def __init__(self, session, *, after_commit=None, before_commit=False) -> None:
        self._session = session
        self._after_commit = after_commit
        self._before_commit = before_commit
        self._lost = False

    def __getattr__(self, name):
        return getattr(self._session, name)

    def mutate(self, mutate):
        if self._before_commit and not self._lost:
            self._lost = True
            raise RuntimeError("begin response failed before commit")
        result = self._session.mutate(mutate)
        if not self._lost:
            self._lost = True
            if self._after_commit is not None:
                self._after_commit()
            raise RuntimeError("begin response lost after commit")
        return result


class _SuccessfulFleetTargets:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *args: object, **kwargs: object) -> None:
        self.calls += 1


class _FailingLiveOutput:
    def __init__(self, *, fail_open: bool = False, fail_seal: bool = False, on_seal=None) -> None:
        self.fail_open = fail_open
        self.fail_seal = fail_seal
        self.on_seal = on_seal

    def open_run(self, _fleet_run_id: str) -> None:
        if self.fail_open:
            raise RuntimeError("live output open failed")

    def seal(self, _fleet_run_id: str) -> None:
        if self.on_seal is not None:
            self.on_seal()
        if self.fail_seal:
            raise RuntimeError("live output seal failed")


def test_live_output_open_failure_never_blocks_fleet_execution(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "fleet-live-open-failure.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _ = lifecycle.admit(
        "fleet-live-open-failure",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    targets = _SuccessfulFleetTargets()

    FleetRunExecution(
        lifecycle,
        targets,
        _FailingLiveOutput(fail_open=True),  # type: ignore[arg-type]
    ).execute(ticket)

    detail = journal.read_view().run_detail("fleet-live-open-failure")
    assert detail is not None and detail.run.status is FleetRunStatus.COMPLETED
    assert targets.calls == 1


def test_live_output_seal_failure_never_replaces_terminal_run(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "fleet-live-seal-failure.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _ = lifecycle.admit(
        "fleet-live-seal-failure",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )

    FleetRunExecution(
        lifecycle,
        _SuccessfulFleetTargets(),
        _FailingLiveOutput(fail_seal=True),  # type: ignore[arg-type]
    ).execute(ticket)

    detail = journal.read_view().run_detail("fleet-live-seal-failure")
    assert detail is not None and detail.run.status is FleetRunStatus.COMPLETED
    assert execution.active_lease() is None


def test_live_output_seals_before_durable_terminal_publication(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "fleet-live-seal-order.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _ = lifecycle.admit(
        "fleet-live-seal-order",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    observed: list[FleetRunStatus] = []

    def observe_before_terminal() -> None:
        detail = journal.read_view().run_detail("fleet-live-seal-order")
        assert detail is not None
        observed.append(detail.run.status)

    FleetRunExecution(
        lifecycle,
        _SuccessfulFleetTargets(),
        _FailingLiveOutput(on_seal=observe_before_terminal),  # type: ignore[arg-type]
    ).execute(ticket)

    assert observed == [FleetRunStatus.RUNNING]
    detail = journal.read_view().run_detail("fleet-live-seal-order")
    assert detail is not None and detail.run.status is FleetRunStatus.COMPLETED


def test_fleet_worker_begin_failure_terminalizes_and_releases_live_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = LocalDatabase(tmp_path / "fleet-begin-failure.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _state = lifecycle.admit(
        "fleet-begin-failure",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )

    monkeypatch.setattr(
        lifecycle,
        "begin",
        lambda _session: (_ for _ in ()).throw(RuntimeError("begin failed")),
    )

    FleetRunExecution(lifecycle, _NoFleetTargets(), GameTapeLiveOutputStore()).execute(
        ticket,
    )

    detail = journal.read_view().run_detail("fleet-begin-failure")
    assert detail is not None
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure is not None
    assert detail.run.failure.message == "begin failed"
    assert execution.active_lease() is None


def test_fleet_begin_replays_exact_running_state_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "fleet-begin-ambiguous.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _ = lifecycle.admit(
        "fleet-begin-ambiguous",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )

    running = lifecycle.begin(
        _AmbiguousFleetMutateSession(lifecycle.activate(ticket))  # type: ignore[arg-type]
    )

    assert running.status is FleetRunStatus.RUNNING
    assert [event.event_type for event in journal.run_events("fleet-begin-ambiguous").events] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_STARTED,
    ]


def test_fleet_begin_replay_rejects_later_cancellation_state(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "fleet-begin-superseded.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _ = lifecycle.admit(
        "fleet-begin-superseded",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    session = lifecycle.activate(ticket)

    with pytest.raises(RuntimeError, match="begin response lost after commit"):
        lifecycle.begin(
            _AmbiguousFleetMutateSession(
                session, after_commit=lambda: lifecycle.cancel("fleet-begin-superseded")
            )  # type: ignore[arg-type]
        )

    detail = journal.read_view().run_detail("fleet-begin-superseded")
    assert detail is not None and detail.run.status is FleetRunStatus.CANCELLING
    lifecycle.finish(session, FleetRunTerminalIntent.CANCEL)


def test_fleet_begin_does_not_replay_failure_before_transaction_body(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "fleet-begin-precommit.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _ = lifecycle.admit(
        "fleet-begin-precommit",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    session = lifecycle.activate(ticket)

    with pytest.raises(RuntimeError, match="before commit"):
        lifecycle.begin(
            _AmbiguousFleetMutateSession(session, before_commit=True)  # type: ignore[arg-type]
        )

    detail = journal.read_view().run_detail("fleet-begin-precommit")
    assert detail is not None and detail.run.status is FleetRunStatus.QUEUED
    lifecycle.finish(session, FleetRunTerminalIntent.CANCEL)


def test_fleet_terminal_settlement_replays_canonical_terminal_state_after_ambiguous_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "fleet-terminal-ambiguous.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _state = lifecycle.admit(
        "fleet-terminal-ambiguous",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    session = lifecycle.activate(ticket)
    lifecycle.begin(session)

    terminal = lifecycle.finish(
        _AmbiguousFleetFinishSession(session),  # type: ignore[arg-type]
        FleetRunTerminalIntent.COMPLETE,
    )

    assert terminal.status is FleetRunStatus.COMPLETED
    assert execution.active_lease() is None
    assert [
        event.event_type for event in journal.run_events("fleet-terminal-ambiguous").events
    ] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_STARTED,
        FleetRunEventType.RUN_COMPLETED,
    ]


def test_fleet_terminal_replay_rejects_different_terminal_fact(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "fleet-terminal-different.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _ = lifecycle.admit(
        "fleet-terminal-different",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    session = lifecycle.activate(ticket)
    lifecycle.begin(session)

    def cancel_instead(connection):
        return lifecycle._terminalize_in(
            connection, "fleet-terminal-different", FleetRunTerminalIntent.CANCEL, None
        )

    with pytest.raises(RuntimeError, match="terminal response lost after commit"):
        lifecycle.finish(
            _AmbiguousFleetFinishSession(session, replacement=cancel_instead),  # type: ignore[arg-type]
            FleetRunTerminalIntent.COMPLETE,
        )

    detail = journal.read_view().run_detail("fleet-terminal-different")
    assert detail is not None and detail.run.status is FleetRunStatus.CANCELLED
    assert execution.active_lease() is None


def test_fleet_cancel_replays_canonical_request_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "fleet-cancel-request-ambiguous.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    ticket, _state = lifecycle.admit(
        "fleet-cancel-request-ambiguous",
        resolve_capability_intent(OperationAction.INIT),
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    session = lifecycle.activate(ticket)
    lifecycle.begin(session)
    _lose_next_write_commit_response(database)

    cancelling = lifecycle.cancel("fleet-cancel-request-ambiguous")

    assert cancelling.status is FleetRunStatus.CANCELLING
    assert session.cancel_requested() is True
    lifecycle.finish(session, FleetRunTerminalIntent.CANCEL)


def test_fleet_orphan_cancel_replays_canonical_terminal_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "fleet-orphan-cancel-ambiguous.db")
    journal = Journal(database)
    lifecycle = FleetRunLifecycle(
        database, journal, ExecutionCoordinator(database), FleetExecutorPool(database, None)
    )
    admit_test_run(journal, "fleet-orphan-cancel-ambiguous", OperationAction.INIT, [])
    start_run(journal, "fleet-orphan-cancel-ambiguous")
    _lose_next_write_commit_response(database)

    cancelled = lifecycle.cancel("fleet-orphan-cancel-ambiguous")

    assert cancelled.status is FleetRunStatus.CANCELLED
    assert [
        event.event_type for event in journal.run_events("fleet-orphan-cancel-ambiguous").events
    ] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_STARTED,
        FleetRunEventType.RUN_CANCEL_REQUESTED,
        FleetRunEventType.RUN_CANCELLED,
    ]


def test_fleet_worker_fails_closed_if_current_capability_no_longer_matches_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "fleet-capability-drift.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    intent = resolve_capability_intent(OperationAction.INIT)
    ticket, _ = lifecycle.admit(
        "fleet-capability-drift",
        intent,
        fleet_repository_scope(None),
        [],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )
    targets = _NoFleetTargets()
    monkeypatch.setattr(
        fleet_run_execution_module,
        "resolve_bound_capability_intent",
        lambda _capability, _target: (_ for _ in ()).throw(
            RuntimeError("capability policy changed")
        ),
    )

    FleetRunExecution(lifecycle, targets, GameTapeLiveOutputStore()).execute(ticket)

    detail = journal.read_view().run_detail("fleet-capability-drift")
    assert detail is not None
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure is not None
    assert detail.run.failure.message == "capability policy changed"
    assert execution.active_lease() is None
