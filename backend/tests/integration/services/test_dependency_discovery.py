from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.dependency_fleet import (
    DependencyTargetFilter,
    DependencyTargetMaterializeRequest,
)
from oh_goon.core.models.fleet_observation import DependencyObservationSnapshot
from oh_goon.core.models.fleet_plan import ResolvedDependencyInventoryStep
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.vocabulary import FleetScope
from oh_goon.integrations.fleet_plan_dependency_observation import execute_dependency_inventory
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
    assert service.search("MLFlow").items[0].repositories == 1
    assert service.repositories("MLFlow", evidence="current").items[0].repository == repository

    population = service.repositories("MLFlow", evidence="current")
    assert population.package_name == "mlflow"
    assert population.query_identity is not None
    assert population.query_identity.requested_name == "MLFlow"
    selected = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=DependencyTargetFilter(package_name="MLFlow", evidence="current"),
            expected_population_id=population.population_id,
        )
    )
    restarted = DependencyTargetMaterializationStore(
        LocalDatabase(tmp_path / "dependency-integration.db")
    )
    assert restarted.get(selected.query_id).repo_ids == (repository.repository_id,)
    assert restarted.get(selected.query_id).filter.package_name == "mlflow"


def test_explicit_npm_step_retains_producer_identity_through_durable_query(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "npm")
    repository.path.mkdir()
    (repository.path / ".git").mkdir()
    dependency_snapshot(repository.path, ("@scope/shared", "1.5.0"), ecosystem="npm")
    step = ResolvedDependencyInventoryStep(step_id="npm-inventory", ecosystem="npm")
    result = execute_dependency_inventory(repository.path, step)
    assert result.returncode == 0
    snapshot = result.observations[0]
    assert isinstance(snapshot, DependencyObservationSnapshot)
    assert snapshot.ecosystem == "npm"
    assert snapshot.dependencies[0].component_id
    assert snapshot.dependencies[0].resolved_source == "package-lock.json"

    database = LocalDatabase(tmp_path / "npm.db")
    store = DependencyObservationStore(database, Journal(database))
    with database.transaction(write=True) as connection:
        store._index_report(connection, dependency_report(repository, snapshot, state="c" * 40))
    reopened_database = LocalDatabase(database.path)
    reopened = DependencyObservationStore(reopened_database, Journal(reopened_database))
    service = DependencyFleetService(
        _Repositories(repository), reopened, DependencyTargetMaterializationStore(reopened_database)
    )  # type: ignore[arg-type]
    page = service.repositories(
        "@scope/shared", ecosystem="npm", version_specifier="^1.0.0", evidence="current"
    )
    assert page.total == 1
    match = page.items[0]
    assert match.ecosystem == "npm" and match.contexts == snapshot.dependencies[0].contexts
    assert match.component_ids == (snapshot.dependencies[0].component_id,)
    producer = match.producer_projection
    assert producer["producer_implementation_identity"] == snapshot.producer_implementation_identity
    assert producer["disclosure"]["requalification_envelope"] is False
    assert "components" not in producer["observation"]
    assert snapshot.producer_projection["observation"]["components"]
    assert (
        producer["observation"]["observation_identity"]
        == snapshot.producer_observation["observation_identity"]
    )
    assert producer["packages"][0]["version_predicate"]["counts"]["match"] == 1
