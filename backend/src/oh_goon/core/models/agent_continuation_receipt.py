"""Passive continuation fences shared by Run targets and public transport models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.source_authority import RepositorySourceAuthority

AgentContinuationState = Literal[
    "awaiting_input", "stopped", "needs_review", "native_finished", "verified_success"
]


class AgentContinuationReview(ImmutableModel):
    """Small observation fences; none grants native effects or reuses a session."""

    campaign_id: str = Field(min_length=1, max_length=160)
    campaign_revision: int = Field(ge=1)
    decision_id: str = Field(min_length=1, max_length=160)
    previous_run_id: str = Field(min_length=1)
    previous_execution_digest: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    repository_instance_id: str = Field(min_length=1)
    source_authority: RepositorySourceAuthority
    implementation: Literal["codex", "opencode", "pi"]
    profile: str = Field(min_length=1, max_length=64)
    config_file: str | None = None
    selection_identity: str = Field(pattern=r"^agent-continuation-selection-[0-9a-f]{64}$")
    verification: tuple[tuple[str, ...], ...] = Field(default=(), max_length=16)
    verification_timeouts: tuple[float | None, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_verification_timeouts(self) -> AgentContinuationReview:
        import math

        if len(self.verification) != len(self.verification_timeouts) or any(
            value is not None and (not math.isfinite(value) or not 0 < value <= 21600)
            for value in self.verification_timeouts
        ):
            raise ValueError("Verification requires one valid native timeout per command")
        if self.source_authority.subject_instance_id != self.repository_instance_id:
            raise ValueError("Continuation source authority must identify the inspected subject")
        return self


class AgentContinuationReceipt(ImmutableModel):
    """Immutable lineage and exact-request identity retained on the admitted target."""

    request_identity: str = Field(pattern=r"^agent-continuation-request-[0-9a-f]{64}$")
    review: AgentContinuationReview
    feedback_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
