from __future__ import annotations

from pydantic import AwareDatetime, Field, computed_field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary import GameTapeStatus


class GameTapeRepositoryStatusCount(ImmutableModel):
    status: GameTapeStatus
    count: int = Field(ge=1)


class GameTapeTeamFailureFocus(ImmutableModel):
    repository_execution_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)
    step_execution_id: str | None = Field(default=None, min_length=1)
    step_id: str | None = Field(default=None, min_length=1, max_length=80)
    occurred_at: AwareDatetime | None = None


class GameTapeTeamComposition(ImmutableModel):
    """Run-wide repository composition projected only from canonical execution facts."""

    repository_count: int = Field(ge=0)
    timed_repository_count: int = Field(ge=0)
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    observed_peak_concurrency: int = Field(ge=0)
    overlapping_repository_execution_ids: tuple[str, ...] = Field(default_factory=tuple)

    @computed_field
    @property
    def duration_ms(self) -> int | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return max(0, int((self.finished_at - self.started_at).total_seconds() * 1000))

    @computed_field
    @property
    def overlap_observed(self) -> bool:
        return bool(self.overlapping_repository_execution_ids)
    status_counts: tuple[GameTapeRepositoryStatusCount, ...] = Field(default_factory=tuple)
    first_failure: GameTapeTeamFailureFocus | None = None
