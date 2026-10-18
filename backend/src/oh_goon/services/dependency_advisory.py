from __future__ import annotations

from oh_goon.core.dependency_version_filter import (
    parse_version_specifier,
    version_matches_specifier,
)
from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.dependency_fleet import DependencyAdvisoryOverview
from oh_goon.runtime.dependency_observations import DependencyObservationStore
from oh_goon.services.dependency_aggregation import build_dependency_overview
from oh_goon.services.repositories import RepositoryService


class DependencyAdvisoryService:
    """Relate explicit advisory ranges to current resolved dependency observations."""

    def __init__(
        self, repositories: RepositoryService, observations: DependencyObservationStore
    ) -> None:
        self._repositories = repositories
        self._store = observations

    def overview(
        self, package_name: str, *, advisory_id: str, affected_specifier: str
    ) -> DependencyAdvisoryOverview:
        if not advisory_id.strip():
            raise InvalidRequest("Advisory identity must be non-empty")
        parse_version_specifier(affected_specifier)
        current = self._current_repository_ids()
        overview = build_dependency_overview(self._store, package_name, current)
        rows = [
            row
            for row in self._store.package_rows(package_name)
            if str(row["repository_id"]) in current
        ]
        grouped: dict[str, list[object]] = {}
        for row in rows:
            grouped.setdefault(str(row["repository_id"]), []).append(row)
        affected = not_affected = unclassified = 0
        for group in grouped.values():
            first = group[0]
            if self._store.evidence_state(first) != "current":
                continue
            outcomes = [
                version_matches_specifier(str(row["resolved_version"]), affected_specifier)
                for row in group
                if row["resolved_version"] is not None
            ]
            if any(outcome is True for outcome in outcomes):
                affected += 1
            elif outcomes and all(outcome is False for outcome in outcomes):
                not_affected += 1
            else:
                unclassified += 1
        return DependencyAdvisoryOverview(
            advisory_id=advisory_id.strip(),
            package_name=package_name,
            affected_specifier=affected_specifier,
            affected_repositories=affected,
            observed_not_affected=not_affected,
            version_unclassified=unclassified,
            no_matching_dependency=overview.freshness.no_matching_dependency,
            aged=overview.freshness.aged,
            stale=overview.freshness.stale,
            no_current_evidence=overview.freshness.no_current_evidence,
        )

    def _current_repository_ids(self) -> set[str]:
        self._store.synchronize()
        return {entry.repository.repository_id for entry in self._repositories.included()}
