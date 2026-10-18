from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.game_tape_failure_relation import GameTapeFailureSignatureRelation
from oh_goon.core.models.game_tape_failure_transition import (
    GameTapeFailureSequenceTransition,
    GameTapeFailureTransitionSummary,
    GameTapeSelectedContextWindow,
)
from oh_goon.core.models.game_tape_reproduction_context import (
    GameTapeReproductionContextClassification,
    GameTapeReproductionContextComparison,
)
from oh_goon.core.vocabulary import GameTapeStatus

GameTapeFailureSequencePosition = Literal["PRIOR", "SELECTED", "LATER"]


class GameTapeFailureSequenceEntry(ImmutableModel):
    """One comparable chronological step outcome around the selected failure."""

    fleet_run_id: str = Field(min_length=1)
    accepted_at: AwareDatetime
    position: GameTapeFailureSequencePosition
    status: GameTapeStatus
    duration_ms: int | None = Field(default=None, ge=0)
    signature_relation: GameTapeFailureSignatureRelation
    context: GameTapeReproductionContextComparison


class GameTapeFailureSequenceSummary(ImmutableModel):
    """Exact sequence facts; deliberately no trend, cause, confidence or recovery label."""

    prior_comparable_runs: int = Field(ge=0)
    later_comparable_runs: int = Field(ge=0)
    sampled_prior_runs: int = Field(ge=0, le=20)
    sampled_later_runs: int = Field(ge=0, le=20)
    complete_context_entries: int = Field(ge=0)
    incomplete_context_entries: int = Field(ge=0)
    later_same_context_passes: int = Field(ge=0)
    later_same_context_exact_failures: int = Field(ge=0)
    first_later_same_context_pass_run_id: str | None = Field(default=None, min_length=1)
    exact_failure_recurred_in_same_context_after_same_context_pass: bool = False
    latest_comparable_run_id: str = Field(min_length=1)
    latest_comparable_status: GameTapeStatus
    latest_signature_relation: GameTapeFailureSignatureRelation
    latest_context_classification: GameTapeReproductionContextClassification


class GameTapeFailureSequence(ImmutableModel):
    """Bounded chronological window over comparable durable Game Tape step facts."""

    sample_limit: int = Field(ge=1, le=20)
    sample_rule: Literal["NEAREST_COMPARABLE_SAME_PLAN_REPOSITORY_STEP"] = (
        "NEAREST_COMPARABLE_SAME_PLAN_REPOSITORY_STEP"
    )
    summary: GameTapeFailureSequenceSummary
    selected_context_window: GameTapeSelectedContextWindow
    transition_summary: GameTapeFailureTransitionSummary
    entries: tuple[GameTapeFailureSequenceEntry, ...] = Field(min_length=1, max_length=41)
    transitions: tuple[GameTapeFailureSequenceTransition, ...] = Field(
        default_factory=tuple, max_length=40
    )
