from __future__ import annotations

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.game_tape_failure_relation import GameTapeFailureSignatureRelation
from oh_goon.core.models.game_tape_reproduction_context import (
    GameTapeReproductionContextClassification,
    GameTapeReproductionContextComparison,
)
from oh_goon.core.vocabulary import GameTapeStatus


class GameTapeFailureSequenceTransition(ImmutableModel):
    """Exact adjacent-run changes inside the bounded comparable failure sequence."""

    from_run_id: str = Field(min_length=1)
    to_run_id: str = Field(min_length=1)
    from_status: GameTapeStatus
    to_status: GameTapeStatus
    status_changed: bool
    from_signature_relation: GameTapeFailureSignatureRelation
    to_signature_relation: GameTapeFailureSignatureRelation
    signature_relation_changed: bool
    duration_delta_ms: int | None = None
    context: GameTapeReproductionContextComparison


class GameTapeFailureTransitionSummary(ImmutableModel):
    """Counts over visible adjacent transitions only; no causal interpretation."""

    sampled_transition_count: int = Field(ge=0, le=40)
    status_change_count: int = Field(ge=0, le=40)
    same_context_status_changes: int = Field(ge=0, le=40)
    changed_context_status_changes: int = Field(ge=0, le=40)
    incomplete_context_status_changes: int = Field(ge=0, le=40)
    signature_relation_change_count: int = Field(ge=0, le=40)


class GameTapeSelectedContextWindow(ImmutableModel):
    """Contiguous comparable runs nearest the selection that match its complete context."""

    prior_same_context_runs: int = Field(ge=0)
    later_same_context_runs: int = Field(ge=0)
    total_same_context_runs_including_selected: int = Field(ge=1)
    nearest_prior_boundary_run_id: str | None = Field(default=None, min_length=1)
    nearest_prior_boundary_classification: GameTapeReproductionContextClassification | None = None
    nearest_later_boundary_run_id: str | None = Field(default=None, min_length=1)
    nearest_later_boundary_classification: GameTapeReproductionContextClassification | None = None
