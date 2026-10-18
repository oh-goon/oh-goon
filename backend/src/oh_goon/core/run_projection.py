from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from oh_goon.core.errors import InvalidRunTransition
from oh_goon.core.execution_lifecycle_validation import validate_execution_lifecycle
from oh_goon.core.product_identity import (
    fleet_step_attempt_id,
    repository_attempt_operation_id,
    validate_repository_attempt_operation_id,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport
from oh_goon.core.models.fleet_plan_evidence import CompletedPlanStepExecution, FailedPlanStepExecution, SkippedPlanStepExecution
from oh_goon.core.models.run import (
    CancelledFleetRun,
    CancellingFleetRun,
    CompletedFleetRun,
    FailedFleetRun,
    FleetRunDetail,
    FleetRunProgress,
    FleetRunState,
    QueuedFleetRun,
    RunningFleetRun,
)
from oh_goon.core.models.run_admission import (
    FleetExecutionAdmission,
    PlanRepositoryAdmissionEvidence,
)
from oh_goon.core.models.scheduling import SchedulingEnvelope
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    FleetRunEvent,
    ProcessExitedEvent,
    OutputFinalizedEvent,
    OutputAbandonedEvent,
    OutputQuarantinedEvent,
    ProcessStartedEvent,
    TerminationRequestedEvent,
    ProcessStateUnknownEvent,
    ProcessStartingEvent,
    RepositoryCompletedEvent,
    RepositoryStartedEvent,
    StepAttemptCreatedEvent,
    StepCancelledEvent,
    StepFailedEvent,
    StepReachedEvent,
    StepSkippedEvent,
    StepStartedEvent,
    StepSucceededEvent,
    ReconciliationCompletedEvent,
    ReconciliationFailedEvent,
    ReconciliationRequiredEvent,
    ReconciliationStartedEvent,
    RunAdmittedEvent,
    RunCancelledEvent,
    RunCancelRequestedEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    StorageMaintenanceRecordedEvent,
)
from oh_goon.core.vocabulary import FleetRunStatus, Verdict
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceFact

OperationResolver = Callable[[str], OperationReport | None]

_TERMINAL_STATES = (CompletedFleetRun, CancelledFleetRun, FailedFleetRun)


