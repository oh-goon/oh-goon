from __future__ import annotations

from pathlib import Path

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


class _Repositories:
    def __init__(self, repository: RepositoryIdentity) -> None:
        self._entry = IncludedRepositoryCatalogEntry(
            scope=FleetScope.INCLUDED, repository=repository
        )

    def included(self):
        return [self._entry]


def test_observed_dependency_population_round_trips_through_durable_selection(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "dependency-integration.db")
    journal = Journal(database)
    observations = DependencyObservationStore(database, journal)
    targets = DependencyTargetMaterializationStore(database)
    repository = RepositoryIdentity(path=tmp_path / "api")
    repository.path.mkdir(parents=True, exist_ok=True)
    (repository.path / ".git").mkdir()
    report = dependency_report(
        repository, dependency_snapshot(repository.path, ("mlflow", "2.17.2")), state="a" * 40
    )
    with database.transaction(write=True) as connection:
        observations._index_report(connection, report)

    service = DependencyFleetService(_Repositories(repository), observations, targets)  # type: ignore[arg-type]
    assert service.search("mlflow").items[0].repositories == 1
    assert service.repositories("mlflow", evidence="current").items[0].repository == repository

    population = service.repositories("mlflow", evidence="current")
    selected = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=DependencyTargetFilter(package_name="mlflow", evidence="current"),
            expected_population_id=population.population_id,
        )
    )
    restarted = DependencyTargetMaterializationStore(
        LocalDatabase(tmp_path / "dependency-integration.db")
    )
    assert restarted.get(selected.query_id).repo_ids == (repository.repository_id,)
