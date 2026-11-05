"""Controlled native orderings at the existing claim/cancel/terminal SQLite cuts."""

from __future__ import annotations

import os
import select
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from oh_goon._application_foundation import RuntimeFoundation, build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import InvalidRunTransition
from oh_goon.core.models.background import RunWorkerExitCode
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunStatus, OperationAction
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.runtime.execution.run_supervisor import RunExecutionSupervisor, RunWorkerLaunch

_Cut = Literal[
    "cancel-before-claim",
    "cancel-after-claim",
    "cancel-before-completion",
    "cancel-after-completion",
]

_WORKER_BARRIER = """
import os, runpy, sys
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle

header, ready, proceed, cut = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]

def synchronize():
    os.write(ready, b'R')
    assert os.read(proceed, 1) == b'G'
    os.close(ready)
    os.close(proceed)

if cut == 'cancel-before-claim':
    synchronize()
elif cut == 'cancel-after-claim':
    original = ExecutionCoordinator.claim_fleet_worker
    def claim(self, ticket):
        owned = original(self, ticket)
        synchronize()
        return owned
    ExecutionCoordinator.claim_fleet_worker = claim
else:
    original = FleetRunLifecycle.finish_detail
    def finish(self, session, intent, failure=None):
        if cut == 'cancel-before-completion':
            synchronize()
            return original(self, session, intent, failure)
        detail = original(self, session, intent, failure)
        synchronize()
        return detail
    FleetRunLifecycle.finish_detail = finish

sys.argv = ['oh_goon._application_worker', '--handoff', header]
runpy.run_module('oh_goon._application_worker', run_name='__main__', alter_sys=True)
"""


class _BarrierWorker(RunExecutionSupervisor):
    """Native launch observer used only to hold one declared public-owner cut.

    The original protected handoff, fresh process, claim, execution and terminal
    owners still run. Pipes are bounded test synchronization, never Run truth.
    """

    def __init__(self, cut: _Cut) -> None:
        super().__init__()
        self._cut = cut
        self._ready_read, self._ready_write = os.pipe2(os.O_CLOEXEC)
        self._proceed_read, self._proceed_write = os.pipe2(os.O_CLOEXEC)
        self._descriptors = {
            self._ready_read,
            self._ready_write,
            self._proceed_read,
            self._proceed_write,
        }
        self.returncode: int | None = None

    def _close(self, descriptor: int) -> None:
        if descriptor in self._descriptors:
            self._descriptors.remove(descriptor)
            os.close(descriptor)

    def submit(self, fleet_run_id: str, launch: RunWorkerLaunch) -> None:
        def started() -> None:
            if launch.on_started is not None:
                launch.on_started()
            self._close(self._ready_write)
            self._close(self._proceed_read)

        def cleanup() -> None:
            try:
                if launch.cleanup is not None:
                    launch.cleanup()
            finally:
                # The launcher owns only its passed child-side copies. The
                # test retains its reader/writer until the worker is retired.
                self._close(self._ready_write)
                self._close(self._proceed_read)

        def exited(returncode: int | None) -> None:
            self.returncode = returncode
            if launch.on_exit is not None:
                launch.on_exit(returncode)

        super().submit(
            fleet_run_id,
            replace(
                launch,
                argv=(
                    launch.argv[0],
                    "-I",
                    "-c",
                    _WORKER_BARRIER,
                    launch.argv[-1],
                    str(self._ready_write),
                    str(self._proceed_read),
                    self._cut,
                ),
                pass_fds=(*launch.pass_fds, self._ready_write, self._proceed_read),
                on_started=started,
                on_exit=exited,
                cleanup=cleanup,
            ),
        )

    def wait_at_cut(self) -> None:
        assert select.select([self._ready_read], [], [], 30)[0], "worker did not reach race cut"
        assert os.read(self._ready_read, 1) == b"R", "worker exited before race cut"
        self._close(self._ready_read)

    def proceed(self) -> None:
        if self._proceed_write in self._descriptors:
            try:
                os.write(self._proceed_write, b"G")
            except BrokenPipeError:
                pass
            finally:
                self._close(self._proceed_write)

    def close_barrier(self) -> None:
        for descriptor in tuple(self._descriptors):
            self._close(descriptor)


