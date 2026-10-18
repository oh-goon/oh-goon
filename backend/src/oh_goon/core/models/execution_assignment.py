from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.product_identity import stable_id
from oh_goon.core.models.base import ImmutableModel


class AttemptBudget(ImmutableModel):
    wall_time_seconds: int = Field(ge=1, le=86_400)
    max_processes: int | None = Field(default=None, ge=1)


class ExecutionAssignment(ImmutableModel):
    """Complete immutable authority resolved before an execution effect is allowed."""

    assignment_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    input_checkpoint_id: str = Field(min_length=1)
    filesystem_authority: Literal["read_only", "candidate_mutation"]
    process_authority: Literal["isolated"] = "isolated"
    network_authority: Literal["none", "declared"] = "none"
    secret_grants: tuple[str, ...] = ()
    git_authority: Literal["none", "read", "write"] = "none"
    publish_authority: bool = False
    tool_ids: tuple[str, ...] = ()
    delegation_limit: int = Field(default=0, ge=0, le=32)
    isolation_requirements: tuple[str, ...] = ("private_filesystem",)
    input_artifact_ids: tuple[str, ...] = ()
    attempt_budget: AttemptBudget
    assignment_fingerprint: str = Field(min_length=1)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _authority_separation(self) -> "ExecutionAssignment":
        if self.publish_authority and self.filesystem_authority == "candidate_mutation":
            raise ValueError("Candidate-mutation assignments cannot carry publish authority")
        if self.publish_authority and self.network_authority != "declared":
            raise ValueError("Publish authority requires declared network authority")
        if self.publish_authority and not self.input_artifact_ids:
            raise ValueError("Publish authority requires an exact proof artifact input")
        return self

    @model_validator(mode="after")
    def _fingerprint_matches_contract(self) -> "ExecutionAssignment":
        expected = execution_assignment_fingerprint(self)
        if self.assignment_fingerprint != expected:
            raise ValueError("assignment_fingerprint does not match immutable authority contract")
        return self


def execution_assignment_fingerprint(assignment: ExecutionAssignment) -> str:
    return stable_id(
        "execution-assignment",
        {
            "attempt_id": assignment.attempt_id,
            "workspace_id": assignment.workspace_id,
            "input_checkpoint_id": assignment.input_checkpoint_id,
            "filesystem_authority": assignment.filesystem_authority,
            "process_authority": assignment.process_authority,
            "network_authority": assignment.network_authority,
            "secret_grants": list(assignment.secret_grants),
            "git_authority": assignment.git_authority,
            "publish_authority": assignment.publish_authority,
            "tool_ids": list(assignment.tool_ids),
            "delegation_limit": assignment.delegation_limit,
            "isolation_requirements": list(assignment.isolation_requirements),
            "input_artifact_ids": list(assignment.input_artifact_ids),
            "attempt_budget": assignment.attempt_budget.model_dump(mode="json", exclude_computed_fields=True),
        },
    )
