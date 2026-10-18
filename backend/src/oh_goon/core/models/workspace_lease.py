from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel


class WorkspaceLease(ImmutableModel):
    lease_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    assignment_fingerprint: str = Field(min_length=1)
    executor_id: str = Field(min_length=1)
    executor_generation: int = Field(ge=1)
    input_checkpoint_id: str = Field(min_length=1)
    lease_generation: int = Field(ge=1)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    writer_authority_kind: Literal["cgroup", "process_group", "remote_backend"] | None = None
    writer_authority_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _writer_pair(self) -> "WorkspaceLease":
        if (self.writer_authority_kind is None) != (self.writer_authority_id is None):
            raise ValueError("writer authority kind/id must be present together")
        if self.expires_at <= self.issued_at:
            raise ValueError("workspace lease expiry must follow issuance")
        return self


class WriterQuiescenceEvidence(ImmutableModel):
    lease_id: str = Field(min_length=1)
    authority_kind: Literal["cgroup", "process_group", "remote_backend"]
    authority_id: str = Field(min_length=1)
    executor_id: str = Field(min_length=1)
    executor_generation: int = Field(ge=1)
    quiescent: bool
    observed_at: AwareDatetime
    evidence_id: str = Field(min_length=1)
