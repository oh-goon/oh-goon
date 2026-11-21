"""Explicit fresh-Run continuation intent and its original Journal receipt."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.models.agent_continuation_receipt import (
    AgentContinuationReceipt as AgentContinuationReceipt,
)
from oh_goon.core.models.agent_continuation_receipt import (
    AgentContinuationReview as AgentContinuationReview,
)
from oh_goon.core.models.agent_continuation_receipt import AgentContinuationState
from oh_goon.core.models.agent_controls import NativeAgentControlMode
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_feedback import RunFeedback
from oh_goon.core.models.source_authority import RepositorySourceAuthority

MAX_CONTINUATION_FEEDBACK_BYTES = 8192


class AgentContinuationDiff(ImmutableModel):
    availability: Literal["available", "unavailable"]
    text: str = Field(default="", max_length=65536)
    truncated: bool = False
    byte_count: int | None = Field(default=None, ge=0)
    omitted_bytes: int | None = Field(default=None, ge=0)
    reason: str | None = None
    baseline: Literal["last_captured_candidate"] = "last_captured_candidate"


class AgentContinuationSourceInspection(ImmutableModel):
    source_authority: RepositorySourceAuthority
    repository_instance_id: str
    diff: AgentContinuationDiff
    recorded_candidate_identity: str
    original_delivery_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentContinuationSelection(ImmutableModel):
    implementation: Literal["codex", "opencode", "pi"]
    profile: str
    mode: NativeAgentControlMode
    selected_backend: Literal["local", "bubblewrap"]
    sandbox_owner: Literal["harness", "execution_backend"]
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_identity: str
    environment_qualification: Literal["unqualified"] = "unqualified"


class AgentContinuationInspection(ImmutableModel):
    state: AgentContinuationState
    campaign_id: str
    previous_run_id: str
    repository: RepositoryIdentity
    selection: AgentContinuationSelection
    feedback: RunFeedback
    source: AgentContinuationSourceInspection
    review: AgentContinuationReview
    verification_status: Literal["available", "unavailable"]
    verification_reason: str
    can_continue: bool
    refusal_reasons: tuple[str, ...] = ()


class AgentContinuationRequest(ImmutableModel):
    new_fleet_run_id: str = Field(min_length=1, max_length=160)
    review: AgentContinuationReview
    feedback: str = Field(min_length=1, max_length=MAX_CONTINUATION_FEEDBACK_BYTES)
    authorize: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_explicit_feedback(self) -> AgentContinuationRequest:
        try:
            encoded = self.feedback.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("Continuation feedback must be UTF-8") from exc
        if (
            not self.feedback.strip()
            or "\x00" in self.feedback
            or len(encoded) > MAX_CONTINUATION_FEEDBACK_BYTES
        ):
            raise ValueError("Continuation feedback requires 1 to 8192 UTF-8 bytes without NUL")
        if self.authorize is not True:
            raise ValueError("A fresh continuation requires explicit authorization")
        return self


class AgentContinuationResult(ImmutableModel):
    fleet_run_id: str
    receipt: AgentContinuationReceipt
    acknowledgment: Literal["admitted", "existing"]
