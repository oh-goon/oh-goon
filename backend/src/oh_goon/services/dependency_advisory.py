from __future__ import annotations

import sqlite3

from oh_goon.core.dependency_identity import DependencyEcosystem
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
        self,
        package_name: str,
        *,
        advisory_id: str,
        affected_specifier: str,
        ecosystem: DependencyEcosystem = "pypi",
    ) -> DependencyAdvisoryOverview:
        with self._store.assessment_scope():
            if not advisory_id.strip():
                raise InvalidRequest("Advisory identity must be non-empty")
            if not affected_specifier.strip():
                raise InvalidRequest("Affected version predicate must be non-empty")
            current = self._current_repository_ids()
            overview = build_dependency_overview(
                self._store, package_name, current, ecosystem=ecosystem
            )
            rows = [
                row
                for row in self._store.package_rows(package_name, ecosystem=ecosystem)
                if str(row["repository_id"]) in current
            ]
            grouped: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                grouped.setdefault(str(row["repository_id"]), []).append(row)
            affected = not_affected = unclassified = 0
            predicates = []
            for group in grouped.values():
                first = group[0]
                projection = self._store.package_projection(
                    first, package_name, version_specifier=affected_specifier
                )
                if self._store.evidence_state(first) != "current":
                    continue
                predicate = (
                    projection["packages"][0]["version_predicate"]
                    if projection is not None
                    else None
                )
                if predicate is not None and projection is not None:
                    predicates.append(
                        {
                            "repository_id": str(first["repository_id"]),
                            "projection_identity": projection["projection_identity"],
                            "producer_implementation_identity": projection[
                                "producer_implementation_identity"
                            ],
                            "assessment": projection["assessment"],
                            **predicate,
                        }
                    )
                outcomes = [
                    self._store.version_state(row, affected_specifier)
                    for row in group
                    if row["resolved_version"] is not None
                ]
                if "match" in outcomes:
                    affected += 1
                elif (
                    outcomes
                    and all(outcome == "nonmatch" for outcome in outcomes)
                    and predicate is not None
                    and predicate["truncation"] == "complete"
                    and predicate["counts"]["unclassified"] == 0
                ):
                    not_affected += 1
                else:
                    unclassified += 1
            return DependencyAdvisoryOverview(
                advisory_id=advisory_id.strip(),
                package_name=package_name,
                ecosystem=ecosystem,
                affected_specifier=affected_specifier,
                affected_repositories=affected,
                observed_not_affected=not_affected,
                version_unclassified=unclassified,
                no_matching_dependency=overview.freshness.no_matching_dependency,
                unknown=overview.freshness.unknown,
                stale=overview.freshness.stale,
                no_current_evidence=overview.freshness.no_current_evidence,
                producer_predicates=tuple(predicates),
            )

    def _current_repository_ids(self) -> set[str]:
        self._store.synchronize()
        return {entry.repository.repository_id for entry in self._repositories.included()}