def _runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: _Cut,
) -> tuple[RuntimeFoundation, ApplicationRuntime, _BarrierWorker, str]:
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
    foundation = build_runtime_foundation(standard_local=True, startup_purpose="workflow")
    foundation.supervisor.shutdown(wait=True)
    supervisor = _BarrierWorker(cut)
    foundation = replace(foundation, supervisor=supervisor)
    runtime = build_product_services(foundation)
    runtime.locations.add(repository.parent)
    repository_id = RepositoryIdentity.model_validate({"path": repository}).repository_id
    return foundation, runtime, supervisor, repository_id


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native protected Run transport")
@pytest.mark.parametrize("cut", ("cancel-before-claim", "cancel-after-claim"))
def test_native_claim_and_cancellation_have_one_canonical_ordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: _Cut,
) -> None:
    foundation, runtime, supervisor, repository_id = _runtime(tmp_path, monkeypatch, cut)
    run_id = f"race-{cut}"
    try:
        runtime.runs.start(
            OperationAction.INIT, repository_ids={repository_id}, fleet_run_id=run_id
        )
        supervisor.wait_at_cut()
        with foundation.database.transaction() as connection:
            lease = connection.execute(
                "SELECT custody_phase, owner_pid FROM execution_lease WHERE fleet_run_id=?",
                (run_id,),
            ).fetchone()
        assert lease is not None
        if cut == "cancel-before-claim":
            assert tuple(lease) == ("ADMITTED_UNCLAIMED", os.getpid())
        else:
            assert lease[0] == "WORKER_OWNED" and lease[1] != os.getpid()
        cancelling = runtime.runs.cancel(run_id)
        assert cancelling.status is FleetRunStatus.CANCELLING
        supervisor.proceed()
        detail = runtime.runs.wait_for_terminal(run_id, timeout=30)
        assert detail.run.status is FleetRunStatus.CANCELLED
        assert supervisor.returncode == (
            RunWorkerExitCode.CLAIM_REFUSED if cut == "cancel-before-claim" else 0
        )
        assert detail.operations == ()
        events = runtime.runs.events(run_id).events
        assert [event.event_type for event in events] == [
            FleetRunEventType.RUN_ADMITTED,
            FleetRunEventType.RUN_CANCEL_REQUESTED,
            FleetRunEventType.RUN_CANCELLED,
        ]
        assert foundation.execution.active_lease() is None
    finally:
        supervisor.proceed()
        runtime.shutdown(wait_for_runs=True)
        supervisor.close_barrier()


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native protected Run transport")
@pytest.mark.parametrize("cut", ("cancel-before-completion", "cancel-after-completion"))
def test_native_completion_and_cancellation_preserve_the_winning_terminal_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: _Cut,
) -> None:
    foundation, runtime, supervisor, repository_id = _runtime(tmp_path, monkeypatch, cut)
    run_id = f"race-{cut}"
    try:
        runtime.runs.start(
            OperationAction.INIT, repository_ids={repository_id}, fleet_run_id=run_id
        )
        supervisor.wait_at_cut()
        if cut == "cancel-before-completion":
            assert runtime.runs.cancel(run_id).status is FleetRunStatus.CANCELLING
            expected = FleetRunStatus.CANCELLED
        else:
            with pytest.raises(InvalidRunTransition, match="already terminal"):
                runtime.runs.cancel(run_id)
            expected = FleetRunStatus.COMPLETED
        supervisor.proceed()
        detail = runtime.runs.wait_for_terminal(run_id, timeout=30)
        assert detail.run.status is expected
        assert supervisor.returncode == 0
        assert len(detail.operations) == 1  # cancellation cannot erase a settled effect report
        events = runtime.runs.events(run_id).events
        terminal = [
            event.event_type
            for event in events
            if event.event_type
            in {
                FleetRunEventType.RUN_COMPLETED,
                FleetRunEventType.RUN_CANCELLED,
                FleetRunEventType.RUN_FAILED,
            }
        ]
        assert terminal == [
            FleetRunEventType.RUN_CANCELLED
            if expected is FleetRunStatus.CANCELLED
            else FleetRunEventType.RUN_COMPLETED
        ]
        assert foundation.execution.active_lease() is None
    finally:
        supervisor.proceed()
        runtime.shutdown(wait_for_runs=True)
        supervisor.close_barrier()
