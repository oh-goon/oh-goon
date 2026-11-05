"""In-process worker simulation for service tests with memory-owned fake runners.

This fake establishes no native process qualification. It reopens the real
database with a fresh session and uses explicit fake process identities through
the canonical claim before invoking the existing execution owner. Native worker
custody and transport are exercised separately by the process integration tests.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import count
from threading import Condition
from uuid import uuid4

from oh_goon.core.errors import ExecutionBusy, RuntimeQuiescing
from oh_goon.core.models.background import BackgroundFailure
from oh_goon.core.vocabulary import BackgroundFailureStage
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionTicket, RunExecutionSupervisor
from oh_goon.runtime.execution.lease_environment import LeaseEnvironment, SystemLeaseEnvironment
from oh_goon.runtime.execution.run_supervisor import (
    RunExecutionSupervisorSnapshot,
    RunSupervisorState,
    RunWorkerLaunch,
)
from oh_goon.services.fleet_run_execution import FleetRunExecution

_SIMULATED_PIDS = count(10_000_000)


def simulated_worker_coordinator(
    database: LocalDatabase, *, source_environment: LeaseEnvironment | None = None
) -> tuple[LocalDatabase, ExecutionCoordinator]:
    """Compose fresh SQLite custody for synchronous owner tests, not native proof.

    Explicit source-environment liveness lets existing restart fixtures revoke
    this simulated worker without pretending that a real host process died.
    """
    source = source_environment or SystemLeaseEnvironment()
    source_pid = source.process_id()
    source_birth = source.process_identity(source_pid)
    worker_pid = next(_SIMULATED_PIDS)
    while worker_pid == source_pid:
        worker_pid = next(_SIMULATED_PIDS)
    worker_birth = f"simulated-worker-birth:{uuid4().hex}"

    class Environment:
        def now(self) -> datetime:
            return source.now()

        def process_id(self) -> int:
            return worker_pid

        def process_alive(self, pid: int) -> bool:
            if pid == worker_pid:
                return source.process_alive(source_pid)
            return source.process_alive(pid)

        def process_identity(self, pid: int) -> str | None:
            if pid == worker_pid:
                if (
                    source.process_alive(source_pid)
                    and source.process_identity(source_pid) == source_birth
                ):
                    return worker_birth
                return None
            return source.process_identity(pid)

    worker_database = LocalDatabase.open_worker(database.capture_worker_context())
    return worker_database, ExecutionCoordinator(worker_database, lease_environment=Environment())


class _SimulatedProcessEnvironment:
    def __init__(self, identities: dict[int, str], *, pid: int | None = None) -> None:
        self._native = SystemLeaseEnvironment()
        self._identities = identities
        self._pid = pid

    def now(self) -> datetime:
        return self._native.now()

    def process_id(self) -> int:
        return self._native.process_id() if self._pid is None else self._pid

    def process_alive(self, pid: int) -> bool:
        return pid in self._identities or self._native.process_alive(pid)

    def process_identity(self, pid: int) -> str | None:
        return self._identities.get(pid) or self._native.process_identity(pid)


class SimulatedRunWorker(RunExecutionSupervisor):
    """Test-only asynchronous worker double; never launches an executable."""

    def __init__(
        self,
        database: LocalDatabase,
        execution: Callable[[LocalDatabase, ExecutionCoordinator], FleetRunExecution],
    ) -> None:
        self._database = database
        self._execution_factory = execution
        self._identities: dict[int, str] = {}
        self.parent_environment = _SimulatedProcessEnvironment(self._identities)
        self._unit_executor = ThreadPoolExecutor(max_workers=1)
        self._unit_condition = Condition()
        self._unit_phase = RunSupervisorState.ACCEPTING
        self._unit_active: set[str] = set()
        self._unit_failures: dict[str, BackgroundFailure] = {}
        self._unit_last_failure: BackgroundFailure | None = None

    def configuration(self, fleet_run_id: str) -> Callable[[ExecutionTicket], RunWorkerLaunch]:
        def prepare(ticket: ExecutionTicket) -> RunWorkerLaunch:
            if ticket.require_fleet_run_id() != fleet_run_id:
                raise AssertionError("simulated worker received another Run")
            context = self._database.capture_worker_context()
            pid = next(_SIMULATED_PIDS)
            self._identities[pid] = f"simulated-worker-birth:{uuid4().hex}"

            def execute() -> None:
                worker_database = LocalDatabase.open_worker(context)
                worker = ExecutionCoordinator(
                    worker_database,
                    lease_environment=_SimulatedProcessEnvironment(self._identities, pid=pid),
                )
                claimed = worker.claim_fleet_worker(ticket)
                self._execution_factory(worker_database, worker).execute(claimed)

            def cleanup() -> None:
                self._identities.pop(pid, None)

            return RunWorkerLaunch(
                argv=("simulated-unit-run-worker", fleet_run_id),
                cwd=self._database.path.parent,
                environment={},
                on_started=execute,
                cleanup=cleanup,
            )

        return prepare

    @property
    def state(self) -> RunSupervisorState:
        with self._unit_condition:
            return self._unit_phase

    def submit(self, fleet_run_id: str, launch: RunWorkerLaunch) -> None:
        if launch.argv != ("simulated-unit-run-worker", fleet_run_id):
            raise AssertionError("unit worker double cannot consume a native launch")
        with self._unit_condition:
            if self._unit_phase is not RunSupervisorState.ACCEPTING:
                raise RuntimeQuiescing("Simulated worker is quiescing")
            if self._unit_active:
                raise ExecutionBusy("Simulated worker already has active work")
            self._unit_active.add(fleet_run_id)
            self._unit_executor.submit(self._execute_simulated, fleet_run_id, launch)

    def _execute_simulated(self, fleet_run_id: str, launch: RunWorkerLaunch) -> None:
        failure: BackgroundFailure | None = None
        try:
            if launch.on_started is None:
                raise AssertionError("simulated worker has no execution callback")
            launch.on_started()
        except BaseException as exc:
            failure = BackgroundFailure.from_exception(
                exc, stage=BackgroundFailureStage.FLEET_RUN_SUPERVISOR
            )
        finally:
            try:
                if launch.on_exit is not None:
                    launch.on_exit(0 if failure is None else 1)
            except BaseException as exc:
                failure = BackgroundFailure.from_exception(
                    exc, stage=BackgroundFailureStage.FLEET_RUN_SUPERVISOR
                )
            finally:
                if launch.cleanup is not None:
                    launch.cleanup()
                with self._unit_condition:
                    self._unit_active.discard(fleet_run_id)
                    self._unit_last_failure = failure
                    if failure is not None:
                        self._unit_failures[fleet_run_id] = failure
                    self._unit_condition.notify_all()

    def is_active(self, fleet_run_id: str) -> bool:
        with self._unit_condition:
            return fleet_run_id in self._unit_active

    def wait_until_inactive(self, fleet_run_id: str, *, timeout: float | None = None) -> bool:
        with self._unit_condition:
            return self._unit_condition.wait_for(
                lambda: fleet_run_id not in self._unit_active, timeout=timeout
            )

    def take_failure(self, fleet_run_id: str) -> BackgroundFailure | None:
        with self._unit_condition:
            return self._unit_failures.pop(fleet_run_id, None)

    def snapshot(self) -> RunExecutionSupervisorSnapshot:
        with self._unit_condition:
            return RunExecutionSupervisorSnapshot(
                state=self._unit_phase,
                active_runs=len(self._unit_active),
                last_failure=self._unit_last_failure,
            )

    def quiesce(self) -> None:
        with self._unit_condition:
            if self._unit_phase is RunSupervisorState.ACCEPTING:
                self._unit_phase = RunSupervisorState.QUIESCING

    def shutdown(self, *, wait: bool = True) -> None:
        self.quiesce()
        self._unit_executor.shutdown(wait=wait)
        with self._unit_condition:
            self._unit_phase = RunSupervisorState.CLOSED
