from __future__ import annotations

from pydantic import Field, computed_field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate


class GitLabImportIssue(ImmutableModel):
    job: str = ""
    field: str = ""
    reason: str = Field(min_length=1)


class GitLabResolvedComponent(ImmutableModel):
    reference: str = Field(min_length=1)
    host: str = Field(min_length=1)
    project: str = Field(min_length=1)
    component: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GitLabGoonMigration(ImmutableModel):
    source: str = Field(min_length=1)
    goon: CompiledExecutionTemplate
    components: tuple[GitLabResolvedComponent, ...] = Field(default_factory=tuple)
    issues: tuple[GitLabImportIssue, ...] = Field(default_factory=tuple)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        return not self.issues
