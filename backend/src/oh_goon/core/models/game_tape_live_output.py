from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary import GameTapeFailureNavigationState


class GameTapeLiveOutputChunk(ImmutableModel):
    """One bounded, process-local output fragment; never written to the Journal."""

    sequence: int = Field(ge=1)
    repository_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1, max_length=80)
    attempt: int = Field(ge=1, le=5)
    stream: Literal["stdout", "stderr"]
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    text: str = Field(max_length=16384)
    emitted_at: AwareDatetime

    @model_validator(mode="after")
    def validate_lines(self) -> GameTapeLiveOutputChunk:
        if self.line_end < self.line_start:
            raise ValueError("Live output line_end cannot precede line_start")
        return self


class GameTapeLiveFailureAnchor(ImmutableModel):
    """Backend-owned navigation target into the currently retained transient stream."""

    step_execution_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1, max_length=80)
    attempt: int = Field(ge=1, le=5)
    stream: Literal["stdout", "stderr"]
    line: int = Field(ge=1)
    sequence: int | None = Field(default=None, ge=1)
    retained: bool

    @model_validator(mode="after")
    def validate_retention(self) -> GameTapeLiveFailureAnchor:
        if self.retained != (self.sequence is not None):
            raise ValueError("Live failure anchor retained must match sequence availability")
        return self


class GameTapeLiveOutputBatch(ImmutableModel):
    fleet_run_id: str = Field(min_length=1)
    state: Literal["ACTIVE", "SEALED", "UNAVAILABLE"]
    available: bool
    expires_at: AwareDatetime | None = None
    chunks: tuple[GameTapeLiveOutputChunk, ...] = Field(default_factory=tuple, max_length=2000)
    failure_anchors: tuple[GameTapeLiveFailureAnchor, ...] = Field(default_factory=tuple)
    failure_navigation_state: GameTapeFailureNavigationState = (
        GameTapeFailureNavigationState.AVAILABLE
    )
    latest_activity: str | None = Field(default=None, max_length=512)
    activity_sequence: int = Field(default=0, ge=0)
    next_sequence: int = Field(ge=0)
    retained_from_sequence: int = Field(ge=0)
    dropped_chunk_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_state(self) -> GameTapeLiveOutputBatch:
        if self.available != (self.state != "UNAVAILABLE"):
            raise ValueError("Live output available must match state")
        if self.state == "SEALED" and self.expires_at is None:
            raise ValueError("SEALED live output requires expires_at")
        if self.state != "SEALED" and self.expires_at is not None:
            raise ValueError("Only SEALED live output can expose expires_at")
        if (
            self.state == "UNAVAILABLE"
            and self.failure_navigation_state is not GameTapeFailureNavigationState.UNAVAILABLE
        ):
            raise ValueError("Unavailable live output must mark failure navigation unavailable")
        if (
            self.state != "UNAVAILABLE"
            and self.failure_navigation_state is GameTapeFailureNavigationState.UNAVAILABLE
        ):
            raise ValueError("Available live output cannot mark failure navigation unavailable")
        return self
