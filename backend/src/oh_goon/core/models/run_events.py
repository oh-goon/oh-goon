from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.fleet_plan_evidence import PlanStepCancellationEvidence
from oh_goon.core.models.pi_session_storage import PiSessionWriterObservation
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    FleetRunFailure,
    FleetRunState,
)
from oh_goon.core.models.run_admission import (
    FleetExecutionAdmission,
    FleetRepositoryScope,
    PlanRepositoryAdmissionEvidence,
    _validate_execution_admission,
    _validate_execution_admission_run,
    _validate_plan_repository_admissions,
    _validate_trusted_local_admission,
    validate_fleet_repository_capture,
)
from oh_goon.core.models.run_launch import (
    ApplicationLaunchReceipt,
    UnmanagedApplicationLaunchReceipt,
)
from oh_goon.core.models.run_resources import (
    RunResourceEnvelopeEvidence,
    RunResourceEnvelopeObservation,
)
from oh_goon.core.models.scheduling import SchedulingEnvelope
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceReceipt
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunSource


class _FleetRunEventBase(ImmutableModel):
    sequence: int = Field(ge=1)
    fleet_run_id: str = Field(min_length=1)
    generated_at: AwareDatetime = Field(default_factory=utc_now)


class RunAdmittedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RUN_ADMITTED]
    source: FleetRunSource = FleetRunSource.MANUAL
    target: OperationTarget | None = None
    capability: CapabilitySnapshot
    repository_scope: FleetRepositoryScope
    targets: tuple[RepositoryIdentity, ...]
    execution: FleetExecutionAdmission
    repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...]
    application_launch: ApplicationLaunchReceipt = Field(
        default_factory=UnmanagedApplicationLaunchReceipt
    )
    scheduling: SchedulingEnvelope | None = None

    @model_validator(mode="after")
    def validate_admission(self) -> RunAdmittedEvent:
        if not self.capability.matches_intent(self.capability.action, self.target):
            raise ValueError("RUN_ADMITTED capability snapshot must match target kind")
        validate_fleet_repository_capture(self.repository_scope, self.targets, target=self.target)
        if self.repository_admissions:
            target_ids = tuple(sorted(item.repository_id for item in self.targets))
            evidence_ids = tuple(item.repository_id for item in self.repository_admissions)
            if evidence_ids != target_ids:
                raise ValueError(
                    "Repository/Plan admission evidence must match captured Fleet targets exactly"
                )
        _validate_execution_admission(self.target, self.execution)
        _validate_execution_admission_run(self.execution, self.fleet_run_id)
        _validate_trusted_local_admission(
            target=self.target,
            execution=self.execution,
            fleet_run_id=self.fleet_run_id,
            source=self.source,
            repository_scope=self.repository_scope,
            repository_count=len(self.targets),
        )
        _validate_plan_repository_admissions(self.target, self.repository_admissions)
        return self


class RunStartedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RUN_STARTED]


class RunResourcesObservedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RUN_RESOURCES_OBSERVED]
    observation: RunResourceEnvelopeObservation
    worker_resource_envelope: RunResourceEnvelopeEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_run_binding(self) -> RunResourcesObservedEvent:
        if self.observation.fleet_run_id != self.fleet_run_id:
            raise ValueError("Native resource observation must belong to the same Run")
        if self.worker_resource_envelope is not None and (
            self.worker_resource_envelope.subject != "worker"
            or self.worker_resource_envelope.fleet_run_id != self.fleet_run_id
            or self.worker_resource_envelope.unit_name != self.observation.unit_name
            or self.worker_resource_envelope.invocation_id != self.observation.invocation_id
        ):
            raise ValueError("Worker resource observation differs from its actual launch envelope")
        return self


class RepositoryStartedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.REPOSITORY_STARTED]
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)


class StepReachedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.STEP_REACHED]
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    step_order: int = Field(ge=1)


class StepStartedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.STEP_STARTED]
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    step_order: int = Field(ge=1)


class StepAttemptCreatedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.STEP_ATTEMPT_CREATED]
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    step_order: int = Field(ge=1)
    attempt_number: int = Field(ge=1)
    step_attempt_id: str = Field(min_length=1)


class PiSessionContinuationOrigin(ImmutableModel):
    """The exact prior Run and its observed writer cut, never a filesystem selector."""

    prior_run_id: str = Field(min_length=1, max_length=160)
    reservation_sequence: int = Field(ge=1)
    observation_sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def require_observed_reservation(self) -> PiSessionContinuationOrigin:
        if self.observation_sequence <= self.reservation_sequence:
            raise ValueError("Pi continuation observation must follow its reservation")
        return self


class PiSessionWriterReservedEvent(_FleetRunEventBase):
    """Original Run-owned writer fence for one admitted Pi session."""

    event_type: Literal[FleetRunEventType.PI_SESSION_WRITER_RESERVED]
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1, max_length=80)
    attempt_number: Literal[1]
    attempt_sequence: int = Field(ge=1)
    storage_id: str = Field(pattern=r"^pi-session-[0-9a-f]{64}$")
    lease_binding: str = Field(pattern=r"^pi-writer-[0-9a-f]{64}$")
    continuation: PiSessionContinuationOrigin | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class PiSessionWriterObservedEvent(_FleetRunEventBase):
    """Observation after exact native process quiescence, without reuse authority."""

    event_type: Literal[FleetRunEventType.PI_SESSION_WRITER_OBSERVED]
    reservation_sequence: int = Field(ge=1)
    quiescent_sequence: int = Field(ge=1)
    observation: PiSessionWriterObservation


class ProcessStartingEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.PROCESS_STARTING]
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    step_id: str | None = Field(default=None, min_length=1)
    step_order: int | None = Field(default=None, ge=1)
    attempt_number: int | None = Field(default=None, ge=1)
    step_attempt_id: str | None = Field(default=None, min_length=1)
    physical_execution_id: str = Field(min_length=1)
    workspace_materialization_id: str | None = Field(
        default=None, min_length=1, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def require_complete_process_scope(self) -> ProcessStartingEvent:
        scope = (self.step_id, self.step_order, self.attempt_number, self.step_attempt_id)
        if any(value is None for value in scope) and not all(value is None for value in scope):
            raise ValueError(
                "Physical process scope must be operation-only or one complete Step attempt"
            )
        return self


# These immutable Pydantic variants intentionally retain payload inheritance and
# isinstance ancestry while replacing the concrete wire discriminator. The narrow
# checker exceptions preserve that existing schema choice despite Literal invariance.
class ProcessStartedEvent(ProcessStartingEvent):
    event_type: Literal[FleetRunEventType.PROCESS_STARTED]  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]
    pid: int = Field(ge=1)
    process_identity: str | None = Field(default=None, min_length=1)


class TerminationRequestedEvent(ProcessStartedEvent):
    event_type: Literal[FleetRunEventType.TERMINATION_REQUESTED]  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]
    reason: str = Field(min_length=1)


class ProcessStateUnknownEvent(ProcessStartedEvent):
    event_type: Literal[FleetRunEventType.PROCESS_STATE_UNKNOWN]  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]
    reason: str = Field(min_length=1)


class OutputFinalizedEvent(ProcessStartingEvent):
    event_type: Literal[FleetRunEventType.OUTPUT_FINALIZED]  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]
    output_count: int = Field(ge=1)


class OutputAbandonedEvent(ProcessStartingEvent):
    event_type: Literal[FleetRunEventType.OUTPUT_ABANDONED]  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]
    reason: str = Field(min_length=1)


class OutputQuarantinedEvent(ProcessStartingEvent):
    event_type: Literal[FleetRunEventType.OUTPUT_QUARANTINED]  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]
    reason: str = Field(min_length=1)


class ProcessExitedEvent(ProcessStartedEvent):
    event_type: Literal[FleetRunEventType.PROCESS_EXITED]  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]
    returncode: int


