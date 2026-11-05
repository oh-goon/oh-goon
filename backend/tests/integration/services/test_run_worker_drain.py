"""Native Run drain, admission linearization and bounded containment contracts."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Any, NoReturn

import pytest
import yaml

from oh_goon._application_foundation import RuntimeFoundation, build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon._application_worker import capture_run_worker_submission
from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import ExecutionBusy, RuntimeQuiescing
from oh_goon.core.models.recovery_status import RecoveryStatus
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.core.product_identity import repository_id
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunStatus,
    OperationAction,
)
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.runtime.database import DatabasePathIdentityLost
from oh_goon.runtime.execution import ExecutionTicket
from oh_goon.runtime.execution.run_supervisor import RunWorkerLaunch
from oh_goon.runtime.lifecycle import RuntimeLifecycleState
from tests.factories.capabilities import capability_snapshot

pytestmark = [
    pytest.mark.process,
    pytest.mark.skipif(
        not atomic_execution_bytes_supported(), reason="native protected Run transport"
    ),
]


@dataclass(frozen=True)
class _RunFixture:
    foundation: RuntimeFoundation
    runtime: ApplicationRuntime
    repository: Path
    target: FleetPlanTarget


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[_RunFixture, None, None]:
    for key, value in (
        ("OH_GOON_DB", tmp_path / "state" / "runtime.db"),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
        ("XDG_STATE_HOME", tmp_path / "state"),
    ):
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("OH_GOON_EXECUTORS", raising=False)
    repository = tmp_path / "repositories" / "subject"
    repository.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", str(repository)], check=True, capture_output=True, timeout=10
    )
    (repository / "work.py").write_text(
        "from pathlib import Path\nimport time\n"
        "Path('ready').touch()\ndeadline=time.monotonic()+20\n"
        "while not Path('release').exists():\n"
        " if time.monotonic()>deadline: raise TimeoutError('test release unavailable')\n"
        " time.sleep(0.01)\nPath('completed').touch()\n"
    )
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    goon = workflow / "goon.yml"
    goon.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "execution": {"purpose": "verification"},
                "steps": [{"id": "work", "run": f"{sys.executable} work.py"}],
            }
        )
    )
    foundation = build_runtime_foundation(standard_local=True)
    runtime = build_product_services(foundation)
    runtime.locations.add(repository.parent)
    runtime.plans.lock(goon)
    selected = _RunFixture(
        foundation,
        runtime,
        repository,
        runtime.plans.prepare_execution_target(goon, repository=repository),
    )
    try:
        yield selected
    finally:
        (repository / "release").touch()
        runtime.shutdown(wait_for_runs=False, cancel_grace_seconds=1, containment_timeout_seconds=5)


def _wait(predicate: Callable[[], bool], message: str) -> None:
    deadline = monotonic() + 15
    while not predicate():
        if monotonic() >= deadline:
            pytest.fail(message)
        Event().wait(0.01)


def _lifecycle_state(fixture: _RunFixture) -> RuntimeLifecycleState:
    return fixture.foundation.lifecycle.state


def _start(fixture: _RunFixture, run_id: str) -> None:
    fixture.runtime.runs.start_prepared_plan(
        fixture.target, repository_ids={repository_id(fixture.repository)}, fleet_run_id=run_id
    )


def _thread(operation: Callable[[], None]) -> tuple[Thread, Event, list[BaseException]]:
    done = Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            operation()
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    thread = Thread(target=run, daemon=True)
    thread.start()
    return thread, done, errors


@pytest.mark.parametrize("cleanup_proof", (True, False), ids=("settled", "unverified-then-retry"))
def test_default_rolling_shutdown_drains_healthy_work_and_fences_new_admission(
    fixture: _RunFixture,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_proof: bool,
) -> None:
    runtime = fixture.runtime
    runtime.start_background()
    _start(fixture, "healthy")
    _wait(lambda: (fixture.repository / "ready").exists(), "Step did not start")
    original_recovery = runtime.recovery.recover_worker_exit
    observed: list[str] = []

    def observe(run_id: str, *, quiescence_timeout_seconds: float = 0.0) -> RecoveryStatus:
        observed.append(run_id)
        assert runtime.runs.get(run_id).run.status is FleetRunStatus.COMPLETED
        if not cleanup_proof:
            raise ExecutionBusy("Declared native resource observation unavailable")
        return original_recovery(run_id, quiescence_timeout_seconds=quiescence_timeout_seconds)

    monkeypatch.setattr(runtime.recovery, "recover_worker_exit", observe)
    thread, done, errors = _thread(lambda: runtime.shutdown(drain_timeout_seconds=10))
    _wait(
        lambda: _lifecycle_state(fixture) is RuntimeLifecycleState.QUIESCING,
        "runtime did not quiesce",
    )
    try:
        with pytest.raises(RuntimeQuiescing):
            runtime.runs.start(OperationAction.INIT, fleet_run_id="too-late")
        with pytest.raises(RuntimeQuiescing):
            runtime.plans.run(
                fixture.repository / "not-opened-after-quiesce.yml",
                repository_ids={repository_id(fixture.repository)},
                fleet_run_id="no-plan-resolution",
            )
        assert fixture.foundation.state.journal.read_view().run_detail("too-late") is None
        assert not done.is_set()
        assert not any(
            event.event_type is FleetRunEventType.RUN_CANCEL_REQUESTED
            for event in runtime.runs.events("healthy").events
        )
    finally:
        (fixture.repository / "release").touch()
        thread.join(15)
    assert done.is_set() and observed == ["healthy"]
    if not cleanup_proof:
        assert len(errors) == 1 and isinstance(errors[0], ExecutionBusy)
        assert _lifecycle_state(fixture) is RuntimeLifecycleState.QUIESCING
        cleanup_proof = True
        runtime.shutdown(drain_timeout_seconds=0)
    else:
        assert not errors
    assert _lifecycle_state(fixture) is RuntimeLifecycleState.CLOSED
    assert runtime.runs.get("healthy").run.status is FleetRunStatus.COMPLETED
    assert (fixture.repository / "completed").exists()


def test_pre_quiesce_committed_admission_may_launch_after_quiesce(
    fixture: _RunFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    capture = fixture.runtime.runs._worker_configuration
    assert capture is not None

    def capture_held(run_id: str) -> Callable[[ExecutionTicket], RunWorkerLaunch]:
        prepare = capture(run_id)

        def held(ticket: ExecutionTicket) -> RunWorkerLaunch:
            entered.set()
            assert release.wait(10)
            return prepare(ticket)

        return held

    monkeypatch.setattr(fixture.runtime.runs, "_worker_configuration", capture_held)
    starter, started, start_errors = _thread(lambda: _start(fixture, "accepted"))
    assert entered.wait(10)
    assert fixture.runtime.runs.get("accepted").run.status is FleetRunStatus.QUEUED
    fixture.runtime.runs.quiesce()
    shutdown, closed, close_errors = _thread(
        lambda: fixture.runtime.shutdown(drain_timeout_seconds=10)
    )
    try:
        release.set()
        assert started.wait(10), start_errors
        _wait(lambda: (fixture.repository / "ready").exists(), "pre-quiesce Run failed to launch")
        (fixture.repository / "release").touch()
    finally:
        release.set()
        (fixture.repository / "release").touch()
        starter.join(15)
        shutdown.join(15)
    assert closed.is_set() and not start_errors and not close_errors
    assert fixture.runtime.runs.get("accepted").run.status is FleetRunStatus.COMPLETED
    assert not any(
        event.event_type is FleetRunEventType.RUN_CANCEL_REQUESTED
        for event in fixture.runtime.runs.events("accepted").events
    )


def test_quiesce_wins_before_final_admission_without_creating_a_run(
    fixture: _RunFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    original = fixture.runtime.runs._lifecycle.admit

    def held(*args: Any, **kwargs: Any) -> tuple[ExecutionTicket, FleetRunState]:
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(fixture.runtime.runs._lifecycle, "admit", held)
    thread, done, errors = _thread(lambda: _start(fixture, "rejected"))
    assert entered.wait(10)
    fixture.runtime.shutdown(drain_timeout_seconds=0)
    release.set()
    thread.join(10)
    assert done.is_set() and len(errors) == 1 and isinstance(errors[0], RuntimeQuiescing)
    assert fixture.foundation.state.journal.read_view().run_detail("rejected") is None
    assert fixture.foundation.execution.active_lease() is None
    assert fixture.foundation.supervisor.wait_until_idle(timeout=0)


@pytest.mark.parametrize("failure", ("rollback", "lost-ack"))
def test_admission_failure_or_ack_loss_converges_the_original_submission_slot(
    fixture: _RunFixture, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    if failure == "rollback":
        original = fixture.foundation.state.journal.admit_run_in

        def fail(*args: object, **kwargs: object) -> NoReturn:
            raise RuntimeError("admission writer rolled back")

        monkeypatch.setattr(fixture.foundation.state.journal, "admit_run_in", fail)
        with pytest.raises(RuntimeError, match="rolled back"):
            _start(fixture, "rollback")
        assert fixture.foundation.state.journal.read_view().run_detail("rollback") is None
        assert fixture.foundation.execution.active_lease() is None
        assert fixture.foundation.supervisor.wait_until_idle(timeout=0)
        monkeypatch.setattr(fixture.foundation.state.journal, "admit_run_in", original)
    else:
        original_transaction = fixture.foundation.database.transaction
        lost = False

        @contextmanager
        def lose_ack(
            *, write: bool = False, track_revision: bool = True
        ) -> Generator[sqlite3.Connection, None, None]:
            nonlocal lost
            with original_transaction(write=write, track_revision=track_revision) as connection:
                yield connection
            if write and track_revision and not lost:
                lost = True
                raise RuntimeError("admission response lost after commit")

        monkeypatch.setattr(fixture.foundation.database, "transaction", lose_ack)
    (fixture.repository / "release").touch()
    _start(fixture, "survivor")
    assert (
        fixture.runtime.runs.wait_for_terminal("survivor", timeout=15).run.status
        is FleetRunStatus.COMPLETED
    )
    assert fixture.foundation.supervisor.wait_until_idle(timeout=0)


def _stopped_claimed_worker(fixture: _RunFixture) -> int:
    foundation = fixture.foundation
    run_id = "stopped-worker"
    from oh_goon.core.models.repository import RepositoryIdentity

    target = RepositoryIdentity.model_validate({"path": fixture.repository})
    intent = FleetRunAdmissionIntent(
        capability=capability_snapshot(OperationAction.INIT),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    ticket, _ = foundation.execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=foundation.database.durable_revision(),
        mutate=lambda connection: foundation.state.journal.admit_run_in(
            connection, run_id, intent, fleet_repository_scope({target.repository_id}), [target]
        ),
    )
    prepare = capture_run_worker_submission(foundation, run_id)
    ready = fixture.repository / "claimed"

    def launch(selected: ExecutionTicket) -> RunWorkerLaunch:
        original = prepare(selected)
        program = (
            "import os,signal,sys\nfrom pathlib import Path\n"
            "from oh_goon._application_worker import _restore_handoff\n"
            "from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime\n"
            "from oh_goon.runtime.database import LocalDatabase\n"
            "from oh_goon.runtime.execution import ExecutionCoordinator,ExecutionTicket\n"
            "handoff=_restore_handoff(sys.argv[1])\n"
            "require_loaded_hashmarks_runtime(expected_implementation_identity=handoff.configuration.hashmarks_implementation_identity)\n"
            "database=LocalDatabase.open_worker(handoff.database)\n"
            "ExecutionCoordinator(database).claim_fleet_worker(ExecutionTicket.from_worker_handoff(handoff.ticket))\n"
            "Path(sys.argv[2]).write_text(str(os.getpid()))\nos.kill(os.getpid(),signal.SIGSTOP)\n"
        )
        return replace(
            original, argv=(sys.executable, "-I", "-c", program, original.argv[-1], str(ready))
        )

    fixture.runtime.runs._submit(run_id, ticket, launch)
    _wait(ready.exists, "claimed worker did not become ready")
    return int(ready.read_text())


def test_deadline_cancellation_grace_containment_then_canonical_reconciliation(
    fixture: _RunFixture,
) -> None:
    pid = _stopped_claimed_worker(fixture)
    fixture.runtime.shutdown(
        drain_timeout_seconds=0.01,
        cancel_grace_seconds=0.01,
        termination_grace_seconds=0.01,
        containment_timeout_seconds=5,
    )
    assert observe_process_birth(pid) is None
    assert _lifecycle_state(fixture) is RuntimeLifecycleState.CLOSED
    assert fixture.runtime.runs.get("stopped-worker").run.status is FleetRunStatus.CANCELLED
    events = fixture.runtime.runs.events("stopped-worker").events
    assert any(event.event_type is FleetRunEventType.RUN_CANCEL_REQUESTED for event in events)
    assert any(event.event_type is FleetRunEventType.RECONCILIATION_COMPLETED for event in events)
    assert fixture.foundation.execution.active_lease() is None


def test_failed_database_cancel_still_contains_and_retry_does_not_report_false_closure(
    fixture: _RunFixture,
) -> None:
    pid = _stopped_claimed_worker(fixture)
    database = fixture.foundation.database.path
    retained = database.with_name("retained-original.db")
    database.rename(retained)
    database.write_bytes(b"unrelated database identity")
    try:
        with pytest.raises(DatabasePathIdentityLost):
            fixture.runtime.shutdown(
                drain_timeout_seconds=0,
                cancel_grace_seconds=0,
                termination_grace_seconds=0.01,
                containment_timeout_seconds=5,
            )
        assert observe_process_birth(pid) is None
        assert _lifecycle_state(fixture) is RuntimeLifecycleState.QUIESCING
    finally:
        database.unlink()
        retained.rename(database)
    fixture.runtime.shutdown(
        drain_timeout_seconds=0, cancel_grace_seconds=0, containment_timeout_seconds=5
    )
    assert _lifecycle_state(fixture) is RuntimeLifecycleState.CLOSED
    detail = fixture.runtime.runs.get("stopped-worker")
    assert detail.run.status is FleetRunStatus.FAILED
    assert not any(
        event.event_type is FleetRunEventType.RUN_CANCEL_REQUESTED
        for event in fixture.runtime.runs.events("stopped-worker").events
    )


@pytest.mark.parametrize("join_budget", ("wait", "expired"))
def test_concurrent_shutdown_callers_wait_for_one_reconciliation_owner(
    fixture: _RunFixture, monkeypatch: pytest.MonkeyPatch, join_budget: str
) -> None:
    _stopped_claimed_worker(fixture)
    entered, release = Event(), Event()
    original = fixture.runtime.recovery.recover_worker_exit
    calls: list[str] = []

    def held(run_id: str, *, quiescence_timeout_seconds: float = 0.0) -> RecoveryStatus:
        calls.append(run_id)
        entered.set()
        assert release.wait(10)
        return original(run_id, quiescence_timeout_seconds=quiescence_timeout_seconds)

    monkeypatch.setattr(fixture.runtime.recovery, "recover_worker_exit", held)

    def operation() -> None:
        fixture.runtime.shutdown(
            drain_timeout_seconds=0,
            cancel_grace_seconds=0,
            termination_grace_seconds=0.01,
            containment_timeout_seconds=5,
        )

    first, first_done, first_errors = _thread(operation)
    assert entered.wait(10)

    def second_operation() -> None:
        if join_budget == "wait":
            operation()
        else:
            fixture.runtime.shutdown(
                drain_timeout_seconds=0,
                cancel_grace_seconds=0,
                termination_grace_seconds=0,
                containment_timeout_seconds=0.01,
            )

    second, second_done, second_errors = _thread(second_operation)
    try:
        if join_budget == "wait":
            assert not second_done.wait(0.1)
        else:
            assert second_done.wait(1)
            assert len(second_errors) == 1 and isinstance(second_errors[0], RuntimeQuiescing)
        assert _lifecycle_state(fixture) is RuntimeLifecycleState.QUIESCING
        with pytest.raises(RuntimeQuiescing):
            fixture.runtime.start_background()
    finally:
        release.set()
        first.join(15)
        second.join(15)
    assert first_done.is_set() and second_done.is_set() and not first_errors
    if join_budget == "wait":
        assert not second_errors
    assert calls == ["stopped-worker"]
    assert _lifecycle_state(fixture) is RuntimeLifecycleState.CLOSED


@pytest.mark.parametrize("budget", (-1.0, float("nan"), float("inf")))
def test_invalid_shutdown_budget_is_rejected_before_quiesce(
    fixture: _RunFixture, budget: float
) -> None:
    with pytest.raises(ValueError):
        fixture.runtime.shutdown(drain_timeout_seconds=budget)
    assert _lifecycle_state(fixture) is RuntimeLifecycleState.BUILT
    fixture.foundation.supervisor.require_accepting()
