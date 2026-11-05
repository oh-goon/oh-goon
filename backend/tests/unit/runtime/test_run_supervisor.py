from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Literal, cast

import pytest

from oh_goon.core.errors import ExecutionBusy, RuntimeQuiescing
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.vocabulary import BackgroundFailureStage
from oh_goon.runtime.execution import RunExecutionSupervisor, RunWorkerLaunch
from oh_goon.runtime.execution.run_supervisor import RunSupervisorState


class _Diagnostics:
    def __init__(self) -> None:
        self.events: list[DiagnosticEvent] = []

    def emit(self, event: DiagnosticEvent) -> None:
        self.events.append(event)


def _launch(
    directory: Path,
    source: str,
    *arguments: str,
    on_exit: Callable[[int | None], None] | None = None,
    cleanup: Callable[[], None] | None = None,
) -> RunWorkerLaunch:
    return RunWorkerLaunch(
        argv=(sys.executable, "-I", "-c", source, *arguments),
        cwd=directory,
        environment=dict(os.environ),
        on_exit=on_exit,
        cleanup=cleanup,
    )


def _blocked_worker(directory: Path) -> tuple[RunWorkerLaunch, Path, Event, list[int]]:
    release = directory / "release"
    ready = Event()
    child_pids: list[int] = []

    def observe(stream: Literal["stdout", "stderr"], text: str) -> None:
        if stream == "stdout" and text.strip():
            child_pids.append(int(text.strip()))
            ready.set()

    return (
        RunWorkerLaunch(
            argv=(
                sys.executable,
                "-I",
                "-c",
                "import os,sys,time\n"
                "from pathlib import Path\n"
                "print(os.getpid(), flush=True)\n"
                "deadline=time.monotonic()+10\n"
                "while not Path(sys.argv[1]).exists():\n"
                " if time.monotonic()>deadline: raise TimeoutError('test release unavailable')\n"
                " time.sleep(0.01)\n",
                str(release),
            ),
            cwd=directory,
            environment=dict(os.environ),
            stream_output=observe,
        ),
        release,
        ready,
        child_pids,
    )


@pytest.mark.process
def test_supervisor_runs_a_fresh_process_and_rejects_duplicate_or_second_active_run(
    tmp_path: Path,
) -> None:
    supervisor = RunExecutionSupervisor()
    launch, release, ready, child_pids = _blocked_worker(tmp_path)
    try:
        supervisor.submit("run-1", launch)
        assert ready.wait(5), "native child did not report readiness"
        assert len(child_pids) == 1
        assert child_pids[0] != os.getpid()
        assert supervisor.snapshot().active_runs == 1
        with pytest.raises(ExecutionBusy, match="already submitted"):
            supervisor.submit("run-1", launch)
        with pytest.raises(ExecutionBusy, match="active FleetRun"):
            supervisor.submit("run-2", launch)
    finally:
        release.touch()
        supervisor.shutdown(wait=True)
    assert supervisor.snapshot().active_runs == 0
    assert supervisor.snapshot().last_failure is None
    assert supervisor.take_failure("run-1") is None


@pytest.mark.process
def test_supervisor_waits_on_the_native_child_lifetime(tmp_path: Path) -> None:
    supervisor = RunExecutionSupervisor()
    launch, release, ready, _pids = _blocked_worker(tmp_path)
    try:
        supervisor.submit("run-wait", launch)
        assert ready.wait(5)
        assert supervisor.wait_until_inactive("run-wait", timeout=0.01) is False
        release.touch()
        assert supervisor.wait_until_inactive("run-wait", timeout=5) is True
    finally:
        release.touch()
        supervisor.shutdown(wait=True)


