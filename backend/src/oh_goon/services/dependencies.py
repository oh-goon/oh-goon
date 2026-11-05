from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import get_args

from oh_goon.core.dependency_identity import DependencyEcosystem
from oh_goon.core.errors import InvalidRequest, RepositoryNotManaged, StateConflict
from oh_goon.core.models.dependency_fleet import (
    DependencyAdvisoryOverview,
    DependencyCoveragePopulation,
    DependencyCoverageRepositoryPage,
    DependencyEvidenceFilter,
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
from oh_goon.integrations.hashmarks import dependency_package_query_identity
from oh_goon.runtime.dependency_observations import DependencyObservationStore
from oh_goon.runtime.dependency_targets import DependencyTargetMaterializationStore
from oh_goon.services.dependency_advisory import DependencyAdvisoryService
from oh_goon.services.dependency_aggregation import build_dependency_overview
from oh_goon.services.dependency_coverage import DependencyCoverageService
from oh_goon.services.repositories import RepositoryService


class DependencyFleetService:
    """Search retained lock facts with producer-owned source revalidation."""

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

    def search(
        self,
        query: str,
        *,
        ecosystem: DependencyEcosystem | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> DependencySearchResult:
        with self._store.assessment_scope():
            if offset < 0:
                raise InvalidRequest("Dependency search offset must be zero or greater")
            requested = _package_query(query)
            identities = tuple(
                dependency_package_query_identity(scope, requested)
                for scope in (
                    (ecosystem,)
                    if ecosystem is not None
                    else get_args(DependencyEcosystem.__value__)
                )
            )
            canonical_names = {
                identity.ecosystem: identity.canonical_name for identity in identities
            }
            current = self._current_repositories()
            repository_ids_by_package: dict[tuple[DependencyEcosystem, str], set[str]] = (
                defaultdict(set)
            )
            versions_by_package: dict[tuple[DependencyEcosystem, str], set[str]] = defaultdict(set)
            for identity in identities:
                for row in self._store.package_search_rows(
                    identity.canonical_name, identity.ecosystem
                ):
                    repository_id = str(row["repository_id"])
                    if repository_id not in current:
                        continue
                    package = (row["ecosystem"], str(row["package_name"]))
                    repository_ids_by_package[package].add(repository_id)
                    if row["resolved_version"] is not None:
                        versions_by_package[package].add(str(row["resolved_version"]))
            items = [
                DependencySearchItem(
                    ecosystem=package[0],
                    package_name=package[1],
                    repositories=len(repository_ids),
                    resolved_versions=len(versions_by_package[package]),
                )
                for package, repository_ids in repository_ids_by_package.items()
            ]
            ranked = sorted(
                items,
                key=lambda item: (
                    0 if item.package_name == canonical_names[item.ecosystem] else 1,
                    -item.repositories,
                    item.package_name,
                ),
            )
            return DependencySearchResult(
                query=requested,
                ecosystem=ecosystem,
                query_identities=identities,
                total=len(ranked),
                offset=offset,
                limit=limit,
                items=tuple(ranked[offset : offset + limit]),
            )

    def fleet_coverage(
        self, *, ecosystem: DependencyEcosystem = "pypi"
    ) -> DependencyFleetCoverageOverview:
        with self._store.assessment_scope():
            return self._coverage.overview(ecosystem=ecosystem)

    def overview(
        self, package_name: str, *, ecosystem: DependencyEcosystem = "pypi"
    ) -> DependencyPackageOverview:
        with self._store.assessment_scope():
            identity = dependency_package_query_identity(ecosystem, _package_query(package_name))
            return build_dependency_overview(
                self._store,
                identity.canonical_name,
                self._current_repositories(),
                ecosystem=ecosystem,
            ).model_copy(update={"query_identity": identity})

    def advisory(
        self,
        package_name: str,
        *,
        advisory_id: str,
        affected_specifier: str,
        ecosystem: DependencyEcosystem = "pypi",
    ) -> DependencyAdvisoryOverview:
        with self._store.assessment_scope():
            identity = dependency_package_query_identity(ecosystem, _package_query(package_name))
            return self._advisory.overview(
                identity.canonical_name,
                advisory_id=advisory_id,
                affected_specifier=affected_specifier,
                ecosystem=ecosystem,
            ).model_copy(update={"query_identity": identity})

    def repositories(
        self,
        package_name: str,
        *,
        ecosystem: DependencyEcosystem = "pypi",
        resolved_version: str | None = None,
        version_prefix: str | None = None,
        version_specifier: str | None = None,
        advisory_id: str | None = None,
        evidence: DependencyEvidenceFilter = "all",
        offset: int = 0,
        limit: int = 20,
    ) -> DependencyRepositoryPage:
        with self._store.assessment_scope():
            identity = dependency_package_query_identity(ecosystem, _package_query(package_name))
            query = DependencyTargetFilter(
                package_name=identity.canonical_name,
                ecosystem=ecosystem,
                resolved_version=resolved_version,
                version_prefix=version_prefix,
                version_specifier=version_specifier,
                advisory_id=advisory_id,
                evidence=evidence,
            )
            matches = self._matches(query)
            repo_ids = tuple(sorted(item.repository.repository_id for item in matches))
            repository_instance_ids = _repository_instance_map(item.repository for item in matches)
            return DependencyRepositoryPage(
                population_id=_population_id(query, repo_ids, repository_instance_ids),
                package_name=query.package_name,
                ecosystem=query.ecosystem,
                query_identity=identity,
                total=len(matches),
                offset=offset,
                limit=limit,
                items=tuple(matches[offset : offset + limit]),
            )

    def coverage_repositories(
        self,
        package_name: str,
        *,
        ecosystem: DependencyEcosystem = "pypi",
        coverage: DependencyCoveragePopulation,
        offset: int = 0,
        limit: int = 20,
    ) -> DependencyCoverageRepositoryPage:
        with self._store.assessment_scope():
            identity = dependency_package_query_identity(ecosystem, _package_query(package_name))
            package = identity.canonical_name
            query = DependencyTargetFilter(
                package_name=package, ecosystem=ecosystem, coverage=coverage
            )
            items = self._coverage.items(package, coverage, ecosystem=ecosystem)
            repo_ids = tuple(sorted(item.repository.repository_id for item in items))
            repository_instance_ids = _repository_instance_map(item.repository for item in items)
            return DependencyCoverageRepositoryPage(
                population_id=_population_id(query, repo_ids, repository_instance_ids),
                package_name=package,
                ecosystem=ecosystem,
                query_identity=identity,
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
        with self._store.assessment_scope():
            current = self._current_repositories()
            repository = current.get(repository_id)
            if repository is None:
                raise RepositoryNotManaged("Repository is not in the current included League")
            rows = self._store.repository_rows(repository_id)
            by_package: dict[tuple[DependencyEcosystem, str], list[sqlite3.Row]] = defaultdict(list)
            for row in rows:
                by_package[(row["ecosystem"], str(row["package_name"]))].append(row)
            groups = sorted(by_package.items())
            items = [
                _match_from_rows(self._store, repository, package[1], group)
                for package, group in groups[offset : offset + limit]
            ]
            return RepositoryDependencyPage(
                repository=repository,
                total=len(groups),
                offset=offset,
                limit=limit,
                items=tuple(items),
            )

    def materialize(
        self, request: DependencyTargetMaterializeRequest
    ) -> DependencyTargetMaterialization:
        with self._store.assessment_scope():
            query = request.filter
            identity = dependency_package_query_identity(
                query.ecosystem, _package_query(query.package_name)
            )
            normalized = query.model_copy(update={"package_name": identity.canonical_name})
            if normalized.coverage == "matching":
                matches = self._matches(normalized)
                repo_ids = tuple(sorted(item.repository.repository_id for item in matches))
                repository_instance_ids = _repository_instance_map(
                    item.repository for item in matches
                )
            else:
                coverage_items = self._coverage.items(
                    normalized.package_name, normalized.coverage, ecosystem=normalized.ecosystem
                )
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
        with self._store.assessment_scope():
            return self._targets.get(query_id)

    def _matches(self, query: DependencyTargetFilter) -> list[DependencyRepositoryMatch]:
        current = self._current_repositories()
        if query.version_specifier is not None and not query.version_specifier.strip():
            raise InvalidRequest("Version predicate must be non-empty")
        for snapshot in self._store.snapshot_rows(query.ecosystem):
            if str(snapshot["repository_id"]) not in current:
                continue
            projection = self._store.package_projection(snapshot, query.package_name)
            if projection is not None and projection["packages"][0]["truncation"] != "complete":
                raise StateConflict(
                    "Dependency package projection is incomplete; target population is unverified"
                )
        rows = self._store.package_rows(
            query.package_name,
            ecosystem=query.ecosystem,
            resolved_version=query.resolved_version,
            version_prefix=query.version_prefix,
            evidence=query.evidence,
        )
        grouped = _group_rows([row for row in rows if str(row["repository_id"]) in current])
        if query.version_specifier is not None:
            grouped = {
                repository_id: group
                for repository_id, group in grouped.items()
                if any(
                    row["resolved_version"] is not None
                    and self._store.version_state(row, query.version_specifier) == "match"
                    for row in group
                )
            }
        return [
            _match_from_rows(
                self._store,
                current[repository_id],
                query.package_name,
                group,
                version_specifier=query.version_specifier,
            )
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
    *,
    version_specifier: str | None = None,
) -> DependencyRepositoryMatch:
    first = rows[0]
    projection = (
        store.package_projection(first, package_name, version_specifier=version_specifier) or {}
    )
    resolved_versions = tuple(
        sorted(
            {str(row["resolved_version"]) for row in rows if row["resolved_version"] is not None}
        )
    )
    sources = tuple(sorted({str(row["resolved_source"]) for row in rows}))
    contexts = tuple(
        sorted({context for row in rows for context in json.loads(str(row["contexts_json"]))})
    )
    return DependencyRepositoryMatch(
        repository=repository,
        ecosystem=first["ecosystem"],
        package_name=package_name,
        component_ids=tuple(
            sorted({str(row["component_id"]) for row in rows if row["component_id"] is not None})
        ),
        selection_ids=tuple(sorted({str(row["node_id"]) for row in rows})),
        contexts=contexts,
        resolved_versions=resolved_versions,
        sources=sources,
        evidence=store.assessment(first),
        producer_projection=projection,
        observed_at=datetime.fromisoformat(str(first["observed_at"])),
        fleet_run_id=str(first["fleet_run_id"]),
        operation_id=str(first["operation_id"]),
        producer_goon=(str(first["producer_goon"]) if first["producer_goon"] is not None else None),
        step_id=str(first["step_id"]),
    )


def _package_query(value: str) -> str:
    candidate = value.strip()
    if not candidate or len(value) > 4096 or not value.isprintable():
        raise InvalidRequest("Dependency package query must be bounded non-empty text")
    return value


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
