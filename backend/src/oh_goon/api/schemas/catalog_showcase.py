"""Explicit UI selections; producer evidence and execution policy are server-owned."""

from __future__ import annotations

from pathlib import Path

from oh_goon_catalog_renderer import CatalogCandidate
from pydantic import BaseModel, ConfigDict, Field, field_validator

from oh_goon.core.models.source_authority import RepositorySourceAuthority


class CatalogExploreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_repository_instance_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=512)
    path: str | None = Field(default=None, min_length=1, max_length=500)


class CatalogPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_authority: RepositorySourceAuthority
    candidate: CatalogCandidate

    @field_validator("candidate", mode="before")
    @classmethod
    def exact_candidate(cls, value: object) -> CatalogCandidate:
        return (
            value if isinstance(value, CatalogCandidate) else CatalogCandidate.from_mapping(value)
        )


class CatalogRunRequest(CatalogPreviewRequest):
    expected_plan_id: str = Field(min_length=1)
    expected_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_execution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fleet_run_id: str = Field(min_length=1, max_length=160)


class RunArtifactExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1, max_length=160)
    path: str = Field(min_length=1, max_length=500)
    destination: Path

    @field_validator("destination")
    @classmethod
    def absolute_destination(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Select an absolute local destination file")
        return value
