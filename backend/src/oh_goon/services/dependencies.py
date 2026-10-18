from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime

from oh_goon.core.dependency_identity import is_valid_package_name, normalize_package_name
from oh_goon.core.dependency_version_filter import (
    parse_version_specifier,
    version_matches_specifier,
)
from oh_goon.core.errors import InvalidRequest, RepositoryNotManaged, StateConflict
from oh_goon.core.models.dependency_fleet import (
    DependencyAdvisoryOverview,
    DependencyCoveragePopulation,
    DependencyCoverageRepositoryPage,
    DependencyFleetCoverageOverview,
    DependencyPackageOverview,
    DependencyRepositoryMatch,
    DependencyRepositoryPage,
    DependencySearchItem,
    DependencySearchResult,
    DependencyTargetFilter,
    DependencyTargetMaterialization,
    DependencyTargetMaterializeRequest,
    RepositoryDependencyPage,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.product_identity import repository_subject_instance_id, stable_id
from oh_goon.runtime.dependency_observations import DependencyObservationStore
from oh_goon.runtime.dependency_targets import DependencyTargetMaterializationStore
from oh_goon.services.dependency_advisory import DependencyAdvisoryService
from oh_goon.services.dependency_aggregation import build_dependency_overview
from oh_goon.services.dependency_coverage import DependencyCoverageService
from oh_goon.services.repositories import RepositoryService


class DependencyFleetService:
    """Search canonical dependency observations without reopening repository files."""

    def __init__(
        self,
        repositories: RepositoryService,
        observations: DependencyObservationStore,
        targets: DependencyTargetMaterializationStore,
    ) -> None:
        self._repositories = repositories
        self._store = observations
        self._targets = targets
        self._coverage = DependencyCoverageService(repositories, observations)
        self._advisory = DependencyAdvisoryService(repositories, observations)

    def search(self, query: str, *, offset: int = 0, limit: int = 20) -> DependencySearchResult:
        if offset < 0:
            raise InvalidRequest("Dependency search offset must be zero or greater")
        normalized = _package_query(query)
        current = self._current_repositories()
        repository_ids_by_package: dict[str, set[str]] = defaultdict(set)
        versions_by_package: dict[str, set[str]] = defaultdict(set)
        for row in self._store.package_search_rows(normalized):
            repository_id = str(row["repository_id"])
            if repository_id not in current:
                continue
            package_name = str(row["package_name"])
            repository_ids_by_package[package_name].add(repository_id)
            if row["resolved_version"] is not None:
                versions_by_package[package_name].add(str(row["resolved_version"]))
        items = [
            DependencySearchItem(
                package_name=package_name,
                repositories=len(repository_ids),
                resolved_versions=len(versions_by_package[package_name]),
            )
            for package_name, repository_ids in repository_ids_by_package.items()
        ]
        ranked = sorted(
            items,
            key=lambda item: (
                0 if item.package_name == normalized else 1,
                -item.repositories,
                item.package_name,
            ),
        )
        return DependencySearchResult(
            query=normalized,
            total=len(ranked),
            offset=offset,
            limit=limit,
            items=tuple(ranked[offset : offset + limit]),
        )

    def fleet_coverage(self) -> DependencyFleetCoverageOverview:
        return self._coverage.overview()

    def overview(self, package_name: str) -> DependencyPackageOverview:
        package = _package_query(package_name)
        return build_dependency_overview(self._store, package, self._current_repositories())

    def advisory(
        self, package_name: str, *, advisory_id: str, affected_specifier: str
    ) -> DependencyAdvisoryOverview:
        return self._advisory.overview(
            _package_query(package_name),
            advisory_id=advisory_id,
            affected_specifier=affected_specifier,
        )

    def repositories(
        self,
        package_name: str,
        *,
        resolved_version: str | None = None,
        version_prefix: str | None = None,
        version_specifier: str | None = None,
        advisory_id: str | None = None,
        relationship: str = "all",
        evidence: str = "all",
        offset: int = 0,
        limit: int = 20,
    ) -> DependencyRepositoryPage:
        query = DependencyTargetFilter(
            package_name=_package_query(package_name),
            resolved_version=resolved_version,
            version_prefix=version_prefix,
            version_specifier=version_specifier,
            advisory_id=advisory_id,
            relationship=relationship,
            evidence=evidence,
        )
        matches = self._matches(query)
        repo_ids = tuple(sorted(item.repository.repository_id for item in matches))
        repository_instance_ids = _repository_instance_map(item.repository for item in matches)
        return DependencyRepositoryPage(
            population_id=_population_id(query, repo_ids, repository_instance_ids),
            package_name=query.package_name,
            total=len(matches),
            offset=offset,
            limit=limit,
            items=tuple(matches[offset : offset + limit]),
        )

    def coverage_repositories(
        self,
        package_name: str,
        *,
        coverage: DependencyCoveragePopulation,
        offset: int = 0,
        limit: int = 20,
    ) -> DependencyCoverageRepositoryPage:
        package = _package_query(package_name)
        query = DependencyTargetFilter(package_name=package, coverage=coverage)
        items = self._coverage.items(package, coverage)
        repo_ids = tuple(sorted(item.repository.repository_id for item in items))
        repository_instance_ids = _repository_instance_map(item.repository for item in items)
        return DependencyCoverageRepositoryPage(
            population_id=_population_id(query, repo_ids, repository_instance_ids),
            package_name=package,
            coverage=coverage,
            total=len(items),
            offset=offset,
            limit=limit,
            items=tuple(items[offset : offset + limit]),
        )

    def repository(
        self,
        repository_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> RepositoryDependencyPage:
        current = self._current_repositories()
        repository = current.get(repository_id)
        if repository is None:
            raise RepositoryNotManaged("Repository is not in the current included League")
        rows = self._store.repository_rows(repository_id)
        by_package: dict[str, list[object]] = defaultdict(list)
        for row in rows:
            by_package[str(row["package_name"])].append(row)
        items = [
            _match_from_rows(self._store, repository, package, group)
            for package, group in sorted(by_package.items())
        ]
        return RepositoryDependencyPage(
            repository=repository,
            total=len(items),
            offset=offset,
            limit=limit,
            items=tuple(items[offset : offset + limit]),
        )

    def materialize(
        self, request: DependencyTargetMaterializeRequest
    ) -> DependencyTargetMaterialization:
        query = request.filter
        normalized = query.model_copy(update={"package_name": _package_query(query.package_name)})
        if normalized.coverage == "matching":
            matches = self._matches(normalized)
            repo_ids = tuple(sorted(item.repository.repository_id for item in matches))
            repository_instance_ids = _repository_instance_map(item.repository for item in matches)
        else:
            coverage_items = self._coverage.items(normalized.package_name, normalized.coverage)
            repo_ids = tuple(sorted(item.repository.repository_id for item in coverage_items))
            repository_instance_ids = _repository_instance_map(
                item.repository for item in coverage_items
            )
        selected_at = datetime.now(UTC)
        query_id = _population_id(normalized, repo_ids, repository_instance_ids)
        if query_id != request.expected_population_id:
            raise StateConflict("Dependency target population changed since it was displayed")
        return self._targets.save(
            DependencyTargetMaterialization(
                query_id=query_id,
                selected_at=selected_at,
                filter=normalized,
                total=len(repo_ids),
                repo_ids=repo_ids,
                repository_instance_ids=repository_instance_ids,
            )
        )

    def target_selection(self, query_id: str) -> DependencyTargetMaterialization:
        return self._targets.get(query_id)

    def _matches(self, query: DependencyTargetFilter) -> list[DependencyRepositoryMatch]:
        current = self._current_repositories()
        if query.version_specifier is not None:
            parse_version_specifier(query.version_specifier)
        rows = self._store.package_rows(
            query.package_name,
            resolved_version=query.resolved_version,
            version_prefix=query.version_prefix,
            relationship=query.relationship,
            evidence=query.evidence,
        )
        grouped = _group_rows([row for row in rows if str(row["repository_id"]) in current])
        if query.version_specifier is not None:
            grouped = {
                repository_id: group
                for repository_id, group in grouped.items()
                if any(
                    row["resolved_version"] is not None
                    and version_matches_specifier(
                        str(row["resolved_version"]), query.version_specifier
                    )
                    is True
                    for row in group
                )
            }
        return [
            _match_from_rows(self._store, current[repository_id], query.package_name, group)
            for repository_id, group in sorted(
                grouped.items(), key=lambda item: current[item[0]].name.casefold()
            )
        ]

    def _current_repositories(self) -> dict[str, RepositoryIdentity]:
        self._store.synchronize()
        return {
            entry.repository.repository_id: entry.repository
            for entry in self._repositories.included()
        }


def _group_rows(rows: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["repository_id"])].append(row)
    return grouped


def _match_from_rows(
    store: DependencyObservationStore,
    repository: RepositoryIdentity,
    package_name: str,
    rows: list[sqlite3.Row],
) -> DependencyRepositoryMatch:
    first = rows[0]
    relationships = {str(row["relationship"]) for row in rows}
    relationship = "direct" if "direct" in relationships else "transitive"
    declared_ranges = tuple(
        sorted({str(row["declared_range"]) for row in rows if row["declared_range"] is not None})
    )
    resolved_versions = tuple(
        sorted(
            {str(row["resolved_version"]) for row in rows if row["resolved_version"] is not None}
        )
    )
    sources = tuple(
        sorted(
            {
                str(source)
                for row in rows
                for source in (row["declared_source"], row["resolved_source"])
                if source is not None
            }
        )
    )
    return DependencyRepositoryMatch(
        repository=repository,
        package_name=package_name,
        relationship=relationship,
        declared_ranges=declared_ranges,
        resolved_versions=resolved_versions,
        sources=sources,
        evidence_state=store.evidence_state(first),
        observed_at=datetime.fromisoformat(str(first["observed_at"])),
        fleet_run_id=str(first["fleet_run_id"]),
        operation_id=str(first["operation_id"]),
        producer_goon=(str(first["producer_goon"]) if first["producer_goon"] is not None else None),
        step_id=str(first["step_id"]),
    )


def _package_query(value: str) -> str:
    if not is_valid_package_name(value):
        raise InvalidRequest("Dependency package must be a valid Python package name")
    return normalize_package_name(value)


def _repository_instance_map(repositories: Iterable[RepositoryIdentity]) -> dict[str, str]:
    return {
        repository.repository_id: repository_subject_instance_id(repository.path)
        for repository in repositories
    }


def _population_id(
    query: DependencyTargetFilter,
    repo_ids: tuple[str, ...],
    repository_instance_ids: dict[str, str],
) -> str:
    return stable_id(
        "dependency-target",
        {
            "filter": query.model_dump(mode="json", exclude_none=True),
            "repo_ids": repo_ids,
            "repository_instance_ids": repository_instance_ids,
        },
    )
