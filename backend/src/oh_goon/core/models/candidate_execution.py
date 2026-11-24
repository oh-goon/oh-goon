"""Passive source and workspace admission for an explicit fresh candidate Run."""

from __future__ import annotations

import stat
from typing import Annotated

from pydantic import Field, WithJsonSchema, field_serializer, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.workspace import CandidateCheckpoint, WorkspaceRef
from oh_goon.core.vocabulary.workspace import WorkspaceKind


class CandidateWorkspaceAdmission(ImmutableModel):
    """Bind registered source to a real owned initial candidate, without granting a writer."""

    fleet_run_id: str = Field(min_length=1)
    parallel_work_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$",
        exclude_if=lambda value: value is None,
    )
    source: RepositorySourceAuthority
    source_workspace: WorkspaceRef
    workspace: WorkspaceRef
    checkpoint: CandidateCheckpoint
    state_version: int = Field(ge=1)
    workspace_device: int = Field(ge=0)
    workspace_inode: int = Field(ge=1)
    workspace_mode: int = Field(ge=0)
    workspace_ctime_ns: Annotated[
        int, WithJsonSchema({"type": "string", "pattern": "^[0-9]+$"})
    ] = Field(ge=0)

    @field_serializer("workspace_ctime_ns", when_used="json")
    def serialize_ctime(self, value: int) -> str:
        """Keep the original nanosecond token exact in JavaScript/API transport."""
        return str(value)

    @model_validator(mode="after")
    def validate_initial_candidate(self) -> CandidateWorkspaceAdmission:
        if (
            self.source_workspace.kind is not WorkspaceKind.READ_SNAPSHOT
            or self.workspace.kind is not WorkspaceKind.WORKTREE
            or self.source_workspace.repository_id != self.workspace.repository_id
            or self.source_workspace.base_revision != self.workspace.base_revision
            or self.source_workspace.workspace_id == self.workspace.workspace_id
            or self.checkpoint.workspace_id != self.workspace.workspace_id
            or self.checkpoint.parent_checkpoint_id is not None
            or self.checkpoint.assignment_id is not None
            or self.checkpoint.attempt_id is not None
            or self.checkpoint.source_sha256 != self.source.source_snapshot.sha256
            or self.checkpoint.byte_count != self.source.source_snapshot.byte_count
            or self.checkpoint.entry_count != self.source.source_snapshot.entry_count
            or not stat.S_ISDIR(self.workspace_mode)
        ):
            raise ValueError("Candidate admission requires one exact initial captured source")
        return self
