from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, computed_field, model_validator

from oh_goon.core.errors import ErrorKind, FleetDomainError
from oh_goon.core.models.agent_repair import AgentCampaignReadback
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.fleet_plan_trust_receipt import RepositoryFleetPlanTrustReceipt
from oh_goon.core.models.game_tape import (
    GameTape,
    GameTapeArtifactObservation,
    GameTapeProducerImplementationProvenance,
)
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.repository import RepositoryIdentity
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
from oh_goon.core.models.run_resources import RunResourceEnvelopeObservation
from oh_goon.core.models.scheduling import SchedulingEnvelope
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceFact
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.vocabulary import (
    FleetRunFailureStage,
    FleetRunOutcome,
    FleetRunSource,
    FleetRunStatus,
    OperationAction,
)


class FleetRunProgress(ImmutableModel):
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    needs_attention: int = Field(default=0, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completed(self) -> int:
        return self.succeeded + self.needs_attention

    @model_validator(mode="after")
    def validate_counts(self) -> FleetRunProgress:
        if self.completed > self.total:
            raise ValueError("Fleet progress completed cannot exceed total targets")
        if self.failed > self.needs_attention:
            raise ValueError("Fleet progress failed cannot exceed needs_attention")
        return self


class FleetRunFailure(ImmutableModel):
    """Structured durable failure fact for asynchronous fleet-run terminalization."""

    code: str = Field(min_length=1)
    kind: ErrorKind
    retryable: bool = False
    stage: FleetRunFailureStage
    message: str = Field(min_length=1)

    @classmethod
    def from_exception(
        cls, error: BaseException, *, stage: FleetRunFailureStage
    ) -> FleetRunFailure:
        if isinstance(error, FleetDomainError):
            return cls(
                code=error.code,
                kind=error.kind,
                retryable=error.retryable,
                stage=stage,
                message=str(error) or error.code,
            )
        return cls(
            code="UNEXPECTED_RUN_FAILURE",
            kind=ErrorKind.INTERNAL,
            retryable=False,
            stage=stage,
            message=str(error) or error.__class__.__name__,
        )

    @classmethod
    def recovery(cls, message: str) -> FleetRunFailure:
        return cls(
            code="RUN_INTERRUPTED_BY_RESTART",
            kind=ErrorKind.CONFLICT,
            retryable=True,
            stage=FleetRunFailureStage.RECOVERY,
            message=message,
        )

    @classmethod
    def uncertain_repository_effects(cls, attempt_count: int) -> FleetRunFailure:
        noun = "attempt" if attempt_count == 1 else "attempts"
        return cls(
            code="REPOSITORY_EFFECT_OUTCOME_UNKNOWN",
            kind=ErrorKind.CONFLICT,
            retryable=False,
            stage=FleetRunFailureStage.RECOVERY,
            message=(
                f"Application restarted with {attempt_count} repository {noun} that started but did not "
                "durably commit a result; automatic replay is blocked until the repositories are inspected"
            ),
        )

    @classmethod
    def partial_recovery(cls, *, completed: int, total: int) -> FleetRunFailure:
        return cls(
            code="RUN_PARTIALLY_COMMITTED_BY_RESTART",
            kind=ErrorKind.CONFLICT,
            retryable=False,
            stage=FleetRunFailureStage.RECOVERY,
            message=(
                f"Application restarted after {completed} of {total} repository operations were "
                "durably committed; automatic whole-run replay is blocked to avoid repeating effects"
            ),
        )


class _FleetRunStateBase(ImmutableModel):
    """Facts shared by every structurally valid FleetRun lifecycle state."""

    fleet_run_id: str = Field(min_length=1)
    source: FleetRunSource = FleetRunSource.MANUAL
    target: OperationTarget | None = None
    capability: CapabilitySnapshot
    repository_scope: FleetRepositoryScope
    targets: tuple[RepositoryIdentity, ...]
    accepted_at: AwareDatetime
    progress: FleetRunProgress

    @property
    def action(self) -> OperationAction:
        return self.capability.action

    @model_validator(mode="after")
    def validate_identity(self) -> _FleetRunStateBase:
        if not self.capability.matches_intent(self.capability.action, self.target):
            raise ValueError("Fleet run capability snapshot must match target kind")
        validate_fleet_repository_capture(self.repository_scope, self.targets, target=self.target)
        return self


class QueuedFleetRun(_FleetRunStateBase):
    status: Literal[FleetRunStatus.QUEUED]


class RunningFleetRun(_FleetRunStateBase):
    status: Literal[FleetRunStatus.RUNNING]
    started_at: AwareDatetime


class CancellingFleetRun(_FleetRunStateBase):
    status: Literal[FleetRunStatus.CANCELLING]
    started_at: AwareDatetime | None = None
    cancel_requested_at: AwareDatetime


class CompletedFleetRun(_FleetRunStateBase):
    status: Literal[FleetRunStatus.COMPLETED]
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @model_validator(mode="after")
    def validate_completion(self) -> CompletedFleetRun:
        if self.progress.completed != self.progress.total:
            raise ValueError("Completed FleetRun requires every captured target to complete")
        return self


class CancelledFleetRun(_FleetRunStateBase):
    status: Literal[FleetRunStatus.CANCELLED]
    started_at: AwareDatetime | None = None
    cancel_requested_at: AwareDatetime
    finished_at: AwareDatetime


class FailedFleetRun(_FleetRunStateBase):
    status: Literal[FleetRunStatus.FAILED]
    started_at: AwareDatetime | None = None
    cancel_requested_at: AwareDatetime | None = None
    finished_at: AwareDatetime
    failure: FleetRunFailure


type FleetRunState = Annotated[
    QueuedFleetRun
    | RunningFleetRun
    | CancellingFleetRun
    | CompletedFleetRun
    | CancelledFleetRun
    | FailedFleetRun,
    Field(discriminator="status"),
]


def fleet_run_outcome(run: FleetRunState) -> FleetRunOutcome:
    if run.status is FleetRunStatus.FAILED:
        return FleetRunOutcome.FAILED
    if run.status is FleetRunStatus.CANCELLED:
        return FleetRunOutcome.CANCELLED
    if not run.status.terminal:
        return FleetRunOutcome.IN_PROGRESS
    if run.progress.needs_attention > 0:
        return FleetRunOutcome.ATTENTION_REQUIRED
    return FleetRunOutcome.SUCCESS


class FleetRunDetail(ImmutableModel):
    """Trusted immutable read projection produced by the canonical FleetRun reducer."""

    journal_revision: int | None = Field(default=None, ge=0)
    campaign_journal: AgentCampaignReadback | None = None
    run: FleetRunState
    execution: FleetExecutionAdmission
    resource_observation: RunResourceEnvelopeObservation | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...]
    application_launch: ApplicationLaunchReceipt = Field(
        default_factory=UnmanagedApplicationLaunchReceipt
    )
    operations: tuple[OperationReport, ...] = Field(default_factory=tuple)
    lifecycle_events: tuple[object, ...] = Field(default_factory=tuple, exclude=True)
    storage_maintenance: tuple[FleetContentMaintenanceFact, ...] = Field(default_factory=tuple)
    scheduling: SchedulingEnvelope | None = None
    producer_implementation_provenance: GameTapeProducerImplementationProvenance | None = Field(
        default=None, exclude=True, repr=False
    )
    artifact_observation: GameTapeArtifactObservation | None = Field(
        default=None, exclude=True, repr=False
    )

    @model_validator(mode="after")
    def validate_admission_evidence(self) -> FleetRunDetail:
        _validate_execution_admission(self.run.target, self.execution)
        _validate_execution_admission_run(self.execution, self.run.fleet_run_id)
        _validate_trusted_local_admission(
            target=self.run.target,
            execution=self.execution,
            fleet_run_id=self.run.fleet_run_id,
            source=self.run.source,
            repository_scope=self.run.repository_scope,
            repository_count=len(self.run.targets),
        )
        _validate_plan_repository_admissions(self.run.target, self.repository_admissions)
        if self.repository_admissions:
            target_ids = tuple(sorted(item.repository_id for item in self.run.targets))
            evidence_ids = tuple(item.repository_id for item in self.repository_admissions)
            if evidence_ids != target_ids:
                raise ValueError(
                    "Repository/Plan admission evidence must match captured Fleet targets exactly"
                )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def outcome(self) -> FleetRunOutcome:
        return fleet_run_outcome(self.run)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rerun_supported(self) -> bool:
        """Project current new-work rerun policy without mutating durable run identity."""
        from oh_goon.core.capability_registry import capability_by_id
        from oh_goon.core.errors import CapabilityUnavailable

        if (
            isinstance(self.run.target, FleetPlanTarget)
            and self.run.target.agent_repair is not None
        ):
            return False
        try:
            return capability_by_id(self.run.capability.capability_id).rerun_supported
        except CapabilityUnavailable:
            return False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def game_tape(self) -> GameTape:
        from oh_goon.core.game_tape_projection import project_game_tape

        return project_game_tape(self)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def plan_trust_receipts(self) -> tuple[RepositoryFleetPlanTrustReceipt, ...]:
        from oh_goon.core.fleet_plan_trust_receipt import (
            project_repository_fleet_plan_trust_receipts,
        )

        return project_repository_fleet_plan_trust_receipts(self.operations)


