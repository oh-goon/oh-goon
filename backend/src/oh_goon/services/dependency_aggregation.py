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
    aged_count = sum(state == "aged" for state in states.values())
    stale_count = sum(state == "stale" for state in states.values())
    snapshot_rows = [row for row in store.snapshot_rows() if str(row["repository_id"]) in current]
    current_snapshot_ids = {
        str(row["repository_id"]) for row in snapshot_rows if store.evidence_state(row) == "current"
    }
    current_match_ids = {
        repository_id for repository_id, state in states.items() if state == "current"
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
        direct_repositories=sum(
            any(str(row["relationship"]) == "direct" for row in group)
            for group in by_repository.values()
        ),
        transitive_repositories=sum(
            all(str(row["relationship"]) == "transitive" for row in group)
            for group in by_repository.values()
        ),
        versions=versions,
        freshness=DependencyFreshnessSummary(
            current=current_count,
            no_matching_dependency=len(current_snapshot_ids - current_match_ids),
            aged=aged_count,
            stale=stale_count,
            no_current_evidence=len(current) - len(current_snapshot_ids),
        ),
        latest_observed_at=latest,
    )
