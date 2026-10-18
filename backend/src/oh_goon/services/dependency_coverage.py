from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.dependency_fleet import (
    DependencyCoveragePopulation,
    DependencyCoverageRepository,
    DependencyCoverageRepositoryPage,
    DependencyFleetCoverageOverview,
    DependencySnapshotState,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.runtime.dependency_observations import DependencyObservationStore
from oh_goon.services.repositories import RepositoryService


class DependencyCoverageService:
    """Own non-matching dependency evidence populations and their provenance."""

    def __init__(
        self,
        repositories: RepositoryService,
        observations: DependencyObservationStore,
    ) -> None:
        self._repositories = repositories
        self._store = observations

    def overview(self) -> DependencyFleetCoverageOverview:
        current = self._current_repositories()
        snapshots = {
            str(row["repository_id"]): row
            for row in self._store.snapshot_rows()
            if str(row["repository_id"]) in current
        }
        counts = {"current": 0, "aged": 0, "stale": 0, "missing": 0}
        for repository_id in current:
            snapshot = snapshots.get(repository_id)
            state = self._store.evidence_state(snapshot) if snapshot is not None else "missing"
            counts[state] += 1
        no_current = counts["aged"] + counts["stale"] + counts["missing"]
        return DependencyFleetCoverageOverview(
            repository_total=len(current),
            current_dependency_evidence=counts["current"],
            aged_dependency_evidence=counts["aged"],
            stale_dependency_evidence=counts["stale"],
            missing_dependency_evidence=counts["missing"],
            no_current_dependency_evidence=no_current,
        )

    def repositories(
        self,
        package_name: str,
        *,
        coverage: DependencyCoveragePopulation,
        offset: int = 0,
        limit: int = 20,
    ) -> DependencyCoverageRepositoryPage:
        items = self.items(package_name, coverage)
        return DependencyCoverageRepositoryPage(
            package_name=package_name,
            coverage=coverage,
            total=len(items),
            offset=offset,
            limit=limit,
            items=tuple(items[offset : offset + limit]),
        )

    def repository_ids(
        self,
        package_name: str,
        coverage: DependencyCoveragePopulation,
    ) -> tuple[str, ...]:
        return tuple(
            sorted(item.repository.repository_id for item in self.items(package_name, coverage))
        )

    def items(
        self,
        package_name: str,
        coverage: DependencyCoveragePopulation,
    ) -> list[DependencyCoverageRepository]:
        if coverage not in {"proven_no_match", "no_current_evidence"}:
            raise InvalidRequest("Unsupported dependency coverage population")
        current = self._current_repositories()
        package_rows = [
            row
            for row in self._store.package_rows(package_name)
            if str(row["repository_id"]) in current
        ]
        current_package_ids = {
            str(row["repository_id"])
            for row in package_rows
            if self._store.evidence_state(row) == "current"
        }
        snapshots = {
            str(row["repository_id"]): row
            for row in self._store.snapshot_rows()
            if str(row["repository_id"]) in current
        }
        items: list[DependencyCoverageRepository] = []
        for repository_id, repository in current.items():
            snapshot = snapshots.get(repository_id)
            state = self._store.evidence_state(snapshot) if snapshot is not None else "missing"
            if coverage == "proven_no_match":
                if state != "current" or repository_id in current_package_ids:
                    continue
            elif state == "current":
                continue
            items.append(_coverage_item(repository, coverage, snapshot, state))
        return sorted(items, key=lambda item: item.repository.name.casefold())

    def _current_repositories(self) -> dict[str, RepositoryIdentity]:
        self._store.synchronize()
        return {
            entry.repository.repository_id: entry.repository
            for entry in self._repositories.included()
        }


def _coverage_item(
    repository: RepositoryIdentity,
    coverage: DependencyCoveragePopulation,
    snapshot: sqlite3.Row | None,
    state: DependencySnapshotState,
) -> DependencyCoverageRepository:
    if snapshot is None:
        return DependencyCoverageRepository(
            repository=repository,
            coverage=coverage,
            evidence_state="missing",
        )
    raw_sources = json.loads(str(snapshot["sources_json"]))
    sources = tuple(
        sorted(
            str(item["path"])
            for item in raw_sources
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        )
    )
    return DependencyCoverageRepository(
        repository=repository,
        coverage=coverage,
        evidence_state=state,
        sources=sources,
        observed_at=datetime.fromisoformat(str(snapshot["observed_at"])),
        fleet_run_id=str(snapshot["fleet_run_id"]),
        operation_id=str(snapshot["operation_id"]),
        producer_goon=(
            str(snapshot["producer_goon"]) if snapshot["producer_goon"] is not None else None
        ),
        step_id=str(snapshot["step_id"]),
    )
