from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.game_tape_failure_sequence import GameTapeFailureSequence
from oh_goon.core.models.game_tape_reproduction_context import (
    GameTapeFailureContextCounts,
    GameTapeReproductionContextComparison,
)


class GameTapeFailureOccurrence(ImmutableModel):
    fleet_run_id: str = Field(min_length=1)
    accepted_at: AwareDatetime
    repository_id: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)
    step_id: str = Field(min_length=1, max_length=80)
    duration_ms: int | None = Field(default=None, ge=0)
    attempt_count: int = Field(ge=0, le=5)
    failure_attempt: int | None = Field(default=None, ge=1, le=5)
    exit_code: int
    reason_code: str | None = Field(default=None, min_length=1, max_length=120)
    signature_id: str = Field(min_length=1)
    evidence_stream: Literal["stdout", "stderr"] | None = None
    evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    context: GameTapeReproductionContextComparison


class GameTapeLaterPass(ImmutableModel):
    """A later comparable run where the same admitted step has canonical PASSED status."""

    fleet_run_id: str = Field(min_length=1)
    accepted_at: AwareDatetime
    duration_ms: int | None = Field(default=None, ge=0)
    context: GameTapeReproductionContextComparison


class GameTapeFailureEvolution(ImmutableModel):
    """Exact recurrence/recovery facts across the currently available Journal read cut."""

    exact_occurrence_count: int = Field(ge=1)
    prior_exact_occurrences: int = Field(ge=0)
    later_exact_occurrences: int = Field(ge=0)
    first_seen_run_id: str = Field(min_length=1)
    first_seen_at: AwareDatetime
    last_seen_run_id: str = Field(min_length=1)
    last_seen_at: AwareDatetime
    later_comparable_runs: int = Field(ge=0)
    first_later_pass: GameTapeLaterPass | None = None
    exact_failure_recurred_after_pass: bool = False
    prior_exact_contexts: GameTapeFailureContextCounts
    later_exact_contexts: GameTapeFailureContextCounts
    later_pass_contexts: GameTapeFailureContextCounts


class GameTapeFailureHistory(ImmutableModel):
    """Exact-signature history derived on demand from canonical Game Tape facts."""

    fleet_run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1, max_length=80)
    signature_id: str = Field(min_length=1)
    sample_limit: int = Field(ge=1, le=20)
    history_candidates_considered: int = Field(ge=0)
    same_plan_step_failures: int = Field(ge=0)
    exact_match_rule: Literal["SAME_PLAN_REPOSITORY_STEP_SIGNATURE"] = (
        "SAME_PLAN_REPOSITORY_STEP_SIGNATURE"
    )
    evolution: GameTapeFailureEvolution
    sequence: GameTapeFailureSequence
    occurrences: tuple[GameTapeFailureOccurrence, ...] = Field(default_factory=tuple, max_length=20)
