from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.dependency_fleet import DependencyTargetFilter, DependencyTargetMaterializeRequest
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.vocabulary import FleetScope
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.dependency_observations import DependencyObservationStore
from oh_goon.runtime.dependency_targets import DependencyTargetMaterializationStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.dependencies import DependencyFleetService
from tests.factories.dependency_observations import dependency_report, dependency_snapshot


class _Repositories:
    def __init__(self, repositories: list[RepositoryIdentity]) -> None:
        self._entries = [
            IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=repository)
            for repository in repositories
        ]

    def included(self):
        return list(self._entries)


def _service(tmp_path: Path, repositories: list[RepositoryIdentity]):
    for repository in repositories:
        repository.path.mkdir(parents=True, exist_ok=True)
        (repository.path / ".git").mkdir(exist_ok=True)
    database = LocalDatabase(tmp_path / "dependencies.db")
    observations = DependencyObservationStore(database, Journal(database))
    targets = DependencyTargetMaterializationStore(database)
    service = DependencyFleetService(_Repositories(repositories), observations, targets)  # type: ignore[arg-type]
    return service, observations


def _index(
    store: DependencyObservationStore,
    repository: RepositoryIdentity,
    packages: tuple[tuple[str, str], ...],
    state: str,
) -> None:
    report = dependency_report(repository, dependency_snapshot(*packages), state=state)
    with store._database.transaction(write=True) as connection:  # unit-level projection fixture
        store._index_report(connection, report)


def test_fleet_coverage_counts_complete_current_snapshots_and_unknowns(tmp_path: Path) -> None:
    current_match = RepositoryIdentity(path=tmp_path / "current-match")
    current_non_match = RepositoryIdentity(path=tmp_path / "current-non-match")
    unknown = RepositoryIdentity(path=tmp_path / "unknown")
    service, store = _service(tmp_path, [current_match, current_non_match, unknown])
    _index(store, current_match, (("mlflow", "2.17.2"),), "a" * 40)
    _index(store, current_non_match, (("requests", "2.32.0"),), "b" * 40)

    coverage = service.fleet_coverage()

    assert coverage.repository_total == 3
    assert coverage.current_dependency_evidence == 2
    assert coverage.no_current_dependency_evidence == 1
    assert coverage.missing_dependency_evidence == 1


def test_overview_separates_current_non_match_from_no_current_evidence(tmp_path: Path) -> None:
    matching = RepositoryIdentity(path=tmp_path / "matching")
    non_matching = RepositoryIdentity(path=tmp_path / "non-matching")
    unknown = RepositoryIdentity(path=tmp_path / "unknown")
    service, store = _service(tmp_path, [matching, non_matching, unknown])
    _index(store, matching, (("mlflow", "2.17.2"),), "a" * 40)
    _index(store, non_matching, (("requests", "2.32.0"),), "b" * 40)

    overview = service.overview("MLFlow")

    assert overview.package_name == "mlflow"
    assert overview.observed_repositories == 1
    assert overview.freshness.current == 1
    assert overview.freshness.no_matching_dependency == 1
    assert overview.freshness.no_current_evidence == 1


def test_materialized_selection_is_explicit_sorted_and_immutable(tmp_path: Path) -> None:
    first = RepositoryIdentity(path=tmp_path / "z-repository")
    second = RepositoryIdentity(path=tmp_path / "a-repository")
    service, store = _service(tmp_path, [first, second])
    _index(store, first, (("mlflow", "2.17.2"),), "a" * 40)
    _index(store, second, (("mlflow", "2.17.1"),), "b" * 40)

    query = DependencyTargetFilter(package_name="mlflow", evidence="current")
    population = service.repositories("mlflow", evidence="current")
    selection = service.materialize(DependencyTargetMaterializeRequest(filter=query, expected_population_id=population.population_id))
    assert selection.repo_ids == tuple(sorted((first.repository_id, second.repository_id)))
    assert service.target_selection(selection.query_id) == selection

    _index(store, first, (("requests", "2.32.1"),), "c" * 40)
    new_population = service.repositories("mlflow", evidence="current")
    new_selection = service.materialize(DependencyTargetMaterializeRequest(filter=query, expected_population_id=new_population.population_id))

    assert service.target_selection(selection.query_id).repo_ids == selection.repo_ids
    assert new_selection.repo_ids == (second.repository_id,)
    assert new_selection.query_id != selection.query_id

    import pytest
    from oh_goon.core.errors import StateConflict
    with pytest.raises(StateConflict, match="population changed"):
        service.materialize(DependencyTargetMaterializeRequest(filter=query, expected_population_id=population.population_id))



