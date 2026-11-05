from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from pathlib import Path
from threading import Condition, Lock, Thread

from oh_goon.core.errors import ErrorKind, ExecutionBusy, RuntimeQuiescing, StateConflict
from oh_goon.core.models.background import BackgroundFailure, RunWorkerExitCode
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.vocabulary import BackgroundFailureStage
from oh_goon.engine.command import (
    CommandAuthorityTerminatedError,
    CommandOutputObserver,
    CommandProcessLifecycleEvent,
    run_command,
)


@dataclass(frozen=True, slots=True, eq=False)
class RunWorkerAdmission:
    """One process-local pending submission; never executable or durable Run authority."""

    fleet_run_id: str

    def __post_init__(self) -> None:
        if type(self.fleet_run_id) is not str or not self.fleet_run_id:
            raise ValueError("Run submission identity is required")


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
    admission: RunWorkerAdmission | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.argv or not self.cwd.is_absolute():
            raise ValueError("Run worker requires one explicit native launch and working directory")


@dataclass(slots=True)
class _WorkerObservation:
    admission: RunWorkerAdmission | None = None
    submitted: bool = False
    started: bool = False
    finished: bool = False
    spawn_failure: BaseException | None = None
    pid: int | None = None
    process_identity: str | None = None
    termination: TerminationSignal | None = None


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
        self,
        *,
        max_active_runs: int = 1,
        diagnostics: DiagnosticEmitter | None = None,
        failure_retention: int = 64,
    ) -> None:
        if max_active_runs != 1:
            raise ValueError("Run worker phase one admits one active FleetRun per runtime")
        if type(failure_retention) is not int or failure_retention < 1:
            raise ValueError("Run worker failure retention must be positive")
        self._active: dict[str, _WorkerObservation] = {}
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._state = RunSupervisorState.ACCEPTING
        self._last_failure: BackgroundFailure | None = None
        self._failed_runs: dict[str, BackgroundFailure] = {}
        self._diagnostics = diagnostics
        self._failure_retention = failure_retention

    @property
    def state(self) -> RunSupervisorState:
        with self._lock:
            return self._state

    def require_accepting(self) -> None:
        """Reject new ingress without retaining the lock during planning/capacity waits."""
        with self._lock:
            self._require_accepting_locked()

    def _require_accepting_locked(self) -> None:
        if self._state is not RunSupervisorState.ACCEPTING:
            raise RuntimeQuiescing("Runtime is quiescing and cannot admit new FleetRun work")

    def register_admission(self, fleet_run_id: str) -> RunWorkerAdmission:
        """Linearize a pending submission at the caller's canonical admission write cut."""
        with self._condition:
            self._require_accepting_locked()
            if self._active:
                raise ExecutionBusy("This runtime already supervises an active FleetRun")
            admission = RunWorkerAdmission(fleet_run_id)
            self._active[fleet_run_id] = _WorkerObservation(admission=admission)
            return admission

    def abandon_admission(self, admission: RunWorkerAdmission) -> None:
        """Retire only this unsubmitted registration after rollback/preparation failure."""
        with self._condition:
            observed = self._active.get(admission.fleet_run_id)
            if observed is not None and observed.admission is admission and not observed.submitted:
                self._active.pop(admission.fleet_run_id)
                self._condition.notify_all()

    def submit(self, fleet_run_id: str, launch: RunWorkerLaunch) -> None:
        if type(launch) is not RunWorkerLaunch:
            raise TypeError("FleetRun submission requires a prepared Run worker process")
        with self._condition:
            observed = self._active.get(fleet_run_id)
            if launch.admission is not None:
                if (
                    self._state is RunSupervisorState.CLOSED
                    or observed is None
                    or observed.admission is not launch.admission
                    or launch.admission.fleet_run_id != fleet_run_id
                ):
                    raise RuntimeQuiescing("Run admission no longer owns its pending submission")
                if observed.submitted:
                    raise ExecutionBusy(f"Fleet run is already submitted: {fleet_run_id}")
                if observed.termination is not None:
                    raise RuntimeQuiescing(
                        "Pending Run submission exceeded the runtime drain deadline"
                    )
            else:
                self._require_accepting_locked()
                if observed is not None:
                    raise ExecutionBusy(f"Fleet run is already submitted: {fleet_run_id}")
                if self._active:
                    raise ExecutionBusy("This runtime already supervises an active FleetRun")
                observed = _WorkerObservation()
                self._active[fleet_run_id] = observed
            observed.submitted = True
            try:
                # The thread observes a native process only. It cannot receive a
                # FleetRunExecution callable or execute admitted Run work itself.
                Thread(
                    target=self._run,
                    args=(fleet_run_id, launch, observed),
                    name="oh-goon-run-observer",
                    daemon=True,
                ).start()
            except Exception:
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

    def wait_until_idle(self, *, timeout: float | None = None) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: not self._active, timeout=timeout)

    def request_containment(self, *, grace_seconds: float = 1.0) -> tuple[str, ...]:
        """Ask the existing native command owner to contain its exact owned processes."""
        if (
            isinstance(grace_seconds, bool)
            or not isinstance(grace_seconds, (int, float))
            or not isfinite(grace_seconds)
            or not 0 <= grace_seconds <= 60
        ):
            raise ValueError("Run containment grace must be between 0 and 60 seconds")
        signal = TerminationSignal("operator", "runtime_drain_deadline", grace_seconds)
        with self._condition:
            for observed in self._active.values():
                observed.termination = signal
            return tuple(self._active)

    def require_containment_complete(self) -> None:
        with self._condition:
            if self._active or (
                self._last_failure is not None
                and self._last_failure.code == "RUN_WORKER_CONTAINMENT_FAILED"
            ):
                raise StateConflict("Run worker containment is unverified")

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

    def quiesce(self) -> tuple[str, ...]:
        with self._lock:
            if self._state is RunSupervisorState.ACCEPTING:
                self._state = RunSupervisorState.QUIESCING
            return tuple(self._active)

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

        def termination_requested() -> TerminationSignal | None:
            with self._lock:
                return observed.termination

        def observe_process(event: CommandProcessLifecycleEvent) -> None:
            if event.kind == "PROCESS_STARTED":
                with self._condition:
                    observed.started = True
                    observed.pid = event.pid
                    observed.process_identity = event.process_identity
                if launch.on_started is not None:
                    launch.on_started()
                with self._condition:
                    self._condition.notify_all()

        try:
            result = run_command(
                *launch.argv,
                cwd=launch.cwd,
                env=launch.environment,
                pass_fds=launch.pass_fds,
                stream_output=launch.stream_output,
                termination_requested=termination_requested,
                process_observer=observe_process,
                timeout=None,
                parent_death_signal=False,
                require_process_group_quiescence=True,
                max_capture_bytes_per_stream=64 * 1024,
                retain_output_tail=True,
            )
            returncode = result.returncode
            if returncode != 0:
                failure = _worker_failure(returncode=returncode)
        except BaseException as exc:
            if not observed.started:
                observed.spawn_failure = exc
            # Native stderr and argv can carry sensitive runtime material.
            # Keep only the fixed execution observation in this local mailbox.
            if isinstance(exc, CommandAuthorityTerminatedError):
                failure = _worker_failure(
                    code=(
                        "RUN_WORKER_FORCED_CONTAINMENT"
                        if exc.supervisor.final_process_group_quiescent
                        else "RUN_WORKER_CONTAINMENT_FAILED"
                    ),
                    message=(
                        "Run worker process group stopped after the drain deadline; effects require canonical reconciliation"
                        if exc.supervisor.final_process_group_quiescent
                        else "Run worker native containment remains unverified"
                    ),
                )
            else:
                failure = _worker_failure(
                    code="RUN_WORKER_SPAWN_FAILED"
                    if not observed.started
                    else "RUN_WORKER_CONTAINMENT_FAILED",
                    message="Run worker process could not be created"
                    if not observed.started
                    else "Run worker supervision stopped without native quiescence proof",
                )
        finally:
            if failure is not None:
                self._emit(
                    DiagnosticEvent(
                        severity="ERROR",
                        source="run_supervisor",
                        owner="RunExecutionSupervisor",
                        operation="execute_fleet_run",
                        execution_affected=True,
                        failure_class=failure.code,
                        fleet_run_id=fleet_run_id,
                        message=(
                            f"{failure.message}; pid={observed.pid}; "
                            f"birth={observed.process_identity}; exit={returncode}"
                        ),
                    )
                )
            if observed.started and launch.on_exit is not None:
                try:
                    launch.on_exit(returncode)
                except BaseException:
                    if failure is None:
                        failure = _worker_failure(
                            code="RUN_WORKER_EXIT_OBSERVATION_FAILED",
                            message="Run worker exit observation failed",
                        )
                    self._emit(
                        DiagnosticEvent(
                            severity="WARNING",
                            source="run_supervisor",
                            owner="RunExecutionSupervisor",
                            operation="observe_worker_exit",
                            message="Run worker exit observation failed",
                            execution_affected=False,
                            diagnostic_capability_affected="worker_exit_observation",
                            failure_class="worker_exit_observation",
                            fleet_run_id=fleet_run_id,
                        )
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
                    self._failed_runs.pop(fleet_run_id, None)
                    self._failed_runs[fleet_run_id] = failure
                    while len(self._failed_runs) > self._failure_retention:
                        self._failed_runs.pop(next(iter(self._failed_runs)))
                self._condition.notify_all()


def _worker_failure(
    *, returncode: int | None = None, code: str | None = None, message: str | None = None
) -> BackgroundFailure:
    names: dict[int, tuple[str, str]] = {
        RunWorkerExitCode.HANDOFF_REFUSED: ("HANDOFF_REFUSED", "protected handoff was refused"),
        RunWorkerExitCode.RUNTIME_IDENTITY_REFUSED: (
            "RUNTIME_IDENTITY_REFUSED",
            "loaded runtime identity was refused",
        ),
        RunWorkerExitCode.DATABASE_UNAVAILABLE: (
            "DATABASE_UNAVAILABLE",
            "canonical database authority is unavailable",
        ),
        RunWorkerExitCode.CLAIM_REFUSED: ("CLAIM_REFUSED", "canonical custody claim was refused"),
        RunWorkerExitCode.BOOTSTRAP_FAILED: (
            "BOOTSTRAP_FAILED",
            "claimed execution bootstrap failed",
        ),
        RunWorkerExitCode.EXECUTION_FAILED: (
            "EXECUTION_FAILED",
            "execution stopped; inspect canonical Run evidence",
        ),
        RunWorkerExitCode.CANCELLATION_OBSERVED: (
            "CANCELLATION_OBSERVED",
            "execution observed cancellation",
        ),
    }
    if code is None:
        suffix, detail = names.get(
            returncode if returncode is not None else -1, ("CRASHED", "process exited abnormally")
        )
        code, message = "RUN_WORKER_" + suffix, "Run worker " + detail
    return BackgroundFailure(
        code=code,
        kind=ErrorKind.INTERNAL,
        stage=BackgroundFailureStage.FLEET_RUN_SUPERVISOR,
        message=message or "Run worker process observation failed",
    )
