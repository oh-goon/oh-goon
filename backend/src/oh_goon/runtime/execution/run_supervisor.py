from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from threading import Condition, Lock

from oh_goon.core.errors import ExecutionBusy, RuntimeQuiescing
from oh_goon.core.models.background import BackgroundFailure
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.vocabulary import BackgroundFailureStage


class RunSupervisorState(StrEnum):
    ACCEPTING = "ACCEPTING"
    QUIESCING = "QUIESCING"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class RunExecutionSupervisorSnapshot:
    state: RunSupervisorState
    active_runs: int
    last_failure: BackgroundFailure | None


class RunExecutionSupervisor:
    """Own only process-local Fleet task admission and executor lifetime.

    Durable Fleet status/failure/history belongs to the Journal.  The supervisor
    retains only currently submitted run identities so duplicate local queueing can
    be rejected without growing a second in-memory run registry over process life.
    """

    def __init__(
        self, *, max_active_runs: int = 1, diagnostics: DiagnosticEmitter | None = None
    ) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_active_runs,
            thread_name_prefix="oh-goon-run",
        )
        self._active_run_ids: set[str] = set()
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._state = RunSupervisorState.ACCEPTING
        self._last_failure: BackgroundFailure | None = None
        self._failed_runs: dict[str, BackgroundFailure] = {}
        self._diagnostics = diagnostics

    @property
    def state(self) -> RunSupervisorState:
        with self._lock:
            return self._state

    def submit(self, fleet_run_id: str, work: Callable[[], object]) -> None:
        with self._lock:
            if self._state is not RunSupervisorState.ACCEPTING:
                raise RuntimeQuiescing("Runtime is quiescing and cannot admit new FleetRun work")
            if fleet_run_id in self._active_run_ids:
                raise ExecutionBusy(f"Fleet run is already submitted: {fleet_run_id}")
            self._active_run_ids.add(fleet_run_id)
            try:
                self._pool.submit(self._run, fleet_run_id, work)
            except BaseException:
                self._active_run_ids.remove(fleet_run_id)
                raise

    def is_active(self, fleet_run_id: str) -> bool:
        """Return the supervisor-owned process-local liveness fact for one submitted run."""
        with self._lock:
            return fleet_run_id in self._active_run_ids

    def wait_until_inactive(self, fleet_run_id: str, *, timeout: float | None = None) -> bool:
        """Wait for this process-local submission to leave the supervisor.

        The active-run set is the supervisor's only live execution fact. Waiting on
        that owner avoids polling durable state on an arbitrary timer.
        """
        with self._condition:
            return self._condition.wait_for(
                lambda: fleet_run_id not in self._active_run_ids,
                timeout=timeout,
            )

    def take_failure(self, fleet_run_id: str) -> BackgroundFailure | None:
        """Consume one run-scoped background failure after local execution ends.

        Durable lifecycle truth still belongs to the Journal. This mailbox exists only so
        a foreground waiter can surface the exact process-local worker failure instead of
        replacing it with a generic inactive/non-terminal conflict. Successful runs are
        never retained here.
        """
        with self._lock:
            return self._failed_runs.pop(fleet_run_id, None)

    def snapshot(self) -> RunExecutionSupervisorSnapshot:
        with self._lock:
            return RunExecutionSupervisorSnapshot(
                state=self._state,
                active_runs=len(self._active_run_ids),
                last_failure=(self._last_failure),
            )

    def quiesce(self) -> None:
        with self._lock:
            if self._state is RunSupervisorState.ACCEPTING:
                self._state = RunSupervisorState.QUIESCING

    def shutdown(self, *, wait: bool = True) -> None:
        self.quiesce()
        with self._lock:
            if self._state is RunSupervisorState.CLOSED:
                return
        # Never cancel admitted-but-not-yet-started work. Its durable run/lease must
        # reach the worker so cancellation/terminalization can settle atomically.
        self._pool.shutdown(wait=wait, cancel_futures=False)
        with self._lock:
            self._state = RunSupervisorState.CLOSED

    def _run(self, fleet_run_id: str, work: Callable[[], object]) -> None:
        failure: BackgroundFailure | None = None
        try:
            work()
        except BaseException as exc:
            failure = BackgroundFailure.from_exception(
                exc, stage=BackgroundFailureStage.FLEET_RUN_SUPERVISOR
            )
            if self._diagnostics is not None:
                self._diagnostics.emit(
                    DiagnosticEvent.from_exception(
                        exc,
                        source="run_supervisor",
                        owner="RunExecutionSupervisor",
                        operation="execute_fleet_run",
                        execution_affected=True,
                        failure_class="background_worker",
                        fleet_run_id=fleet_run_id,
                    )
                )
            raise
        finally:
            with self._condition:
                self._active_run_ids.discard(fleet_run_id)
                self._last_failure = failure
                if failure is not None:
                    self._failed_runs[fleet_run_id] = failure
                self._condition.notify_all()
