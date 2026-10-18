from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel

GameTapeEvidenceMatch = Literal["MATCH", "DIFFERENT", "UNKNOWN"]
GameTapeReproductionContextClassification = Literal[
    "SAME_CONTEXT",
    "CHANGED_CONTEXT",
    "INCOMPLETE_CONTEXT",
]


class GameTapeReproductionContextSummary(ImmutableModel):
    """One backend-owned interpretation of the five recorded reproduction dimensions."""

    classification: GameTapeReproductionContextClassification
    known_dimensions: int = Field(ge=0, le=5)
    matching_dimensions: int = Field(ge=0, le=5)
    different_dimensions: int = Field(ge=0, le=5)
    total_dimensions: Literal[5] = 5

    @model_validator(mode="after")
    def validate_counts(self) -> GameTapeReproductionContextSummary:
        if self.known_dimensions != self.matching_dimensions + self.different_dimensions:
            raise ValueError("Known reproduction dimensions must equal matching + different")
        if self.classification == "SAME_CONTEXT" and (
            self.matching_dimensions != 5 or self.different_dimensions != 0
        ):
            raise ValueError("SAME_CONTEXT requires five matching dimensions")
        if self.classification == "CHANGED_CONTEXT" and self.different_dimensions == 0:
            raise ValueError("CHANGED_CONTEXT requires at least one different dimension")
        if self.classification == "INCOMPLETE_CONTEXT" and (
            self.known_dimensions == 5 or self.different_dimensions != 0
        ):
            raise ValueError(
                "INCOMPLETE_CONTEXT requires unknown dimensions and no recorded differences"
            )
        return self


class GameTapeReproductionContextComparison(ImmutableModel):
    """Deterministic identity comparison; UNKNOWN never becomes an inferred match."""

    workspace: GameTapeEvidenceMatch
    runtime: GameTapeEvidenceMatch
    toolchain: GameTapeEvidenceMatch
    isolation: GameTapeEvidenceMatch
    command: GameTapeEvidenceMatch
    summary: GameTapeReproductionContextSummary


class GameTapeFailureContextCounts(ImmutableModel):
    same_context: int = Field(ge=0)
    changed_context: int = Field(ge=0)
    incomplete_context: int = Field(ge=0)
