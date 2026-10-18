from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.errors import ErrorKind
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import (
    ScheduleExecutionStatus,
    ScheduleFailureStage,
    ScheduleRepositoryRunStatus,
    ScheduleState,
)


class _ScheduleTargetSet(ImmutableModel):
    repository_ids: tuple[str, ...] = Field(min_length=1)
    repository_instance_ids: dict[str, str]

    @model_validator(mode="after")
    def validate_repository_ids(self) -> _ScheduleTargetSet:
        if any(not item for item in self.repository_ids):
            raise ValueError("Schedule repository IDs must be non-empty")
        if len(self.repository_ids) != len(set(self.repository_ids)):
            raise ValueError("Schedule repository targets must be unique")
        if self.repository_ids != tuple(sorted(self.repository_ids)):
            raise ValueError("Schedule repository targets must be canonically sorted")
        if set(self.repository_instance_ids) != set(self.repository_ids):
            raise ValueError("Schedule repository instance authority must exactly match targets")
        if any(not value for value in self.repository_instance_ids.values()):
            raise ValueError("Schedule repository instance IDs must be non-empty")
        return self


class ScheduleCreate(_ScheduleTargetSet):
    """Time trigger transporting one already-verified executable target."""

    name: str = Field(min_length=1, max_length=80)
    target: FleetPlanTarget
    interval_minutes: int = Field(ge=1, le=10080)
    state: ScheduleState = ScheduleState.ACTIVE


class ScheduleReplace(ScheduleCreate):
    generation_id: str = Field(min_length=1)
    record_version: int = Field(ge=0)


class ScheduleDeleteRequest(ImmutableModel):
    generation_id: str = Field(min_length=1)
    record_version: int = Field(ge=0)


class ScheduleDefinition(_ScheduleTargetSet):
    schedule_id: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    record_version: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=80)
    target: FleetPlanTarget
    interval_minutes: int = Field(ge=1, le=10080)
    state: ScheduleState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ScheduleFailure(ImmutableModel):
    code: str
    kind: ErrorKind
    retryable: bool = False
    stage: ScheduleFailureStage
    message: str

    @classmethod
    def from_exception(
        cls, error: BaseException, *, stage: ScheduleFailureStage
    ) -> ScheduleFailure:
        from oh_goon.core.errors import FleetDomainError

        if isinstance(error, FleetDomainError):
            return cls(
                code=error.code,
                kind=error.kind,
                retryable=error.retryable,
                stage=stage,
                message=str(error) or error.code,
            )
        return cls(
            code="UNEXPECTED_SCHEDULE_FAILURE",
            kind=ErrorKind.INTERNAL,
            stage=stage,
            message=str(error) or error.__class__.__name__,
        )


class ScheduleOccurrenceRepositoryRun(ImmutableModel):
    """One repository target in an occurrence; actual Run identity exists only after admission."""

    repository_id: str = Field(min_length=1)
    status: ScheduleRepositoryRunStatus
    fleet_run_id: str | None = Field(default=None, min_length=1)
    attention_count: int = Field(default=0, ge=0)
    failure: ScheduleFailure | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> ScheduleOccurrenceRepositoryRun:
        if self.status is ScheduleRepositoryRunStatus.PENDING and self.fleet_run_id is not None:
            raise ValueError("Pending Schedule targets cannot claim an admitted Fleet Run")
        if self.status is ScheduleRepositoryRunStatus.NOT_STARTED:
            if self.fleet_run_id is not None or self.failure is None:
                raise ValueError(
                    "Not-started Schedule targets require failure evidence and no Fleet Run"
                )
        elif self.status is ScheduleRepositoryRunStatus.FAILED:
            if self.fleet_run_id is None or self.failure is None:
                raise ValueError(
                    "Failed repository Runs require Fleet Run identity and failure evidence"
                )
        elif self.status is not ScheduleRepositoryRunStatus.PENDING and self.fleet_run_id is None:
            raise ValueError("Started Schedule repository targets require Fleet Run identity")
        elif self.failure is not None:
            raise ValueError(
                "Only failed/not-started Schedule targets may contain failure evidence"
            )
        return self


class ScheduleOccurrence(ImmutableModel):
    """Aggregate/context for one Schedule firing; repository Runs remain canonical execution truth."""

    schedule_run_id: str = Field(min_length=1)
    schedule_id: str = Field(min_length=1)
    expected: int = Field(ge=1)
    successful: int = Field(ge=0)
    failed: int = Field(ge=0)
    attention_required: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    running: int = Field(ge=0)
    pending: int = Field(ge=0)
    not_started: int = Field(ge=0)
    status: ScheduleExecutionStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    attention_count: int = Field(default=0, ge=0)
    failure: ScheduleFailure | None = None

    @model_validator(mode="after")
    def validate_partition(self) -> ScheduleOccurrence:
        total = (
            self.successful
            + self.failed
            + self.attention_required
            + self.cancelled
            + self.running
            + self.pending
            + self.not_started
        )
        if total != self.expected:
            raise ValueError(
                "Schedule occurrence counts must partition expected repository targets"
            )
        return self


class ScheduleOccurrenceRepositoryPage(ImmutableModel):
    schedule_run_id: str = Field(min_length=1)
    schedule_id: str = Field(min_length=1)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[ScheduleOccurrenceRepositoryRun, ...] = Field(
        default_factory=tuple, max_length=100
    )


class ScheduleStatus(ImmutableModel):
    definition: ScheduleDefinition
    latest_occurrence: ScheduleOccurrence | None = None
    next_run_at: AwareDatetime | None = None
    missing_expected_occurrence_at: AwareDatetime | None = None


class ScheduleCatalog(ImmutableModel):
    schedules: tuple[ScheduleStatus, ...] = Field(default_factory=tuple)
