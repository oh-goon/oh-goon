from __future__ import annotations

from pathlib import Path

from pydantic import AwareDatetime, Field, field_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState


class WorkspaceRef(ImmutableModel):
    """Stable workspace identity; never filesystem path or mutable lifecycle authority."""

    workspace_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    kind: WorkspaceKind


class WorkspaceState(ImmutableModel):
    """Immutable snapshot of the one durable mutable workspace-state row."""

    workspace_id: str = Field(min_length=1)
    lifecycle_state: WorkspaceLifecycleState
    state_version: int = Field(ge=0)
    current_lease_id: str | None = Field(default=None, min_length=1)
    current_checkpoint_id: str | None = Field(default=None, min_length=1)
    updated_at: AwareDatetime


class CandidateCheckpoint(ImmutableModel):
    """Durable identity for one observed state of a mutable candidate workspace."""

    checkpoint_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    parent_checkpoint_id: str | None = Field(default=None, min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)
    byte_count: int = Field(ge=0)
    entry_count: int = Field(ge=0)
    changed_paths: tuple[str, ...] = ()
    attempt_id: str | None = Field(default=None, min_length=1)
    assignment_id: str | None = Field(default=None, min_length=1)
    created_at: AwareDatetime

    @field_validator("source_sha256")
    @classmethod
    def _valid_sha256(cls, value: str) -> str:
        if any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("source_sha256 must be lowercase hexadecimal")
        return value

    @field_validator("changed_paths")
    @classmethod
    def _canonical_changed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("changed_paths must be unique and sorted")
        if any(not item or item.startswith("/") or ".." in Path(item).parts for item in value):
            raise ValueError("changed_paths must be canonical repository-relative paths")
        return value
