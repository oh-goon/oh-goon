from __future__ import annotations

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import MigrationReadiness, MigrationSource


class MigrationCandidate(ImmutableModel):
    repository: RepositoryIdentity
    repository_instance_id: str = Field(min_length=1)
    source: MigrationSource
    readiness: MigrationReadiness
    reasons: tuple[str, ...] = Field(default_factory=tuple)
    source_files: tuple[str, ...] = Field(default_factory=tuple)


class AdoptUvPreview(ImmutableModel):
    repositories: tuple[MigrationCandidate, ...] = Field(default_factory=tuple)
    discovered: int = 0
    already_uv: int = 0
    safe_automatic: int = 0
    review_required: int = 0
    unsupported: int = 0


class MigrationResult(ImmutableModel):
    source: MigrationSource
    readiness: MigrationReadiness
    created_files: tuple[str, ...] = Field(default_factory=tuple)
    changed_files: tuple[str, ...] = Field(default_factory=tuple)
    validation_steps: tuple[str, ...] = Field(default_factory=tuple)
    branch: str | None = None
    commit: str | None = None