class FleetRunOverview(ImmutableModel):
    """Aggregate canonical Fleet Run execution truth for the primary overview."""

    journal_revision: int = Field(ge=0)
    total: int = Field(ge=0)
    successful: int = Field(ge=0)
    failed: int = Field(ge=0)
    running: int = Field(ge=0)
    queued: int = Field(ge=0)
    cancelling: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    attention_required: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_partition(self) -> FleetRunOverview:
        if (
            self.successful
            + self.failed
            + self.running
            + self.queued
            + self.cancelling
            + self.cancelled
            + self.attention_required
            != self.total
        ):
            raise ValueError("Fleet run overview counts must partition total runs exactly")
        return self


class FleetRunFailureIdentity(ImmutableModel):
    """Exact first failure identity for collection-level drill-down."""

    repository_id: str | None = Field(default=None, min_length=1)
    repository_name: str | None = Field(default=None, min_length=1)
    goon_name: str | None = Field(default=None, min_length=1, max_length=160)
    step_id: str | None = Field(default=None, min_length=1, max_length=80)


class FleetRunHistoryItem(ImmutableModel):
    admitted_revision: int = Field(ge=1)
    run: FleetRunState
    execution: FleetExecutionAdmission
    failure_identity: FleetRunFailureIdentity | None = None

    @model_validator(mode="after")
    def validate_admission_evidence(self) -> FleetRunHistoryItem:
        _validate_execution_admission(self.run.target, self.execution)
        _validate_execution_admission_run(self.execution, self.run.fleet_run_id)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def outcome(self) -> FleetRunOutcome:
        return fleet_run_outcome(self.run)


class FleetRunHistoryPage(ImmutableModel):
    journal_revision: int = Field(ge=0)
    items: tuple[FleetRunHistoryItem, ...]
    next_before_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_history_order(self) -> FleetRunHistoryPage:
        revisions = [item.admitted_revision for item in self.items]
        if revisions != sorted(revisions, reverse=True) or len(revisions) != len(set(revisions)):
            raise ValueError("Fleet run history items must have unique descending queued revisions")
        if any(revision > self.journal_revision for revision in revisions):
            raise ValueError("Fleet run history cannot reference a future journal revision")
        if self.next_before_revision is not None:
            if not revisions or self.next_before_revision != revisions[-1]:
                raise ValueError("Fleet run history cursor must equal the oldest returned revision")
        return self
