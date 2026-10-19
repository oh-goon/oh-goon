from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from oh_goon.core.models.dependency_fleet import (
    DependencyFreshnessSummary,
    DependencyPackageOverview,
    DependencyVersionBucket,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.runtime.dependency_observations import DependencyObservationStore


def build_dependency_overview(
    store: DependencyObservationStore,
    package: str,
    current: dict[str, RepositoryIdentity],
) -> DependencyPackageOverview:
    rows = [row for row in store.package_rows(package) if str(row["repository_id"]) in current]
    by_repository: dict[str, list[object]] = defaultdict(list)
    for row in rows:
        by_repository[str(row["repository_id"])].append(row)
    states = {
        repository_id: store.evidence_state(group[0])
        for repository_id, group in by_repository.items()
    }
    current_count = sum(state == "current" for state in states.values())
    unknown_count = sum(state == "unknown" for state in states.values())
    stale_count = sum(state == "stale" for state in states.values())
    snapshot_rows = [row for row in store.snapshot_rows() if str(row["repository_id"]) in current]
    current_snapshot_ids = {
        str(row["repository_id"])
        for row in snapshot_rows
        if store.assessment(row).absence_admissible
    }
    version_repositories: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["resolved_version"] is not None:
            version_repositories[str(row["resolved_version"])].add(str(row["repository_id"]))
    versions = tuple(
        DependencyVersionBucket(version=version, repositories=len(repository_ids))
        for version, repository_ids in sorted(
            version_repositories.items(), key=lambda item: (-len(item[1]), item[0])
        )
    )
    latest = max(
        (datetime.fromisoformat(str(row["observed_at"])) for row in rows),
        default=None,
    )
    return DependencyPackageOverview(
        package_name=package,
        observed_repositories=len(by_repository),
        versions=versions,
        freshness=DependencyFreshnessSummary(
            current=current_count,
            no_matching_dependency=sum(
                store.absent_from_lock(row, package) for row in snapshot_rows
            ),
            unknown=unknown_count,
            stale=stale_count,
            no_current_evidence=len(current) - len(current_snapshot_ids),
        ),
        latest_observed_at=latest,
    )
