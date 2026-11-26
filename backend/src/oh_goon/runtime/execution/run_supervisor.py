from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from pathlib import Path
from threading import Condition, Lock, Thread
from typing import TYPE_CHECKING

from pydantic import JsonValue

from oh_goon.core.errors import ErrorKind, ExecutionBusy, RuntimeQuiescing, StateConflict
from oh_goon.core.models.background import BackgroundFailure, RunWorkerExitCode
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.run_resources import RunConcurrencyAdmission
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.vocabulary import BackgroundFailureStage
from oh_goon.engine.command import (
    CommandAuthorityTerminatedError,
    CommandOutputObserver,
    CommandProcessLifecycleEvent,
    run_command,
)

if TYPE_CHECKING:
    from oh_goon.core.models.agent_providers import ResolvedNativeAgent
    from oh_goon.runtime.execution.coordinator import ExecutionTicket


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
    finalize: Callable[[], object] | None = field(default=None, repr=False)
    finalization_cleanup: Callable[[], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.argv or not self.cwd.is_absolute():
            raise ValueError("Run worker requires one explicit native launch and working directory")
        if self.finalization_cleanup is not None and self.finalize is None:
            raise ValueError("Run worker resource handles require their original finalizer")


@dataclass(frozen=True, slots=True)
class RunWorkerPreparation:
    """Resolved metadata and transport preparation for exactly one original Run."""

    fleet_run_id: str
    prepare: Callable[[ExecutionTicket], RunWorkerLaunch] = field(repr=False)
    concurrency: RunConcurrencyAdmission | None = None
    deferred_resource_scope: dict[str, JsonValue] | None = field(default=None, repr=False)
    require_native_binding: Callable[[Path, ResolvedNativeAgent], None] | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.concurrency is not None and self.concurrency.fleet_run_id != self.fleet_run_id:
            raise ValueError("Run worker resources differ from their original Run")
        if (self.concurrency is None) != (self.deferred_resource_scope is None):
            raise ValueError("Run worker capacity requires its exact private resource transport")

    def __call__(self, ticket: ExecutionTicket) -> RunWorkerLaunch:
        if ticket.require_fleet_run_id() != self.fleet_run_id:
            raise StateConflict("Run worker preparation differs from its admission ticket")
        return self.prepare(ticket)


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


@dataclass(frozen=True, slots=True)
class _PendingWorkerFinalization:
    observation: _WorkerObservation
    retry: Callable[[], None] | None = field(repr=False)
    retry_lock: Lock = field(default_factory=Lock, repr=False, compare=False)


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
        if type(max_active_runs) is not int or max_active_runs not in {1, 2}:
            raise ValueError("Run worker capacity must be one or two active FleetRuns")
        if type(failure_retention) is not int or failure_retention < 1:
            raise ValueError("Run worker failure retention must be positive")
        self._active: dict[str, _WorkerObservation] = {}
        self._pending_finalization: dict[str, _PendingWorkerFinalization] = {}
        self._max_active_runs = max_active_runs
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
            self._require_capacity_locked(fleet_run_id)
            admission = RunWorkerAdmission(fleet_run_id)
            self._active[fleet_run_id] = _WorkerObservation(admission=admission)
            return admission

    def _require_capacity_locked(self, fleet_run_id: str) -> None:
        if fleet_run_id in self._active or fleet_run_id in self._pending_finalization:
            raise ExecutionBusy(
                f"Fleet run is already submitted or awaiting finalization: {fleet_run_id}"
            )
        if len(self._active) + len(self._pending_finalization) >= self._max_active_runs:
            raise ExecutionBusy("This runtime already supervises its active FleetRun capacity")

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
                self._require_capacity_locked(fleet_run_id)
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
        # Retry only callbacks captured by the original observation. A public
        # caller cannot acknowledge native absence with a Run ID or boolean.
        # Callback effects remain in the original native/lifecycle owners and
        # never execute while the supervisor lock is held.
        with self._condition:
            pending = tuple(self._pending_finalization.items())
        for fleet_run_id, original in pending:
            if original.retry is None:
                continue
            with original.retry_lock:
                with self._condition:
                    if self._pending_finalization.get(fleet_run_id) is not original:
                        continue
                try:
                    original.retry()
                except Exception:
                    continue
                with self._condition:
                    if self._pending_finalization.get(fleet_run_id) is original:
                        self._pending_finalization.pop(fleet_run_id)
                        self._condition.notify_all()
        with self._condition:
            if self._active or self._pending_finalization:
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
        native_quiescent = False

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
            native_quiescent = result.supervisor.final_process_group_quiescent
            if returncode != 0:
                failure = _worker_failure(returncode=returncode)
        except BaseException as exc:
            if not observed.started:
                observed.spawn_failure = exc
            # Native stderr and argv can carry sensitive runtime material.
            # Keep only the fixed execution observation in this local mailbox.
            if isinstance(exc, CommandAuthorityTerminatedError):
                native_quiescent = exc.supervisor.final_process_group_quiescent
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
            callback_failed = False
            finalizer_succeeded = False

            def retry_original_finalization() -> None:
                if observed.started and launch.on_exit is not None:
                    launch.on_exit(returncode)
                if launch.finalize is not None:
                    launch.finalize()
                if launch.finalization_cleanup is not None:
                    launch.finalization_cleanup()

            if observed.started and launch.on_exit is not None:
                try:
                    launch.on_exit(returncode)
                except BaseException:
                    callback_failed = True
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
            if launch.finalize is not None:
                try:
                    launch.finalize()
                    if launch.finalization_cleanup is not None:
                        launch.finalization_cleanup()
                    finalizer_succeeded = True
                except BaseException:
                    callback_failed = True
                    if failure is None:
                        failure = _worker_failure(
                            code="RUN_WORKER_FINALIZATION_FAILED",
                            message="Run worker resource finalization remains unverified",
                        )
                    self._emit(
                        DiagnosticEvent(
                            severity="WARNING",
                            source="run_supervisor",
                            owner="RunExecutionSupervisor",
                            operation="finalize_worker_resources",
                            message="Run worker resource finalization remains unverified",
                            execution_affected=False,
                            diagnostic_capability_affected="worker_resource_finalization",
                            failure_class="worker_resource_finalization",
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
                # An old observer cannot remove or publish failure against
                # another registration generation with the same Run ID.
                if self._active.get(fleet_run_id) is observed:
                    self._active.pop(fleet_run_id)
                    if callback_failed or (
                        observed.started and not native_quiescent and not finalizer_succeeded
                    ):
                        self._pending_finalization[fleet_run_id] = _PendingWorkerFinalization(
                            observed,
                            retry_original_finalization
                            if launch.finalize is not None or native_quiescent
                            else None,
                        )
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
        RunWorkerExitCode.RESOURCE_SCOPE_REFUSED: (
            "RESOURCE_SCOPE_REFUSED",
            "original native worker resource scope was refused",
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
