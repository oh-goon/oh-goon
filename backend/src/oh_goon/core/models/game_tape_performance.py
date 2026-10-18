from __future__ import annotations

from typing import Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary import GameTapeStatus

GameTapePerformanceDirection = Literal["NO_BASELINE", "FASTER", "SAME", "SLOWER"]


class GameTapeDurationBaseline(ImmutableModel):
    """Transparent timing baseline from prior comparable durable executions."""

    sample_count: int = Field(ge=0, le=20)
    sample_run_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    median_duration_ms: float | None = Field(default=None, ge=0)
    minimum_duration_ms: int | None = Field(default=None, ge=0)
    maximum_duration_ms: int | None = Field(default=None, ge=0)


class GameTapeDurationComparison(ImmutableModel):
    current_duration_ms: int | None = Field(default=None, ge=0)
    baseline: GameTapeDurationBaseline
    delta_ms: float | None = None
    delta_percent: int | None = None
    direction: GameTapePerformanceDirection


class GameTapeStepPerformance(ImmutableModel):
    step_execution_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1, max_length=80)
    status: GameTapeStatus
    timing: GameTapeDurationComparison


class GameTapeRepositoryPerformance(ImmutableModel):
    repository_execution_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)
    timing: GameTapeDurationComparison
    largest_slowdown_step_id: str | None = Field(default=None, min_length=1, max_length=80)
    largest_slowdown_percent: int | None = None
    steps: tuple[GameTapeStepPerformance, ...] = Field(default_factory=tuple)


class GameTapePerformanceReport(ImmutableModel):
    """On-demand arithmetic over prior Game Tape facts; no metrics store is created."""

    fleet_run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    sample_limit: int = Field(ge=1, le=20)
    history_candidates_considered: int = Field(ge=0)
    comparable_plan_runs: int = Field(ge=0)
    previous_comparable_run_id: str | None = Field(default=None, min_length=1)
    baseline_rule: Literal["SAME_PLAN_SUCCESSFUL_TIMING"] = "SAME_PLAN_SUCCESSFUL_TIMING"
    run_timing: GameTapeDurationComparison
    repositories: tuple[GameTapeRepositoryPerformance, ...] = Field(default_factory=tuple)
