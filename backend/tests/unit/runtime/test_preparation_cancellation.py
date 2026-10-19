from contextlib import contextmanager
from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.effect_attempt_transitions import begin_effect_attempt
from oh_goon.core.models.effect_origin import DirectEffectOrigin
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import EffectAttemptOwner, EffectBoundaryKind, OperationAction
from oh_goon.services.repository_effect_execution import RepositoryEffectExecution
from tests.factories.repository_state import baseline_for


class _NoResourcePrepared:
    execution_input_id = None

    def execute(self, _request):
        raise AssertionError("not executed")

    def close(self) -> None:
        return None


class _Fence:
    def __init__(self) -> None:
        self.callbacks = []

    def verify(self, _repository, _baseline, cancel_requested) -> None:
        self.callbacks.append(cancel_requested)


class _PreparationExecutor:
    def __init__(self) -> None:
        self.request: CapabilityExecutionRequest | None = None

    def prepare(self, request: CapabilityExecutionRequest) -> _NoResourcePrepared:
        self.request = request
        return _NoResourcePrepared()


class _PreparationSession:
    def __init__(self, exact_cancel, bounded_cancel) -> None:
        self.cancel_requested = exact_cancel
        self._bounded_cancel = bounded_cancel
        self.observer_entered = 0
        self.observer_exited = 0

    @contextmanager
    def preparation_cancellation_observer(self):
        self.observer_entered += 1
        try:
            yield self._bounded_cancel
        finally:
            self.observer_exited += 1


def _request(repository: Path, cancel_requested) -> CapabilityExecutionRequest:
    intent = resolve_capability_intent(OperationAction.PLAN)
    attempt = begin_effect_attempt(
        repository,
        intent.definition.durable_snapshot(),
        origin=DirectEffectOrigin(kind=EffectAttemptOwner.DIRECT),
        target=intent.target,
        baseline=baseline_for(repository),
        effect_boundary=EffectBoundaryKind.EXTERNAL,
        started_at=utc_now(),
        operation_id="op-preparation-cancel",
    )
    return CapabilityExecutionRequest(
        attempt=attempt,
        definition=intent.definition,
        cancel_requested=cancel_requested,
    )


