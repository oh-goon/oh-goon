from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Condition, Lock, Thread

from oh_goon.core.errors import ExecutionBusy, RuntimeQuiescing
from oh_goon.core.models.background import BackgroundFailure
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.vocabulary import BackgroundFailureStage
from oh_goon.engine.command import CommandOutputObserver, CommandProcessLifecycleEvent, run_command


@dataclass(frozen=True, slots=True)
class RunWorkerLaunch:
    """One prepared native process launch; never executable Fleet work in the parent."""

    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str] = field(repr=False)
    pass_fds: tuple[int, ...] = ()
    on_started: Callable[[], None] | None = field(default=None, repr=False)
    stream_output: CommandOutputObserver | None = field(default=None, repr=False)
    on_exit: Callable[[int | None], None] | None = field(default=None, repr=False)
    cleanup: Callable[[], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.argv or not self.cwd.is_absolute():
            raise ValueError("Run worker requires one explicit native launch and working directory")


@dataclass(slots=True)
class _WorkerObservation:
    started: bool = False
    finished: bool = False
    spawn_failure: BaseException | None = None
    pid: int | None = None
    process_identity: str | None = None


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
    """Own only process-local Fleet worker submission and native process lifetime.

    Durable Fleet status/failure/history belongs to the Journal.  The supervisor
    retains only currently submitted run identities so duplicate local queueing can
    be rejected without growing a second in-memory run registry over process life.
    """

    def __init__(
        self, *, max_active_runs: int = 1, diagnostics: DiagnosticEmitter | None = None
    ) -> None:
        if max_active_runs != 1:
            raise ValueError("Run worker phase one admits one active FleetRun per runtime")
        self._active: dict[str, _WorkerObservation] = {}
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

    def submit(self, fleet_run_id: str, launch: RunWorkerLaunch) -> None:
        if type(launch) is not RunWorkerLaunch:
            raise TypeError("FleetRun submission requires a prepared Run worker process")
        with self._condition:
            if self._state is not RunSupervisorState.ACCEPTING:
                raise RuntimeQuiescing("Runtime is quiescing and cannot admit new FleetRun work")
            if fleet_run_id in self._active:
                raise ExecutionBusy(f"Fleet run is already submitted: {fleet_run_id}")
            if self._active:
                raise ExecutionBusy("This runtime already supervises an active FleetRun")
            observed = _WorkerObservation()
            self._active[fleet_run_id] = observed
            try:
                # The thread observes a native process only. It cannot receive a
                # FleetRunExecution callable or execute admitted Run work itself.
                Thread(
                    target=self._run,
                    args=(fleet_run_id, launch, observed),
                    name="oh-goon-run-observer",
                    daemon=True,
                ).start()
            except BaseException:
                self._active.pop(fleet_run_id, None)
                raise
            self._condition.wait_for(lambda: observed.started or observed.finished)
            if not observed.started:
                raise RuntimeError(
                    "Run worker process could not be created"
                ) from observed.spawn_failure

    def is_active(self, fleet_run_id: str) -> bool:
        """Return the supervisor-owned process-local liveness fact for one submitted run."""
        with self._lock:
            return fleet_run_id in self._active

    def wait_until_inactive(self, fleet_run_id: str, *, timeout: float | None = None) -> bool:
        """Wait for this process-local submission to leave the supervisor.

        The active-run set is the supervisor's only live execution fact. Waiting on
        that owner avoids polling durable state on an arbitrary timer.
        """
        with self._condition:
            return self._condition.wait_for(
                lambda: fleet_run_id not in self._active,
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
                active_runs=len(self._active),
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
        # Quiescing stops submission. Durable cancellation and Run truth remain
        # lifecycle-owned; the supervisor never turns shutdown into a Run result.
        with self._condition:
            if wait:
                self._condition.wait_for(lambda: not self._active)
            self._state = RunSupervisorState.CLOSED

    def _emit(self, event: DiagnosticEvent) -> None:
        if self._diagnostics is not None:
            try:
                self._diagnostics.emit(event)
            except Exception:
                # A diagnostic sink never owns native lifetime or retirement.
                pass

    def _run(
        self,
        fleet_run_id: str,
        launch: RunWorkerLaunch,
        observed: _WorkerObservation,
    ) -> None:
        failure: BackgroundFailure | None = None
        returncode: int | None = None

        def observe_process(event: CommandProcessLifecycleEvent) -> None:
            if event.kind == "PROCESS_STARTED":
                if launch.on_started is not None:
                    launch.on_started()
                with self._condition:
                    observed.started = True
                    observed.pid = event.pid
                    observed.process_identity = event.process_identity
                    self._condition.notify_all()

        try:
            result = run_command(
                *launch.argv,
                cwd=launch.cwd,
                env=launch.environment,
                pass_fds=launch.pass_fds,
                stream_output=launch.stream_output,
                process_observer=observe_process,
                timeout=None,
                parent_death_signal=False,
                require_process_group_quiescence=True,
                max_capture_bytes_per_stream=64 * 1024,
                retain_output_tail=True,
            )
            returncode = result.returncode
            if returncode != 0:
                raise RuntimeError("Run worker process exited abnormally")
        except BaseException as exc:
            if not observed.started:
                observed.spawn_failure = exc
            # Native stderr and argv can carry sensitive runtime material.
            # Keep only the fixed execution observation in this local mailbox.
            safe = RuntimeError("Run worker process supervision failed")
            failure = BackgroundFailure.from_exception(
                safe, stage=BackgroundFailureStage.FLEET_RUN_SUPERVISOR
            )
            if self._diagnostics is not None:
                self._emit(
                    DiagnosticEvent.from_exception(
                        safe,
                        source="run_supervisor",
                        owner="RunExecutionSupervisor",
                        operation="execute_fleet_run",
                        execution_affected=True,
                        failure_class="background_worker",
                        fleet_run_id=fleet_run_id,
                    )
                )
        finally:
            if observed.started and launch.on_exit is not None:
                try:
                    launch.on_exit(returncode)
                except BaseException:
                    failure = BackgroundFailure.from_exception(
                        RuntimeError("Run worker exit observation failed"),
                        stage=BackgroundFailureStage.FLEET_RUN_SUPERVISOR,
                    )
            if launch.cleanup is not None:
                try:
                    launch.cleanup()
                except Exception:
                    if self._diagnostics is not None:
                        self._emit(
                            DiagnosticEvent(
                                source="run_supervisor",
                                owner="RunExecutionSupervisor",
                                operation="close_worker_transport",
                                severity="WARNING",
                                message="Run worker transport cleanup was incomplete",
                                execution_affected=False,
                                fleet_run_id=fleet_run_id,
                            )
                        )
            with self._condition:
                observed.finished = True
                self._active.pop(fleet_run_id, None)
                self._last_failure = failure
                if failure is not None:
                    self._failed_runs[fleet_run_id] = failure
                self._condition.notify_all()