def test_materialized_population_changes_when_same_repository_id_gets_new_instance(tmp_path: Path) -> None:
    import shutil

    repository = RepositoryIdentity(path=tmp_path / "repository")
    service, store = _service(tmp_path, [repository])
    _index(store, repository, (("mlflow", "2.17.2"),), "a" * 40)

    query = DependencyTargetFilter(package_name="mlflow", evidence="current")
    first_population = service.repositories("mlflow", evidence="current")
    first_selection = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=query, expected_population_id=first_population.population_id
        )
    )

    first_instance_id = first_selection.repository_instance_ids[repository.repository_id]
    shutil.rmtree(repository.path / ".git")
    (repository.path / ".git").mkdir()

    second_population = service.repositories("mlflow", evidence="current")

    assert second_population.population_id != first_population.population_id
    second_selection = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=query, expected_population_id=second_population.population_id
        )
    )
    assert second_selection.repository_instance_ids[repository.repository_id] != first_instance_id
    assert service.target_selection(first_selection.query_id) == first_selection

def test_coverage_populations_are_reviewable_and_materialize_exact_ids(tmp_path: Path) -> None:
    matching = RepositoryIdentity(path=tmp_path / "matching")
    non_matching = RepositoryIdentity(path=tmp_path / "non-matching")
    unknown = RepositoryIdentity(path=tmp_path / "unknown")
    service, store = _service(tmp_path, [matching, non_matching, unknown])
    _index(store, matching, (("mlflow", "2.17.2"),), "a" * 40)
    _index(store, non_matching, (("requests", "2.32.0"),), "b" * 40)

    proven = service.coverage_repositories("mlflow", coverage="proven_no_match")
    missing = service.coverage_repositories("mlflow", coverage="no_current_evidence")

    assert [item.repository.repository_id for item in proven.items] == [non_matching.repository_id]
    assert proven.items[0].evidence_state == "current"
    assert proven.items[0].sources == ("uv.lock",)
    assert [item.repository.repository_id for item in missing.items] == [unknown.repository_id]
    assert missing.items[0].evidence_state == "missing"
    assert missing.items[0].fleet_run_id is None

    proven_selection = service.materialize(DependencyTargetMaterializeRequest(
        filter=DependencyTargetFilter(package_name="mlflow", coverage="proven_no_match"),
        expected_population_id=proven.population_id,
    ))
    missing_selection = service.materialize(DependencyTargetMaterializeRequest(
        filter=DependencyTargetFilter(package_name="mlflow", coverage="no_current_evidence"),
        expected_population_id=missing.population_id,
    ))
    assert proven_selection.repo_ids == (non_matching.repository_id,)
    assert missing_selection.repo_ids == (unknown.repository_id,)


def test_nonmatching_coverage_rejects_match_specific_filters() -> None:
    import pytest

    with pytest.raises(ValueError, match="Coverage populations"):
        DependencyTargetFilter(
            package_name="mlflow", coverage="no_current_evidence", evidence="current"
        )


def test_advisory_range_keeps_uncomparable_versions_out_of_safe_count(tmp_path: Path) -> None:
    affected = RepositoryIdentity(path=tmp_path / "affected")
    safe = RepositoryIdentity(path=tmp_path / "safe")
    unknown_version = RepositoryIdentity(path=tmp_path / "unknown-version")
    absent = RepositoryIdentity(path=tmp_path / "absent")
    service, store = _service(tmp_path, [affected, safe, unknown_version, absent])
    _index(store, affected, (("mlflow", "2.17.2"),), "a" * 40)
    _index(store, safe, (("mlflow", "2.18.0"),), "b" * 40)
    _index(store, unknown_version, (("mlflow", "2.18.0rc1"),), "c" * 40)
    _index(store, absent, (("requests", "2.32.0"),), "d" * 40)

    advisory = service.advisory(
        "mlflow", advisory_id="CVE-2026-TEST", affected_specifier=">=2.0,<2.18.0"
    )

    assert advisory.affected_repositories == 1
    assert advisory.observed_not_affected == 1
    assert advisory.version_unclassified == 1
    assert advisory.no_matching_dependency == 1
    query = DependencyTargetFilter(
        package_name="mlflow",
        version_specifier=">=2.0,<2.18.0",
        advisory_id="CVE-2026-TEST",
        evidence="current",
    )
    population = service.repositories(
        "mlflow",
        version_specifier=">=2.0,<2.18.0",
        advisory_id="CVE-2026-TEST",
        evidence="current",
    )
    selected = service.materialize(DependencyTargetMaterializeRequest(filter=query, expected_population_id=population.population_id))
    assert selected.repo_ids == (affected.repository_id,)
    assert selected.filter.advisory_id == "CVE-2026-TEST"


def test_dependency_search_total_and_offset_describe_full_matching_population(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "many-packages")
    service, store = _service(tmp_path, [repository])
    packages = tuple((f"package-{index:03d}", f"1.{index}.0") for index in range(35))
    _index(store, repository, packages, "e" * 40)

    first = service.search("package-", offset=0, limit=20)
    second = service.search("package-", offset=20, limit=10)
    last = service.search("package-", offset=30, limit=10)

    assert first.total == second.total == last.total == 35
    assert first.offset == 0 and first.limit == 20 and len(first.items) == 20
    assert second.offset == 20 and second.limit == 10 and len(second.items) == 10
    assert last.offset == 30 and len(last.items) == 5
    assert {item.package_name for item in first.items}.isdisjoint(
        {item.package_name for item in second.items}
    )