def test_repository_preparation_uses_bounded_callback_only_for_hot_prepare_loop(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    exact_calls = 0

    def exact_cancel() -> bool:
        nonlocal exact_calls
        exact_calls += 1
        return False

    request = _request(repository, exact_cancel)

    def bounded():
        return False

    session = _PreparationSession(exact_cancel, bounded)
    executor = _PreparationExecutor()
    fence = _Fence()
    execution = object.__new__(RepositoryEffectExecution)
    execution._executor = executor  # type: ignore[attr-defined]
    execution._attempts = object()  # type: ignore[attr-defined]
    execution._effect_fence = fence  # type: ignore[attr-defined]

    prepared = execution._prepare(request, session=session)
    try:
        assert executor.request is not None
        assert executor.request.cancel_requested is bounded
        # The exact callback stays owned by the execution request; preparation owns resources only.
        assert request.cancel_requested is exact_cancel
        assert fence.callbacks == [exact_cancel, exact_cancel]
        assert session.observer_entered == 1
        assert session.observer_exited == 1
    finally:
        prepared.close()


def test_preparation_observer_reuses_one_native_handle_across_bounded_intervals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.runtime.execution.coordinator as coordinator
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.execution import ExecutionCoordinator

    database = LocalDatabase(tmp_path / "fresh-observer.db", journal_mode="DELETE")
    execution = ExecutionCoordinator(database)
    ticket = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    session = execution.activate(ticket)

    original_observer_open = database._open_admitted_observer_connection
    opens = 0

    def counted_observer_open():
        nonlocal opens
        opens += 1
        return original_observer_open()

    now = [500.0]
    monkeypatch.setattr(database, "_open_admitted_observer_connection", counted_observer_open)
    monkeypatch.setattr(coordinator, "monotonic", lambda: now[0])

    with session.preparation_cancellation_observer() as probe:
        # Context entry opens the one query-only handle and proves the exact owner.
        assert opens == 1
        assert probe() is False
        assert opens == 1
        # Hot-loop and due durable observations do not reconnect SQLite.
        for _ in range(50_000):
            assert probe() is False
        assert opens == 1
        for _ in range(25):
            now[0] += coordinator.PREPARATION_CANCELLATION_OBSERVATION_INTERVAL_SECONDS
            assert probe() is False
        assert opens == 1

    monkeypatch.setattr(database, "_open_admitted_observer_connection", original_observer_open)
    session.finish(lambda _connection: None)


def test_persistent_preparation_observer_sees_external_fleet_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.runtime.execution.coordinator as coordinator
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.execution import ExecutionCoordinator

    database = LocalDatabase(tmp_path / "observer-cancel-visible.db", journal_mode="DELETE")
    execution = ExecutionCoordinator(database)
    fleet_run_id = "fleet-preparation-observer"
    ticket = execution.reserve_fleet_with(
        fleet_run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    session = execution.activate(ticket)
    now = [800.0]
    monkeypatch.setattr(coordinator, "monotonic", lambda: now[0])

    with session.preparation_cancellation_observer() as probe:
        assert probe() is False
        with database.transaction(write=True) as connection:
            assert execution.request_fleet_cancel_in(connection, fleet_run_id) is True
        now[0] += coordinator.PREPARATION_CANCELLATION_OBSERVATION_INTERVAL_SECONDS
        assert probe() is True

    session.finish(lambda _connection: None)


def test_persistent_preparation_observer_survives_sustained_concurrent_sqlite_writer(
    tmp_path: Path,
) -> None:
    import threading

    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.execution import ExecutionCoordinator

    database = LocalDatabase(tmp_path / "observer-concurrent-writer.db", journal_mode="DELETE")
    execution = ExecutionCoordinator(database)
    fleet_run_id = "fleet-observer-concurrent-writer"
    ticket = execution.reserve_fleet_with(
        fleet_run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    session = execution.activate(ticket)
    errors: list[BaseException] = []

    def write_loop() -> None:
        try:
            for _ in range(400):
                with database.transaction(write=True, track_revision=False) as connection:
                    connection.execute(
                        "UPDATE state_metadata SET durable_revision = durable_revision "
                        "WHERE singleton = 1"
                    )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    with session._leases.preparation_cancellation_observer(session._lease) as observe:
        writer = threading.Thread(target=write_loop, name="sqlite-preparation-writer")
        writer.start()
        observations = 0
        while writer.is_alive() or observations < 400:
            assert observe() is False
            observations += 1
            # Production observations are cadence-bounded to 250 ms. A tiny yield here
            # preserves the same non-starving ownership shape without making this unit
            # regression hundreds of seconds long.
            threading.Event().wait(0.0005)
            if observations >= 4000 and writer.is_alive():
                raise AssertionError("concurrent SQLite writer did not make progress")
        writer.join(timeout=2)

    assert not writer.is_alive()
    assert errors == []
    session.finish(lambda _connection: None)


def test_preparation_observer_survives_separate_database_owner_write_pressure(
    tmp_path: Path,
) -> None:
    """Production shape: observer and writer own independent LocalDatabase instances."""
    import threading

    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.execution import ExecutionCoordinator

    path = tmp_path / "cross-owner-preparation.db"
    observer_database = LocalDatabase(path, journal_mode="DELETE")
    writer_database = LocalDatabase(path, journal_mode="DELETE")
    execution = ExecutionCoordinator(observer_database)
    fleet_run_id = "fleet-cross-owner-preparation"
    ticket = execution.reserve_fleet_with(
        fleet_run_id,
        expected_durable_revision=observer_database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    session = execution.activate(ticket)
    errors: list[BaseException] = []

    def write_loop() -> None:
        try:
            for _ in range(800):
                with writer_database.transaction(write=True, track_revision=False) as connection:
                    connection.execute(
                        "UPDATE state_metadata SET durable_revision = durable_revision "
                        "WHERE singleton = 1"
                    )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    with session._leases.preparation_cancellation_observer(session._lease) as observe:
        writer = threading.Thread(target=write_loop, name="sqlite-separate-owner-writer")
        writer.start()
        observations = 0
        while writer.is_alive() or observations < 800:
            assert observe() is False
            observations += 1
            threading.Event().wait(0.0005)
            if observations >= 8000 and writer.is_alive():
                raise AssertionError("separate SQLite writer did not make progress")
        writer.join(timeout=2)

    assert not writer.is_alive()
    assert errors == []
    session.finish(lambda _connection: None)
