"""Generic operation commands retain their original native process scope without Steps."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from oh_goon._application_foundation import build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon.core.models.run_events import (
    ProcessExitedEvent,
    ProcessStartedEvent,
    ProcessStartingEvent,
    ProcessStateUnknownEvent,
)
from oh_goon.core.process_birth import observe_process_birth, observe_process_liveness
from oh_goon.core.product_identity import repository_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.engine.git import GitService
from oh_goon.runtime.execution import ExecutionTicket
from oh_goon.runtime.execution.run_supervisor import RunWorkerLaunch
from oh_goon.runtime.process_group import wait_for_process_session_quiescence

pytestmark = [
    pytest.mark.process,
    pytest.mark.skipif(
        not atomic_execution_bytes_supported(), reason="native protected Run transport"
    ),
]


@pytest.mark.parametrize("mode", ("normal", "forced"))
def test_generic_native_commands_have_operation_scope_and_truthful_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    for name, value in (
        ("OH_GOON_DB", tmp_path / "state" / "runtime.db"),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
        ("XDG_STATE_HOME", tmp_path / "state"),
    ):
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("OH_GOON_EXECUTORS", raising=False)
    repository = tmp_path / "repositories" / "subject"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    (repository / "pyproject.toml").write_text("[project]\nname='subject'\nversion='0.1.0'\n")
    control = tmp_path / "control"
    control.mkdir()
    block, ready = control / "block", control / "ready"
    wrapper = control / "git-wrapper"
    native_git = shutil.which("git")
    assert native_git is not None
    wrapper.write_text(
        f"#!{sys.executable}\nimport os,signal,sys,time\nfrom pathlib import Path\n"
        f"if Path({str(block)!r}).exists():\n"
        " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f" Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        " while True: time.sleep(0.1)\n"
        f"os.execv({native_git!r},[{native_git!r},*sys.argv[1:]])\n"
    )
    wrapper.chmod(0o755)
    foundation = build_runtime_foundation(
        standard_local=True, git_service=GitService(executable=str(wrapper))
    )
    runtime = build_product_services(foundation)
    command_identity: tuple[int, str] | None = None
    try:
        runtime.locations.add(repository.parent)
        if mode == "forced":
            capture = runtime.runs._worker_configuration
            assert capture is not None

            def selected(run_id: str) -> Callable[[ExecutionTicket], RunWorkerLaunch]:
                prepare = capture(run_id)

                def after_admission(ticket: ExecutionTicket) -> RunWorkerLaunch:
                    block.touch()
                    return prepare(ticket)

                return after_admission

            monkeypatch.setattr(runtime.runs, "_worker_configuration", selected)
        run = runtime.runs.start(
            OperationAction.INIT,
            repository_ids={repository_id(repository)},
            fleet_run_id=f"generic-{mode}",
        )
        if mode == "forced":
            deadline = monotonic() + 15
            while not ready.exists():
                if monotonic() >= deadline:
                    pytest.fail("Generic native command did not reach its barrier")
                Event().wait(0.01)
            pid = int(ready.read_text())
            birth = observe_process_birth(pid)
            assert birth is not None and observe_process_liveness(pid) is True
            command_identity = (pid, f"{birth.boot_id}:{birth.start_ticks}")
            runtime.shutdown(
                drain_timeout_seconds=0,
                cancel_grace_seconds=0,
                termination_grace_seconds=0.01,
                containment_timeout_seconds=5,
            )
            # A batch owner's subreaper may retain the original dead PID until
            # it reaps adopted children. No executable command or guardian may
            # remain in the original owned session.
            assert wait_for_process_session_quiescence(pid, timeout=5)
        else:
            assert (
                runtime.runs.wait_for_terminal(run.fleet_run_id, timeout=15).run.status
                is FleetRunStatus.COMPLETED
            )
        detail = runtime.runs.get(run.fleet_run_id)
        physical = [
            event for event in detail.lifecycle_events if isinstance(event, ProcessStartingEvent)
        ]
        assert physical and any(type(event) is ProcessStartedEvent for event in physical)
        assert all(
            event.step_id is None and event.step_attempt_id is None and event.attempt_number is None
            for event in physical
        )
        tape = detail.game_tape
        assert tape.repositories[0].steps == ()
        assert all(
            event.step_id is None for event in tape.play_by_play if event.physical_execution_id
        )
        now = utc_now()
        timeline = runtime.runs.execution_timeline(
            from_at=now - timedelta(days=1), to_at=now + timedelta(days=1), bucket="day"
        )
        assert timeline.total_repositories == 1
        assert sum(bucket.run_count for row in timeline.items for bucket in row.buckets) == 1
        if mode == "forced":
            assert command_identity is not None
            assert any(
                type(event) is ProcessStartedEvent
                and (event.pid, event.process_identity) == command_identity
                for event in physical
            )
            assert detail.run.status is FleetRunStatus.CANCELLED
            assert not any(
                isinstance(event, ProcessExitedEvent) for event in detail.lifecycle_events
            )
            assert any(
                isinstance(event, ProcessStateUnknownEvent) for event in detail.lifecycle_events
            )
    finally:
        block.unlink(missing_ok=True)
        runtime.shutdown(
            wait_for_runs=False, cancel_grace_seconds=0.1, containment_timeout_seconds=5
        )
