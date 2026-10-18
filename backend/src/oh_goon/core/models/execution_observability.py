from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary import GameTapeStatus

ExecutionTimeBucket = Literal["day", "week", "month"]


class ExecutionStatusCount(ImmutableModel):
    """Count of one canonical Game Tape status inside an observation bucket."""

    status: GameTapeStatus
    count: int = Field(ge=1)


class ExecutionBucketWindow(ImmutableModel):
    """One canonical UTC bucket boundary shared by every repository row."""

    start_at: AwareDatetime
    end_at: AwareDatetime


class RepositoryExecutionBucket(ImmutableModel):
    """Observed execution facts for one repository in one time bucket."""

    start_at: AwareDatetime
    end_at: AwareDatetime
    run_count: int = Field(ge=1)
    statuses: tuple[ExecutionStatusCount, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts(self) -> RepositoryExecutionBucket:
        if sum(item.count for item in self.statuses) != self.run_count:
            raise ValueError("Execution observation status counts must equal run_count")
        if len({item.status for item in self.statuses}) != len(self.statuses):
            raise ValueError("Execution observation status counts must be unique")
        return self


class RepositoryExecutionTimelineRow(ImmutableModel):
    repository_id: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)
    buckets: tuple[RepositoryExecutionBucket, ...] = Field(default_factory=tuple)


class FleetExecutionTimeline(ImmutableModel):
    """Bounded read-only time projection over canonical Run/Game Tape facts."""

    journal_revision: int = Field(ge=0)
    from_at: AwareDatetime
    to_at: AwareDatetime
    bucket: ExecutionTimeBucket
    total_repositories: int = Field(ge=0)
    windows: tuple[ExecutionBucketWindow, ...] = Field(min_length=1, max_length=366)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[RepositoryExecutionTimelineRow, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_window(self) -> FleetExecutionTimeline:
        if self.to_at <= self.from_at:
            raise ValueError("Execution observation to_at must be after from_at")
        if len(self.items) > self.limit:
            raise ValueError("Execution observation page cannot exceed limit")
        return self
