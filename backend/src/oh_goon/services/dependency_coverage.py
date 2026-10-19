from __future__ import annotations

import sqlite3
from datetime import datetime

from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.dependency_fleet import (
    DependencyCoveragePopulation,
    DependencyCoverageRepository,
    DependencyFleetCoverageOverview,
)
from oh_goon.core.models.fleet_observation import (
    DependencyEvidenceAssessment,
    DependencyObservationSnapshot,
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
        with self._store.assessment_scope():
            current = self._current_repositories()
            snapshots = {
                str(row["repository_id"]): row
                for row in self._store.snapshot_rows()
                if str(row["repository_id"]) in current
            }
            counts = {"current": 0, "unknown": 0, "stale": 0, "missing": 0}
            for repository_id in current:
                snapshot = snapshots.get(repository_id)
                assessment = (
                    self._store.assessment(snapshot)
                    if snapshot is not None
                    else DependencyEvidenceAssessment(availability="missing")
                )
                state = assessment.freshness if snapshot is not None else "missing"
                counts[state] += 1
            no_current = counts["unknown"] + counts["stale"] + counts["missing"]
            return DependencyFleetCoverageOverview(
                repository_total=len(current),
                current_dependency_evidence=counts["current"],
                unknown_dependency_evidence=counts["unknown"],
                stale_dependency_evidence=counts["stale"],
                missing_dependency_evidence=counts["missing"],
                no_current_dependency_evidence=no_current,
            )

    def items(
        self,
        package_name: str,
        coverage: DependencyCoveragePopulation,
    ) -> list[DependencyCoverageRepository]:
        with self._store.assessment_scope():
            if coverage not in {"absent_from_lock", "no_current_evidence"}:
                raise InvalidRequest("Unsupported dependency coverage population")
            current = self._current_repositories()
            snapshots = {
                str(row["repository_id"]): row
                for row in self._store.snapshot_rows()
                if str(row["repository_id"]) in current
            }
            items: list[DependencyCoverageRepository] = []
            for repository_id, repository in current.items():
                snapshot = snapshots.get(repository_id)
                assessment = (
                    self._store.assessment(snapshot)
                    if snapshot is not None
                    else DependencyEvidenceAssessment(availability="missing")
                )

                if coverage == "absent_from_lock":
                    if snapshot is None or not self._store.absent_from_lock(snapshot, package_name):
                        continue
                elif assessment.absence_admissible:
                    continue
                items.append(_coverage_item(repository, coverage, snapshot, assessment))
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
    assessment: DependencyEvidenceAssessment,
) -> DependencyCoverageRepository:
    if snapshot is None:
        return DependencyCoverageRepository(
            repository=repository,
            coverage=coverage,
            evidence=assessment,
        )
    observed = DependencyObservationSnapshot.model_validate_json(str(snapshot["snapshot_json"]))
    sources = tuple(item.path for item in observed.sources)
    return DependencyCoverageRepository(
        repository=repository,
        coverage=coverage,
        evidence=assessment,
        sources=sources,
        observed_at=datetime.fromisoformat(str(snapshot["observed_at"])),
        fleet_run_id=str(snapshot["fleet_run_id"]),
        operation_id=str(snapshot["operation_id"]),
        producer_goon=(
            str(snapshot["producer_goon"]) if snapshot["producer_goon"] is not None else None
        ),
        step_id=str(snapshot["step_id"]),
    )