class DescendantsQuiescentEvent(ProcessStartedEvent):
    event_type: Literal[FleetRunEventType.DESCENDANTS_QUIESCENT]  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]


class _StepTerminalEventBase(_FleetRunEventBase):
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    step_order: int = Field(ge=1)


class StepSucceededEvent(_StepTerminalEventBase):
    event_type: Literal[FleetRunEventType.STEP_SUCCEEDED]
    returncode: int


class StepFailedEvent(_StepTerminalEventBase):
    event_type: Literal[FleetRunEventType.STEP_FAILED]
    returncode: int


class StepSkippedEvent(_StepTerminalEventBase):
    event_type: Literal[FleetRunEventType.STEP_SKIPPED]


class StepCancelledEvent(_StepTerminalEventBase):
    event_type: Literal[FleetRunEventType.STEP_CANCELLED]
    cancellation: PlanStepCancellationEvidence | None = None


class RepositoryCompletedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.REPOSITORY_COMPLETED]
    operation_id: str = Field(min_length=1)


class StorageMaintenanceRecordedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.STORAGE_MAINTENANCE_RECORDED]
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    receipt: FleetContentMaintenanceReceipt


class RunCancelRequestedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RUN_CANCEL_REQUESTED]


class ReconciliationRequiredEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RECONCILIATION_REQUIRED]
    operation_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1)


class ReconciliationStartedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RECONCILIATION_STARTED]


class ReconciliationCompletedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RECONCILIATION_COMPLETED]


class ReconciliationFailedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RECONCILIATION_FAILED]
    reason: str = Field(min_length=1)


class RunCompletedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RUN_COMPLETED]


class RunCancelledEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RUN_CANCELLED]


class RunFailedEvent(_FleetRunEventBase):
    event_type: Literal[FleetRunEventType.RUN_FAILED]
    failure: FleetRunFailure


type FleetRunEvent = Annotated[
    RunAdmittedEvent
    | RunStartedEvent
    | RunResourcesObservedEvent
    | RepositoryStartedEvent
    | StepReachedEvent
    | StepStartedEvent
    | StepAttemptCreatedEvent
    | PiSessionWriterReservedEvent
    | PiSessionWriterObservedEvent
    | ProcessStartingEvent
    | ProcessStartedEvent
    | TerminationRequestedEvent
    | ProcessStateUnknownEvent
    | ProcessExitedEvent
    | OutputFinalizedEvent
    | OutputAbandonedEvent
    | OutputQuarantinedEvent
    | DescendantsQuiescentEvent
    | StepSucceededEvent
    | StepFailedEvent
    | StepSkippedEvent
    | StepCancelledEvent
    | RepositoryCompletedEvent
    | StorageMaintenanceRecordedEvent
    | RunCancelRequestedEvent
    | ReconciliationRequiredEvent
    | ReconciliationStartedEvent
    | ReconciliationCompletedEvent
    | ReconciliationFailedEvent
    | RunCompletedEvent
    | RunCancelledEvent
    | RunFailedEvent,
    Field(discriminator="event_type"),
]


class FleetRunEventBatch(ImmutableModel):
    run: FleetRunState
    events: tuple[FleetRunEvent, ...]
    next_sequence: int = Field(ge=0)
    journal_revision: int = Field(ge=0)
    has_more: bool = False

    @model_validator(mode="after")
    def validate_batch_lineage(self) -> FleetRunEventBatch:
        sequences = [event.sequence for event in self.events]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("Fleet event batches must contain unique ascending sequences")
        if any(event.fleet_run_id != self.run.fleet_run_id for event in self.events):
            raise ValueError("Fleet event batch events must belong to its projected run")
        if sequences and self.next_sequence < sequences[-1]:
            raise ValueError("Fleet event batch next_sequence cannot precede returned events")
        if self.has_more and (not sequences or self.next_sequence != sequences[-1]):
            raise ValueError("More event pages require an exact last-delivered cursor")
        return self
