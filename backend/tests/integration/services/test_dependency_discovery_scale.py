from __future__ import annotations

from pathlib import Path
from time import perf_counter

from oh_goon.core.models.dependency_fleet import (
    DependencyTargetFilter,
    DependencyTargetMaterializeRequest,
)
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.vocabulary import FleetScope
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.dependency_observations import DependencyObservationStore
from oh_goon.runtime.dependency_targets import DependencyTargetMaterializationStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.dependencies import DependencyFleetService
from tests.factories.dependency_observations import dependency_report, dependency_snapshot


class _FiveThousandRepositories:
    def __init__(self, repositories: list[RepositoryIdentity]) -> None:
        self.entries = [
            IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=repository)
            for repository in repositories
        ]
        self.reads = 0

    def included(self):
        self.reads += 1
        return list(self.entries)


def test_five_thousand_repository_mlflow_flow_is_indexed_bounded_and_truthful(
    tmp_path: Path,
) -> None:
    repositories = [
        RepositoryIdentity(path=tmp_path / f"repo-{index:04d}") for index in range(5000)
    ]
    # RepositoryIdentity is logical history identity; dependency target populations also
    # carry current repository-instance authority. Materialize the claimed Git roots so
    # the scale fixture represents real included repositories instead of synthetic paths.
    for repository in repositories:
        (repository.path / ".git").mkdir(parents=True)
    catalog = _FiveThousandRepositories(repositories)
    database = LocalDatabase(tmp_path / "dependency-scale.db")
    journal = Journal(database)
    observations = DependencyObservationStore(database, journal)
    targets = DependencyTargetMaterializationStore(database)

    with database.transaction(write=True) as connection:
        for index, repository in enumerate(repositories[:4848]):
            if index < 92:
                packages = (("mlflow", "2.17.2"),)
            elif index < 137:
                packages = (("mlflow", "2.18.0"),)
            else:
                packages = (("requests", "2.32.0"),)
            observations._index_report(
                connection,
                dependency_report(
                    repository, dependency_snapshot(*packages), state=f"{index:040x}"[-40:]
                ),
            )

    service = DependencyFleetService(catalog, observations, targets)  # type: ignore[arg-type]
    started = perf_counter()
    search = service.search("mlflow")
    overview = service.overview("mlflow")
    fleet_coverage = service.fleet_coverage()
    advisory = service.advisory(
        "mlflow", advisory_id="CVE-2026-MLFLOW", affected_specifier="<2.18.0"
    )
    page = service.repositories(
        "mlflow",
        version_specifier="<2.18.0",
        advisory_id="CVE-2026-MLFLOW",
        evidence="current",
        limit=25,
    )
    selection = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=DependencyTargetFilter(
                package_name="mlflow",
                version_specifier="<2.18.0",
                advisory_id="CVE-2026-MLFLOW",
                evidence="current",
            ),
            expected_population_id=page.population_id,
        )
    )
    proven_page = service.coverage_repositories("mlflow", coverage="proven_no_match", limit=25)
    missing_page = service.coverage_repositories("mlflow", coverage="no_current_evidence", limit=25)
    missing_selection = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=DependencyTargetFilter(package_name="mlflow", coverage="no_current_evidence"),
            expected_population_id=missing_page.population_id,
        )
    )
    elapsed = perf_counter() - started

    assert search.items[0].repositories == 137
    assert overview.observed_repositories == 137
    assert fleet_coverage.repository_total == 5000
    assert fleet_coverage.current_dependency_evidence == 4848
    assert fleet_coverage.aged_dependency_evidence == 0
    assert fleet_coverage.stale_dependency_evidence == 0
    assert fleet_coverage.missing_dependency_evidence == 152
    assert fleet_coverage.no_current_dependency_evidence == 152
    assert overview.freshness.current == 137
    assert overview.freshness.no_matching_dependency == 4711
    assert overview.freshness.no_current_evidence == 152
    assert advisory.affected_repositories == 92
    assert advisory.observed_not_affected == 45
    assert advisory.version_unclassified == 0
    assert advisory.no_matching_dependency == 4711
    assert advisory.no_current_evidence == 152
    assert page.total == 92
    assert len(page.items) == 25
    assert selection.total == 92
    assert len(selection.repo_ids) == 92
    assert selection.repo_ids == tuple(sorted(selection.repo_ids))
    assert proven_page.total == 4711
    assert len(proven_page.items) == 25
    assert missing_page.total == 152
    assert len(missing_page.items) == 25
    assert missing_selection.total == 152
    assert missing_selection.repo_ids == tuple(sorted(missing_selection.repo_ids))
    assert elapsed < 10.0
    assert catalog.reads <= 9
