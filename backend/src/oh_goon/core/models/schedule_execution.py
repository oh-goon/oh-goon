from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.run_admission import ExplicitRepositoryScope
from oh_goon.core.models.schedule import ScheduleFailure, ScheduleOccurrenceRepositoryRun
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import (
    OperationAction,
    ScheduleExecutionStatus,
    ScheduleRepositoryRunStatus,
)


class ScheduledFleetRunIntent(ImmutableModel):
    """Exact Schedule generation and Fleet authority admitted for one occurrence."""

    definition_generation_id: str = Field(min_length=1)
    definition_record_version: int = Field(ge=0)
    capability: CapabilitySnapshot
    target: FleetPlanTarget
    repository_scope: ExplicitRepositoryScope
    repository_instance_ids: dict[str, str]

    @model_validator(mode="after")
    def validate_intent(self) -> ScheduledFleetRunIntent:
        if self.capability.action is not OperationAction.EXECUTE_PLAN:
            raise ValueError("Schedule execution must bind the Fleet Plan capability")
        if not self.capability.matches_intent(self.capability.action, self.target):
            raise ValueError("Schedule capability snapshot must match its Fleet Plan target")
        if not self.repository_scope.repository_ids:
            raise ValueError("Schedule execution must bind at least one repository")
        if set(self.repository_instance_ids) != set(self.repository_scope.repository_ids):
            raise ValueError("Schedule repository instance authority must exactly match scope")
        return self


class ScheduleAttention(ImmutableModel):
    repository_id: str = Field(min_length=1)
    reason_code: ReasonCode

    @property
    def identity(self) -> tuple[str, ReasonCode]:
        return self.repository_id, self.reason_code


class _ScheduleRunRecordBase(ImmutableModel):
    schedule_run_id: str = Field(min_length=1)
    schedule_id: str = Field(min_length=1)
    intent: ScheduledFleetRunIntent
    repository_runs: tuple[ScheduleOccurrenceRepositoryRun, ...] = Field(min_length=1)
    status: ScheduleExecutionStatus
    record_version: int = Field(default=0, ge=0)
    attention: tuple[ScheduleAttention, ...] = Field(default_factory=tuple)
    started_at: AwareDatetime

    @model_validator(mode="after")
    def validate_record(self) -> _ScheduleRunRecordBase:
        expected = self.intent.repository_scope.repository_ids
        actual = tuple(item.repository_id for item in self.repository_runs)
        if actual != expected:
            raise ValueError(
                "Schedule repository observations must match the admitted target set in canonical order"
            )
        identities = [item.identity for item in self.attention]
        if len(identities) != len(set(identities)):
            raise ValueError("Schedule attention observations must be unique")
        if self.status.terminal and any(not item.status.terminal for item in self.repository_runs):
            raise ValueError(
                "Terminal Schedule occurrence cannot retain pending/running repositories"
            )
        if self.status is ScheduleExecutionStatus.COMPLETED and self.attention:
            raise ValueError("Completed Schedule executions cannot retain attention observations")
        if self.status is ScheduleExecutionStatus.ATTENTION_REQUIRED and not self.attention:
            raise ValueError("Attention-required Schedule executions need exact attention evidence")
        return self

    @property
    def attention_count(self) -> int:
        return len(self.attention)


class RunningScheduleRun(_ScheduleRunRecordBase):
    status: Literal[ScheduleExecutionStatus.RUNNING]


class _TerminalScheduleRun(_ScheduleRunRecordBase):
    finished_at: AwareDatetime

    @model_validator(mode="after")
    def validate_terminal_time(self) -> _TerminalScheduleRun:
        if self.finished_at < self.started_at:
            raise ValueError("Schedule finished_at cannot precede started_at")
        return self


class CompletedScheduleRun(_TerminalScheduleRun):
    status: Literal[ScheduleExecutionStatus.COMPLETED]


class AttentionRequiredScheduleRun(_TerminalScheduleRun):
    status: Literal[ScheduleExecutionStatus.ATTENTION_REQUIRED]


class FailedScheduleRun(_TerminalScheduleRun):
    status: Literal[ScheduleExecutionStatus.FAILED]
    failure: ScheduleFailure


class CancelledScheduleRun(_TerminalScheduleRun):
    status: Literal[ScheduleExecutionStatus.CANCELLED]


type ScheduleRunRecord = Annotated[
    RunningScheduleRun
    | CompletedScheduleRun
    | AttentionRequiredScheduleRun
    | FailedScheduleRun
    | CancelledScheduleRun,
    Field(discriminator="status"),
]
_SCHEDULE_RUN_ADAPTER = TypeAdapter(ScheduleRunRecord)


def parse_schedule_run(value: object) -> ScheduleRunRecord:
    return _SCHEDULE_RUN_ADAPTER.validate_python(value)


def pending_repository_runs(
    intent: ScheduledFleetRunIntent,
) -> tuple[ScheduleOccurrenceRepositoryRun, ...]:
    return tuple(
        ScheduleOccurrenceRepositoryRun(
            repository_id=repository_id, status=ScheduleRepositoryRunStatus.PENDING
        )
        for repository_id in intent.repository_scope.repository_ids
    )
