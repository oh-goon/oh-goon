from __future__ import annotations

from threading import Event

import pytest

from oh_goon.core.errors import ExecutionBusy, RuntimeQuiescing, StateConflict
from oh_goon.core.vocabulary import BackgroundFailureStage
from oh_goon.runtime.execution.run_supervisor import RunExecutionSupervisor


def test_supervisor_rejects_duplicate_live_submission_without_retaining_finished_history() -> None:
    supervisor = RunExecutionSupervisor()
    entered = Event()
    release = Event()
    finished = Event()

    def work() -> None:
        entered.set()
        release.wait(timeout=2)
        finished.set()

    supervisor.submit("run-1", work)
    assert entered.wait(timeout=2)

    with pytest.raises(ExecutionBusy, match="already submitted"):
        supervisor.submit("run-1", lambda: None)

    release.set()
    assert finished.wait(timeout=2)
    supervisor.shutdown(wait=True)
    snapshot = supervisor.snapshot()
    assert snapshot.active_runs == 0
    assert snapshot.last_failure is None
    assert not hasattr(supervisor, "_tasks")
    assert not hasattr(supervisor, "_statuses")
    assert not hasattr(supervisor, "_futures")
    assert not hasattr(supervisor, "_last_completion")
    assert not hasattr(supervisor, "take_completion")


def test_supervisor_waits_on_active_owner_without_polling() -> None:
    supervisor = RunExecutionSupervisor()
    entered = Event()
    release = Event()

    def work() -> None:
        entered.set()
        release.wait(timeout=2)

    supervisor.submit("run-wait", work)
    assert entered.wait(timeout=2)
    assert supervisor.wait_until_inactive("run-wait", timeout=0.01) is False

    release.set()
    assert supervisor.wait_until_inactive("run-wait", timeout=2) is True
    supervisor.shutdown(wait=True)


def test_supervisor_closed_lifecycle_never_reopens() -> None:
    supervisor = RunExecutionSupervisor()
    supervisor.shutdown(wait=True)

    with pytest.raises(RuntimeQuiescing, match="quiescing"):
        supervisor.submit("run-after-restart", lambda: None)


def test_supervisor_retains_only_one_typed_background_failure_diagnostic() -> None:
    supervisor = RunExecutionSupervisor()
    failed = Event()

    def fail() -> None:
        failed.set()
        raise StateConflict("worker failed")

    supervisor.submit("run-failed", fail)
    assert failed.wait(timeout=2)

    for _ in range(100):
        snapshot = supervisor.snapshot()
        if snapshot.active_runs == 0:
            break
        Event().wait(0.01)
    else:
        raise AssertionError("supervisor did not retire failed live submission")

    assert snapshot.last_failure is not None
    assert snapshot.last_failure.code == StateConflict.code
    assert snapshot.last_failure.stage is BackgroundFailureStage.FLEET_RUN_SUPERVISOR

    completed = Event()
    supervisor.submit("run-success", completed.set)
    assert completed.wait(timeout=2)
    supervisor.shutdown(wait=True)

    final = supervisor.snapshot()
    assert final.active_runs == 0
    assert final.last_failure is None
    assert not hasattr(supervisor, "_tasks")


def test_supervisor_emits_source_owned_diagnostic_without_changing_failure_semantics() -> None:
    class Diagnostics:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    diagnostics = Diagnostics()
    supervisor = RunExecutionSupervisor(diagnostics=diagnostics)
    failed = Event()

    def fail() -> None:
        failed.set()
        raise StateConflict("worker diagnostic failure")

    supervisor.submit("run-diagnostic", fail)
    assert failed.wait(timeout=2)
    assert supervisor.wait_until_inactive("run-diagnostic", timeout=2)
    snapshot = supervisor.snapshot()
    supervisor.shutdown(wait=True)

    assert snapshot.last_failure is not None
    assert snapshot.last_failure.code == StateConflict.code
    assert len(diagnostics.events) == 1
    event = diagnostics.events[0]
    assert event.fleet_run_id == "run-diagnostic"
    assert event.owner == "RunExecutionSupervisor"
    assert event.execution_affected is True


def test_supervisor_exposes_failed_run_diagnostic_once_by_run_identity() -> None:
    supervisor = RunExecutionSupervisor()
    failed = Event()

    def fail() -> None:
        failed.set()
        raise StateConflict("run-specific worker failure")

    supervisor.submit("run-scoped-failure", fail)
    assert failed.wait(timeout=2)
    assert supervisor.wait_until_inactive("run-scoped-failure", timeout=2)

    failure = supervisor.take_failure("run-scoped-failure")
    assert failure is not None
    assert failure.code == StateConflict.code
    assert failure.message == "run-specific worker failure"
    assert supervisor.take_failure("run-scoped-failure") is None
    supervisor.shutdown(wait=True)
