"""Source-bound presentation and reviewed invocation of the catalog showcase."""

from __future__ import annotations

from typing import Literal

from oh_goon_catalog_renderer import CatalogCandidate, CatalogComponentType, CatalogLifecycle
from pydantic import Field, JsonValue

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.source_authority import RepositorySourceAuthority


class CatalogLanguageObservation(ImmutableModel):
    name: str
    count: int = Field(ge=0)


class CatalogSearchHit(ImmutableModel):
    path: str
    kind: str
    name: str | None = None
    qualname: str | None = None
    signature: str | None = None
    first_line: int | None = Field(default=None, ge=1)
    last_line: int | None = Field(default=None, ge=1)


class CatalogRelation(ImmutableModel):
    source_path: str
    target: str
    kind: str
    line: int | None = None
    confidence: str
    resolved_paths: tuple[str, ...] = ()


class CatalogEvidenceQualification(ImmutableModel):
    generation: int | None = None
    identity_generation: int | None = None
    freshness: str
    completeness: str
    truncated: bool
    negative_evidence: str
    admissibility_reasons: tuple[str, ...]
    repository_wide_completeness: str


class CatalogGitAuthor(ImmutableModel):
    status: Literal["available", "unavailable", "select-file"]
    path: str | None = None
    revision: str | None = None
    commit: str | None = None
    author: str | None = None
    date: str | None = None
    message: str


class CatalogCandidateOptions(ImmutableModel):
    component_types: tuple[CatalogComponentType, ...]
    lifecycles: tuple[CatalogLifecycle, ...]


class CatalogRelationshipQualification(ImmutableModel):
    max_depth: int | None = None
    call_limit_per_symbol: int | None = None
    ref_limit_per_symbol: int | None = None
    unresolved_reasons: tuple[str, ...]
    external_or_unindexed_reasons: tuple[str, ...]
    lifecycle_completeness: str | None = None
    lifecycle_truncated: bool | None = None


class CatalogExploration(ImmutableModel):
    repository: RepositoryIdentity
    repository_instance_id: str
    source_authority: RepositorySourceAuthority
    producer_identity: str
    candidate_options: CatalogCandidateOptions
    languages: tuple[CatalogLanguageObservation, ...]
    hits: tuple[CatalogSearchHit, ...]
    relations: tuple[CatalogRelation, ...]
    relationship_status: Literal["observed", "select-file", "unsupported"]
    relationship_message: str
    relationship_qualification: CatalogRelationshipQualification | None = None
    qualification: CatalogEvidenceQualification
    git_author: CatalogGitAuthor
    ownership_status: Literal["manual-review-required"] = "manual-review-required"
    provider_mode: Literal["none"] = "none"
    technical_evidence: dict[str, JsonValue]


class CatalogPreview(ImmutableModel):
    repository: RepositoryIdentity
    source_authority: RepositorySourceAuthority
    candidate: CatalogCandidate
    yaml: str
    plan_id: str
    execution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_mode: Literal["none"] = "none"


class RunArtifactExport(ImmutableModel):
    status: Literal["exported"] = "exported"
    fleet_run_id: str
    repository_id: str
    step_id: str
    artifact_path: str
    destination: str
    sha256: str
    byte_count: int
    reference_id: str
    cleanup_status: Literal["verified", "failed"]
    cleanup_failure: Literal["STAGING_CLEANUP_FAILED"] | None = None