@pytest.mark.process
def test_quiescing_closes_admission_without_killing_the_active_worker(tmp_path: Path) -> None:
    supervisor = RunExecutionSupervisor()
    launch, release, ready, _pids = _blocked_worker(tmp_path)
    try:
        supervisor.submit("run-live", launch)
        assert ready.wait(5)
        supervisor.shutdown(wait=False)
        assert supervisor.state is RunSupervisorState.CLOSED
        assert supervisor.is_active("run-live")
        with pytest.raises(RuntimeQuiescing, match="quiescing"):
            supervisor.submit("run-later", launch)
        assert supervisor.wait_until_inactive("run-live", timeout=0.01) is False
        release.touch()
        assert supervisor.wait_until_inactive("run-live", timeout=5)
    finally:
        release.touch()
        supervisor.wait_until_inactive("run-live", timeout=12)
        supervisor.shutdown(wait=True)


def test_closed_supervisor_never_reopens_or_starts_another_process(tmp_path: Path) -> None:
    supervisor = RunExecutionSupervisor()
    supervisor.shutdown(wait=True)
    marker = tmp_path / "not-started"
    with pytest.raises(RuntimeQuiescing, match="quiescing"):
        supervisor.submit(
            "run-after-close",
            _launch(tmp_path, "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)),
        )
    assert not marker.exists()
    assert supervisor.state is RunSupervisorState.CLOSED


@pytest.mark.process
def test_abnormal_child_exit_does_not_stop_control_or_the_next_run(tmp_path: Path) -> None:
    supervisor = RunExecutionSupervisor()
    outcomes: list[int | None] = []
    try:
        supervisor.submit(
            "failed", _launch(tmp_path, "import os; os._exit(17)", on_exit=outcomes.append)
        )
        assert supervisor.wait_until_inactive("failed", timeout=5)
        failure = supervisor.take_failure("failed")
        assert failure is not None
        assert failure.stage is BackgroundFailureStage.FLEET_RUN_SUPERVISOR
        assert failure.code == "UNEXPECTED_BACKGROUND_FAILURE"
        assert supervisor.take_failure("failed") is None
        assert supervisor.take_failure("unrelated") is None
        assert outcomes == [17]
        marker = tmp_path / "next-run"
        supervisor.submit(
            "success",
            _launch(tmp_path, "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)),
        )
        assert supervisor.wait_until_inactive("success", timeout=5)
        assert marker.exists()
        assert supervisor.snapshot().last_failure is None
        assert supervisor.take_failure("success") is None
    finally:
        supervisor.shutdown(wait=True)


@pytest.mark.process
def test_diagnostics_never_publish_native_argv_or_captured_stderr(tmp_path: Path) -> None:
    diagnostics = _Diagnostics()
    supervisor = RunExecutionSupervisor(diagnostics=diagnostics)
    canary = "protected-worker-capability-canary"
    try:
        supervisor.submit(
            "run-diagnostic",
            _launch(
                tmp_path,
                "import sys; print(sys.argv[1], file=sys.stderr); raise SystemExit(3)",
                canary,
            ),
        )
        assert supervisor.wait_until_inactive("run-diagnostic", timeout=5)
        failure = supervisor.take_failure("run-diagnostic")
        assert failure is not None
        assert canary not in failure.model_dump_json()
        assert len(diagnostics.events) == 1
        event = diagnostics.events[0]
        assert event.fleet_run_id == "run-diagnostic"
        assert event.owner == "RunExecutionSupervisor"
        assert event.execution_affected is True
        assert canary not in event.model_dump_json()
    finally:
        supervisor.shutdown(wait=True)


@pytest.mark.process
def test_exit_observer_failure_never_publishes_protected_transport_material(tmp_path: Path) -> None:
    canary = "protected-settlement-capability-canary"

    def fail_observation(_returncode: int | None) -> None:
        raise RuntimeError(canary)

    supervisor = RunExecutionSupervisor()
    try:
        supervisor.submit("failed-observation", _launch(tmp_path, "pass", on_exit=fail_observation))
        assert supervisor.wait_until_inactive("failed-observation", timeout=5)
        failure = supervisor.take_failure("failed-observation")
        assert failure is not None
        assert canary not in failure.model_dump_json()
    finally:
        supervisor.shutdown(wait=True)


@pytest.mark.process
def test_cleanup_failure_is_reported_without_rewriting_child_result(tmp_path: Path) -> None:
    diagnostics = _Diagnostics()
    supervisor = RunExecutionSupervisor(diagnostics=diagnostics)
    cleaned: list[bool] = []
    outcomes: list[int | None] = []

    def cleanup() -> None:
        cleaned.append(True)
        raise RuntimeError("private cleanup details")

    try:
        supervisor.submit(
            "cleanup-failed", _launch(tmp_path, "pass", on_exit=outcomes.append, cleanup=cleanup)
        )
        assert supervisor.wait_until_inactive("cleanup-failed", timeout=5)
        assert outcomes == [0]
        assert cleaned == [True]
        assert supervisor.take_failure("cleanup-failed") is None
        assert len(diagnostics.events) == 1
        event = diagnostics.events[0]
        assert event.operation == "close_worker_transport"
        assert event.execution_affected is False
        assert event.severity == "WARNING"
        assert "private cleanup details" not in event.model_dump_json()
    finally:
        supervisor.shutdown(wait=True)


@pytest.mark.process
def test_failed_cleanup_diagnostic_cannot_prevent_worker_retirement(tmp_path: Path) -> None:
    class BrokenDiagnostics:
        def __init__(self) -> None:
            self.attempts = 0

        def emit(self, _event: DiagnosticEvent) -> None:
            self.attempts += 1
            raise RuntimeError("diagnostic sink unavailable")

    diagnostics = BrokenDiagnostics()
    supervisor = RunExecutionSupervisor(diagnostics=diagnostics)
    outcomes: list[int | None] = []
    marker = tmp_path / "completed"
    transport = (tmp_path / "owned-transport").open("wb")

    def cleanup() -> None:
        transport.close()
        raise RuntimeError("cleanup observation failed")

    try:
        supervisor.submit(
            "diagnostic-failed",
            _launch(
                tmp_path,
                "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()",
                str(marker),
                on_exit=outcomes.append,
                cleanup=cleanup,
            ),
        )
        assert supervisor.wait_until_inactive("diagnostic-failed", timeout=5)
        assert marker.exists()
        assert outcomes == [0]
        assert transport.closed
        assert diagnostics.attempts == 1
        assert supervisor.snapshot().active_runs == 0
        assert supervisor.take_failure("diagnostic-failed") is None
    finally:
        transport.close()
        supervisor.shutdown(wait=False)


def test_spawn_failure_retires_submission_and_closes_transport(tmp_path: Path) -> None:
    supervisor = RunExecutionSupervisor()
    outcomes: list[int | None] = []
    cleaned: list[bool] = []
    try:
        with pytest.raises(RuntimeError, match="could not be created"):
            supervisor.submit(
                "not-spawned",
                RunWorkerLaunch(
                    argv=(str(tmp_path / "missing-python"),),
                    cwd=tmp_path,
                    environment={},
                    on_exit=outcomes.append,
                    cleanup=lambda: cleaned.append(True),
                ),
            )
        assert supervisor.wait_until_inactive("not-spawned", timeout=5)
        assert outcomes == []
        assert cleaned == [True]
        assert supervisor.snapshot().active_runs == 0
        assert supervisor.take_failure("not-spawned") is not None
    finally:
        supervisor.shutdown(wait=True)


def test_supervisor_never_executes_submitted_callables_in_control_process() -> None:
    supervisor = RunExecutionSupervisor()
    invoked: list[bool] = []
    try:
        with pytest.raises(TypeError, match="prepared Run worker process"):
            supervisor.submit("no-fallback", cast(RunWorkerLaunch, lambda: invoked.append(True)))
        assert invoked == []
        assert supervisor.snapshot().active_runs == 0
    finally:
        supervisor.shutdown(wait=True)


def test_phase_one_rejects_multiple_active_runs() -> None:
    with pytest.raises(ValueError, match="one active FleetRun"):
        RunExecutionSupervisor(max_active_runs=2)
