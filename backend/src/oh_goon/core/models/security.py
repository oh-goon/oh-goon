from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.repository import RepositoryIdentity


class FleetSecurityFinding(ImmutableModel):
    advisory_id: str
    package: str | None = None
    aliases: tuple[str, ...] = Field(default_factory=tuple)
    fixed_versions: tuple[str, ...] = Field(default_factory=tuple)
    summary: str | None = None
    repositories: tuple[RepositoryIdentity, ...] = Field(default_factory=tuple)


class FleetSecuritySummary(ImmutableModel):
    durable_revision: int = Field(ge=0)
    journal_revision: int = Field(ge=0)
    repositories: int = Field(ge=0)
    repository_ids: tuple[str, ...]
    repository_instance_ids: dict[str, str]
    audited: int = Field(ge=0)
    clean: int = Field(ge=0)
    affected_repositories: int = Field(ge=0)
    stale: int = Field(ge=0)
    findings: tuple[FleetSecurityFinding, ...] = Field(default_factory=tuple)
    latest_audit_run_id: str | None = None

    @model_validator(mode="after")
    def validate_repository_scope(self) -> "FleetSecuritySummary":
        if len(self.repository_ids) != len(set(self.repository_ids)):
            raise ValueError("Security repository IDs must be unique")
        if set(self.repository_instance_ids) != set(self.repository_ids):
            raise ValueError("Security repository instance IDs must match repository IDs")
        if self.repositories != len(self.repository_ids):
            raise ValueError("Security repository count must match its repository scope")
        return self


class FleetSbomDocument(ImmutableModel):
    repository: RepositoryIdentity
    operation_id: str
    component_count: int = Field(ge=0)
    document: dict[str, Any]


class FleetSbomBundle(ImmutableModel):
    fleet_run_id: str
    journal_revision: int = Field(ge=0)
    documents: tuple[FleetSbomDocument, ...]