@dataclass(slots=True)
class _RunFold:
    """One structurally valid FleetRun state plus its canonical completed operations."""

    state: FleetRunState
    execution: FleetExecutionAdmission
    repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...]
    application_launch: object
    scheduling: SchedulingEnvelope | None
    completed_reports: list[OperationReport] = field(default_factory=list)
    storage_maintenance: list[FleetContentMaintenanceFact] = field(default_factory=list)
    lifecycle_events: list[FleetRunEvent] = field(default_factory=list)

    @classmethod
    def from_admitted(cls, event: RunAdmittedEvent) -> _RunFold:
        state = QueuedFleetRun(
            fleet_run_id=event.fleet_run_id,
            source=event.source,
            target=event.target,
            capability=event.capability,
            repository_scope=event.repository_scope,
            targets=event.targets,
            accepted_at=event.generated_at,
            progress=FleetRunProgress(total=len(event.targets), succeeded=0, failed=0),
            status=FleetRunStatus.QUEUED,
        )
        return cls(
            state=state,
            execution=event.execution,
            repository_admissions=event.repository_admissions,
            application_launch=event.application_launch,
            scheduling=event.scheduling,
            lifecycle_events=[event],
        )

    def apply(self, event: FleetRunEvent, resolve_operation: OperationResolver) -> None:
        state = self.state
        if isinstance(event, RunAdmittedEvent):
            raise InvalidRunTransition(
                "Fleet run event stream can contain only one RUN_ADMITTED event"
            )
        reconciliation_events = (
            ReconciliationRequiredEvent,
            ReconciliationStartedEvent,
            ReconciliationCompletedEvent,
            ReconciliationFailedEvent,
        )
        if isinstance(state, _TERMINAL_STATES) and not isinstance(
            event, (ReconciliationCompletedEvent, ReconciliationFailedEvent)
        ):
            raise InvalidRunTransition("Fleet run lifecycle cannot continue after terminalization")
        if isinstance(event, reconciliation_events):
            self._apply_reconciliation_lifecycle(event)
            self.lifecycle_events.append(event)
            return
        if isinstance(
            event,
            (
                RepositoryStartedEvent,
                StepReachedEvent,
                StepStartedEvent,
                StepAttemptCreatedEvent,
                ProcessStartingEvent,
                OutputFinalizedEvent,
    OutputAbandonedEvent,
    OutputQuarantinedEvent,
                ProcessStartedEvent,
    TerminationRequestedEvent,
    ProcessStateUnknownEvent,
                ProcessExitedEvent,
    OutputFinalizedEvent,
    OutputAbandonedEvent,
    OutputQuarantinedEvent,
                DescendantsQuiescentEvent,
                StepSucceededEvent,
                StepFailedEvent,
                StepSkippedEvent,
                StepCancelledEvent,
            ),
        ):
            self._apply_execution_lifecycle(event)
            self.lifecycle_events.append(event)
            return
        if isinstance(event, RepositoryCompletedEvent):
            self._apply_repository_completion(event, resolve_operation)
            self.lifecycle_events.append(event)
            return
        if isinstance(event, StorageMaintenanceRecordedEvent):
            self._apply_storage_maintenance(event)
            self.lifecycle_events.append(event)
            return
        self.state = self._transition(event)
        self.lifecycle_events.append(event)

    def _apply_reconciliation_lifecycle(
        self,
        event: ReconciliationRequiredEvent
        | ReconciliationStartedEvent
        | ReconciliationCompletedEvent
        | ReconciliationFailedEvent,
    ) -> None:
        required = [
            item for item in self.lifecycle_events if isinstance(item, ReconciliationRequiredEvent)
        ]
        started = [
            item for item in self.lifecycle_events if isinstance(item, ReconciliationStartedEvent)
        ]
        settled = [
            item
            for item in self.lifecycle_events
            if isinstance(item, (ReconciliationCompletedEvent, ReconciliationFailedEvent))
        ]
        if isinstance(event, ReconciliationRequiredEvent):
            self._validate_reconciliation_required(event, required)
            return
        if isinstance(event, ReconciliationStartedEvent):
            if not required or started:
                raise InvalidRunTransition(
                    "RECONCILIATION_STARTED requires exactly one prior RECONCILIATION_REQUIRED"
                )
            return
        if not started or settled:
            raise InvalidRunTransition(
                f"{event.event_type.value} requires one unsettled RECONCILIATION_STARTED"
            )
        if not isinstance(self.state, _TERMINAL_STATES):
            raise InvalidRunTransition(
                f"{event.event_type.value} requires a terminal run disposition"
            )

    def _validate_reconciliation_required(self, event: ReconciliationRequiredEvent, required: list[ReconciliationRequiredEvent]) -> None:
        allowed = {repository_attempt_operation_id(self.state.fleet_run_id, target.repository_id) for target in self.state.targets}
        if len(set(event.operation_ids)) != len(event.operation_ids):
            raise InvalidRunTransition("Reconciliation operation identities must be unique")
        if not set(event.operation_ids).issubset(allowed):
            raise InvalidRunTransition("Reconciliation operation identities must belong to the owning fleet run")
        if required:
            raise InvalidRunTransition("Fleet run reconciliation can be required only once")
        if isinstance(self.state, _TERMINAL_STATES):
            raise InvalidRunTransition("Reconciliation must be required before run terminalization")

    def _apply_execution_lifecycle(self, event: FleetRunEvent) -> None:
        validate_execution_lifecycle(self.state, event, self.lifecycle_events)

    def _apply_storage_maintenance(self, event: StorageMaintenanceRecordedEvent) -> None:
        if not isinstance(self.state, (RunningFleetRun, CancellingFleetRun)):
            self._invalid_from(event)
        matching = [r for r in self.completed_reports if r.provenance.operation_id == event.operation_id]
        if not matching:
            raise InvalidRunTransition("Storage maintenance must follow its canonical operation")
        if matching[0].repository.repository_id != event.repository_id:
            raise InvalidRunTransition("Storage maintenance repository does not match canonical operation")
        if any(item.operation_id == event.operation_id for item in self.storage_maintenance):
            raise InvalidRunTransition("Storage maintenance is already recorded for operation")
        self.storage_maintenance.append(FleetContentMaintenanceFact(
            repository_id=event.repository_id, operation_id=event.operation_id, receipt=event.receipt
        ))

    def _apply_repository_completion(
        self,
        event: RepositoryCompletedEvent,
        resolve_operation: OperationResolver,
    ) -> None:
        state = self.state
        if not isinstance(state, (RunningFleetRun, CancellingFleetRun)):
            self._invalid_from(event)
        self._complete_repository(event, resolve_operation)
        self.state = type(state).model_validate(
            {**state.model_dump(mode="python"), "progress": self._progress()}
        )

    def _transition(self, event: FleetRunEvent) -> FleetRunState:
        state = self.state
        common = self._common()
        if isinstance(state, QueuedFleetRun) and isinstance(event, RunStartedEvent):
            return RunningFleetRun(
                **common, status=FleetRunStatus.RUNNING, started_at=event.generated_at
            )
        if isinstance(event, RunCancelRequestedEvent) and isinstance(
            state, (QueuedFleetRun, RunningFleetRun)
        ):
            return CancellingFleetRun(
                **common,
                status=FleetRunStatus.CANCELLING,
                started_at=state.started_at if isinstance(state, RunningFleetRun) else None,
                cancel_requested_at=event.generated_at,
            )
        if isinstance(state, RunningFleetRun) and isinstance(event, RunCompletedEvent):
            return self._completed_state(state, event)
        if isinstance(event, RunCancelledEvent) and isinstance(state, CancellingFleetRun):
            if self._live_processes():
                raise InvalidRunTransition(
                    "Fleet run cannot become RUN_CANCELLED while an owned physical process "
                    "has not reached DESCENDANTS_QUIESCENT"
                )
            return self._cancelled_state(state, event)
        if isinstance(event, RunFailedEvent) and isinstance(
            state, (QueuedFleetRun, RunningFleetRun, CancellingFleetRun)
        ):
            if self._unresolved_processes():
                raise InvalidRunTransition(
                    "Fleet run cannot become RUN_FAILED while an owned physical process "
                    "is neither quiescent nor explicitly PROCESS_STATE_UNKNOWN"
                )
            return self._failed_state(state, event)
        self._invalid_from(event)

    def _completed_state(self, state: RunningFleetRun, event: RunCompletedEvent) -> CompletedFleetRun:
        if len(self.completed_reports) != len(state.targets):
            raise InvalidRunTransition("Fleet run cannot complete before every captured target completes")
        return CompletedFleetRun(**self._common(), status=FleetRunStatus.COMPLETED, started_at=state.started_at, finished_at=event.generated_at)

    def _live_processes(self) -> list[ProcessStartedEvent]:
        quiet = {e.physical_execution_id for e in self.lifecycle_events if isinstance(e, DescendantsQuiescentEvent)}
        return [e for e in self.lifecycle_events if type(e) is ProcessStartedEvent and e.physical_execution_id not in quiet]

    def _unresolved_processes(self) -> list[ProcessStartedEvent]:
        settled = {e.physical_execution_id for e in self.lifecycle_events if isinstance(e, (DescendantsQuiescentEvent, ProcessStateUnknownEvent))}
        return [e for e in self.lifecycle_events if type(e) is ProcessStartedEvent and e.physical_execution_id not in settled]

    def _cancelled_state(
        self,
        state: QueuedFleetRun | RunningFleetRun | CancellingFleetRun,
        event: RunCancelledEvent,
    ) -> CancelledFleetRun:
        started_at = (
            state.started_at if isinstance(state, (RunningFleetRun, CancellingFleetRun)) else None
        )
        cancel_requested_at = (
            state.cancel_requested_at
            if isinstance(state, CancellingFleetRun)
            else event.generated_at
        )
        return CancelledFleetRun(
            **self._common(),
            status=FleetRunStatus.CANCELLED,
            started_at=started_at,
            cancel_requested_at=cancel_requested_at,
            finished_at=event.generated_at,
        )

    def _failed_state(
        self,
        state: QueuedFleetRun | RunningFleetRun | CancellingFleetRun,
        event: RunFailedEvent,
    ) -> FailedFleetRun:
        started_at = (
            state.started_at if isinstance(state, (RunningFleetRun, CancellingFleetRun)) else None
        )
        cancel_requested_at = (
            state.cancel_requested_at if isinstance(state, CancellingFleetRun) else None
        )
        return FailedFleetRun(
            **self._common(),
            status=FleetRunStatus.FAILED,
            started_at=started_at,
            cancel_requested_at=cancel_requested_at,
            finished_at=event.generated_at,
            failure=event.failure,
        )

    def _complete_repository(
        self, event: RepositoryCompletedEvent, resolve_operation: OperationResolver
    ) -> None:
        report = resolve_operation(event.operation_id)
        if report is None:
            raise InvalidRunTransition(
                f"Fleet event references unknown operation: {event.operation_id}"
            )
        self._validate_report(report)
        if not any(
            isinstance(existing, RepositoryStartedEvent)
            and existing.operation_id == event.operation_id
            and existing.repository_id == report.repository.repository_id
            for existing in self.lifecycle_events
        ):
            raise InvalidRunTransition(
                "Repository completion requires matching canonical REPOSITORY_STARTED"
            )
        self._validate_step_settlement(report)
        if any(
            item.provenance.operation_id == event.operation_id for item in self.completed_reports
        ):
            raise InvalidRunTransition(
                f"Operation already recorded in fleet run: {event.operation_id}"
            )
        if any(
            item.repository.repository_id == report.repository.repository_id
            for item in self.completed_reports
        ):
            raise InvalidRunTransition(
                f"Repository already completed in fleet run: {report.repository.repository_id}"
            )
        self.completed_reports.append(report)

    def _validate_step_settlement(self, report: OperationReport) -> None:
        if not isinstance(report.evidence, FleetPlanOperationEvidence):
            return
        operation_events = [
            event for event in self.lifecycle_events
            if getattr(event, "operation_id", None) == report.provenance.operation_id
        ]
        terminal_types = (StepSucceededEvent, StepFailedEvent, StepSkippedEvent, StepCancelledEvent)
        terminal_by_step = {
            event.step_id: event for event in operation_events if isinstance(event, terminal_types)
        }
        for order, execution in enumerate(report.evidence.plan.steps, 1):
            terminal = terminal_by_step.get(execution.step_id)
            if terminal is None:
                raise InvalidRunTransition(
                    f"Repository completion requires canonical terminal lifecycle for Step {execution.step_id}"
                )
            if terminal.step_order != order:
                raise InvalidRunTransition(
                    f"Canonical terminal lifecycle order does not match operation evidence for Step {execution.step_id}"
                )
            if not self._step_settlement_matches(execution, terminal):
                raise InvalidRunTransition(
                    f"Canonical terminal lifecycle does not match operation evidence for Step {execution.step_id}"
                )

    @staticmethod
    def _step_settlement_matches(execution, terminal) -> bool:
        if isinstance(execution, CompletedPlanStepExecution):
            return isinstance(terminal, StepSucceededEvent) and terminal.returncode == execution.returncode
        if isinstance(execution, FailedPlanStepExecution):
            return isinstance(terminal, StepFailedEvent) and terminal.returncode == execution.returncode
        return isinstance(execution, SkippedPlanStepExecution) and isinstance(terminal, StepSkippedEvent)

    def _validate_report(self, report: OperationReport) -> None:
        if report.provenance.fleet_run_id != self.state.fleet_run_id:
            raise InvalidRunTransition(
                "Operation report fleet_run_id does not match the owning fleet run"
            )
        if report.capability != self.state.capability:
            raise InvalidRunTransition(
                "Operation report capability snapshot does not match the owning fleet run"
            )
        if report.target != self.state.target:
            raise InvalidRunTransition(
                "Operation report target does not match the owning fleet run"
            )
        target = next(
            (
                item
                for item in self.state.targets
                if item.repository_id == report.repository.repository_id
            ),
            None,
        )
        if target is None or report.repository != target:
            raise InvalidRunTransition(
                "Operation report repository identity must match a captured target exactly"
            )
        try:
            validate_repository_attempt_operation_id(
                self.state.fleet_run_id,
                report.repository.repository_id,
                report.provenance.operation_id,
            )
        except ValueError as exc:
            raise InvalidRunTransition(str(exc)) from exc

    def _progress(self) -> FleetRunProgress:
        attention = sum(report.outcome.verdict.needs_attention for report in self.completed_reports)
        failed = sum(report.outcome.verdict is Verdict.FAILED for report in self.completed_reports)
        completed = len(self.completed_reports)
        return FleetRunProgress(
            total=len(self.state.targets),
            succeeded=completed - attention,
            failed=failed,
            needs_attention=attention,
        )

    def _common(self) -> dict[str, object]:
        return {
            "fleet_run_id": self.state.fleet_run_id,
            "source": self.state.source,
            "target": self.state.target,
            "capability": self.state.capability,
            "repository_scope": self.state.repository_scope,
            "targets": self.state.targets,
            "accepted_at": self.state.accepted_at,
            "progress": self._progress(),
        }

    def _invalid_from(self, event: FleetRunEvent) -> None:
        raise InvalidRunTransition(
            f"{event.event_type.value} is not valid from {self.state.status.value}"
        )

    def detail(self) -> FleetRunDetail:
        state = type(self.state).model_validate(
            {**self.state.model_dump(mode="python"), "progress": self._progress()}
        )
        return FleetRunDetail(
            run=state,
            execution=self.execution,
            repository_admissions=self.repository_admissions,
            application_launch=self.application_launch,
            scheduling=self.scheduling,
            operations=tuple(report.model_copy(deep=True) for report in self.completed_reports),
            lifecycle_events=tuple(self.lifecycle_events),
            storage_maintenance=tuple(self.storage_maintenance),
        )


def project_fleet_run_detail(
    events: tuple[FleetRunEvent, ...] | list[FleetRunEvent], resolve_operation: OperationResolver
) -> FleetRunDetail:
    """Reduce immutable Fleet facts directly between structurally valid lifecycle states."""
    if not events:
        raise InvalidRunTransition("Fleet run event stream cannot be empty")
    first = events[0]
    if not isinstance(first, RunAdmittedEvent):
        raise InvalidRunTransition("Fleet run event stream must begin with RUN_ADMITTED")
    fold = _RunFold.from_admitted(first)
    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence != expected_sequence:
            raise InvalidRunTransition(
                "Fleet run event sequences must be contiguous and start at 1"
            )
        if event.fleet_run_id != fold.state.fleet_run_id:
            raise InvalidRunTransition("Fleet run event stream cannot mix fleet_run_id values")
        if expected_sequence > 1:
            fold.apply(event, resolve_operation)
    return fold.detail()
