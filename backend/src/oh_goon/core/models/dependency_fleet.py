from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_observation import DependencyEvidenceAssessment
from oh_goon.core.models.repository import RepositoryIdentity

DependencyEvidenceState = Literal["current", "stale", "unknown"]
DependencyEvidenceFilter = Literal["all", "current", "unknown", "stale"]
DependencyCoveragePopulation = Literal["absent_from_lock", "no_current_evidence"]
DependencyCoverageFilter = Literal["matching", "absent_from_lock", "no_current_evidence"]


class DependencyVersionBucket(ImmutableModel):
    version: str = Field(min_length=1)
    repositories: int = Field(ge=1)


class DependencyFreshnessSummary(ImmutableModel):
    current: int = Field(ge=0)
    no_matching_dependency: int = Field(ge=0)
    unknown: int = Field(ge=0)
    stale: int = Field(ge=0)
    no_current_evidence: int = Field(ge=0)


class DependencyFleetCoverageOverview(ImmutableModel):
    repository_total: int = Field(ge=0)
    current_dependency_evidence: int = Field(ge=0)
    unknown_dependency_evidence: int = Field(ge=0)
    stale_dependency_evidence: int = Field(ge=0)
    missing_dependency_evidence: int = Field(ge=0)
    no_current_dependency_evidence: int = Field(ge=0)


class DependencyPackageOverview(ImmutableModel):
    ecosystem: Literal["pypi"] = "pypi"
    package_name: str = Field(min_length=1)
    observed_repositories: int = Field(ge=0)
    versions: tuple[DependencyVersionBucket, ...] = Field(default_factory=tuple, max_length=500)
    freshness: DependencyFreshnessSummary
    latest_observed_at: AwareDatetime | None = None


class DependencySearchItem(ImmutableModel):
    ecosystem: Literal["pypi"] = "pypi"
    package_name: str = Field(min_length=1)
    repositories: int = Field(ge=1)
    resolved_versions: int = Field(ge=0)


class DependencySearchResult(ImmutableModel):
    query: str = Field(min_length=1)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[DependencySearchItem, ...] = Field(default_factory=tuple, max_length=100)


class DependencyRepositoryMatch(ImmutableModel):
    repository: RepositoryIdentity
    ecosystem: Literal["pypi"] = "pypi"
    package_name: str = Field(min_length=1)
    contexts: tuple[str, ...] = ()
    resolved_versions: tuple[str, ...] = Field(default_factory=tuple)
    sources: tuple[str, ...] = Field(min_length=1)
    evidence: DependencyEvidenceAssessment
    observed_at: AwareDatetime
    fleet_run_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    producer_goon: str | None = Field(default=None, min_length=1)
    step_id: str = Field(min_length=1)


class DependencyRepositoryPage(ImmutableModel):
    population_id: str = Field(min_length=1)
    package_name: str = Field(min_length=1)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[DependencyRepositoryMatch, ...] = Field(default_factory=tuple, max_length=100)


class DependencyCoverageRepository(ImmutableModel):
    repository: RepositoryIdentity
    coverage: Literal["absent_from_lock", "no_current_evidence"]
    evidence: DependencyEvidenceAssessment
    sources: tuple[str, ...] = Field(default_factory=tuple, max_length=512)
    observed_at: AwareDatetime | None = None
    fleet_run_id: str | None = Field(default=None, min_length=1)
    operation_id: str | None = Field(default=None, min_length=1)
    producer_goon: str | None = Field(default=None, min_length=1)
    step_id: str | None = Field(default=None, min_length=1)


class DependencyCoverageRepositoryPage(ImmutableModel):
    population_id: str = Field(min_length=1)
    package_name: str = Field(min_length=1)
    coverage: Literal["absent_from_lock", "no_current_evidence"]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[DependencyCoverageRepository, ...] = Field(default_factory=tuple, max_length=100)


class RepositoryDependencyPage(ImmutableModel):
    repository: RepositoryIdentity
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[DependencyRepositoryMatch, ...] = Field(default_factory=tuple, max_length=100)


class DependencyTargetFilter(ImmutableModel):
    package_name: str = Field(min_length=1)
    resolved_version: str | None = Field(default=None, min_length=1, max_length=200)
    version_prefix: str | None = Field(default=None, min_length=1, max_length=200)
    version_specifier: str | None = Field(default=None, min_length=1, max_length=200)
    advisory_id: str | None = Field(default=None, min_length=1, max_length=200)
    evidence: DependencyEvidenceFilter = "all"
    coverage: DependencyCoverageFilter = "matching"

    @model_validator(mode="after")
    def validate_version_filter(self) -> DependencyTargetFilter:
        version_filters = [self.resolved_version, self.version_prefix, self.version_specifier]
        if sum(item is not None for item in version_filters) > 1:
            raise ValueError(
                "Choose exact resolved_version, version_prefix, or version_specifier, not more than one"
            )
        if self.advisory_id is not None and self.version_specifier is None:
            raise ValueError("advisory_id requires an explicit affected version_specifier")
        if self.coverage != "matching":
            if any(version_filters) or self.advisory_id is not None:
                raise ValueError("Coverage populations cannot combine version/advisory filters")
            if self.evidence != "all":
                raise ValueError("Coverage populations cannot combine evidence filters")
        return self


class DependencyAdvisoryOverview(ImmutableModel):
    advisory_id: str = Field(min_length=1, max_length=200)
    package_name: str = Field(min_length=1)
    affected_specifier: str = Field(min_length=1, max_length=200)
    affected_repositories: int = Field(ge=0)
    observed_not_affected: int = Field(ge=0)
    version_unclassified: int = Field(ge=0)
    no_matching_dependency: int = Field(ge=0)
    unknown: int = Field(ge=0)
    stale: int = Field(ge=0)
    no_current_evidence: int = Field(ge=0)


class DependencyTargetMaterializeRequest(ImmutableModel):
    filter: DependencyTargetFilter
    expected_population_id: str = Field(min_length=1)


class DependencyTargetMaterialization(ImmutableModel):
    query_id: str = Field(min_length=1)
    selected_at: AwareDatetime
    filter: DependencyTargetFilter
    total: int = Field(ge=0, le=5000)
    repo_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=5000)
    repository_instance_ids: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target_set(self) -> DependencyTargetMaterialization:
        if self.total != len(self.repo_ids):
            raise ValueError("Materialized dependency target count must match repository IDs")
        if self.repo_ids != tuple(sorted(set(self.repo_ids))):
            raise ValueError("Materialized dependency repository IDs must be unique and sorted")
        if set(self.repository_instance_ids) != set(self.repo_ids):
            raise ValueError(
                "Materialized dependency target instances must exactly match repository IDs"
            )
        if any(not value for value in self.repository_instance_ids.values()):
            raise ValueError("Materialized dependency repository instance IDs must not be empty")
        return self
