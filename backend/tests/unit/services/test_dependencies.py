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
    report = dependency_report(
        repository, dependency_snapshot(repository.path, *packages), state=state
    )
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
    assert overview.query_identity is not None
    assert overview.query_identity.requested_name == "MLFlow"
    assert overview.query_identity.canonical_name == "mlflow"


def test_materialized_selection_is_explicit_sorted_and_immutable(tmp_path: Path) -> None:
    first = RepositoryIdentity(path=tmp_path / "z-repository")
    second = RepositoryIdentity(path=tmp_path / "a-repository")
    service, store = _service(tmp_path, [first, second])
    _index(store, first, (("mlflow", "2.17.2"),), "a" * 40)
    _index(store, second, (("mlflow", "2.17.1"),), "b" * 40)

    query = DependencyTargetFilter(package_name="mlflow", evidence="current")
    population = service.repositories("mlflow", evidence="current")
    selection = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=query, expected_population_id=population.population_id
        )
    )
    assert selection.repo_ids == tuple(sorted((first.repository_id, second.repository_id)))
    assert service.target_selection(selection.query_id) == selection

    _index(store, first, (("requests", "2.32.1"),), "c" * 40)
    new_population = service.repositories("mlflow", evidence="current")
    new_selection = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=query, expected_population_id=new_population.population_id
        )
    )

    assert service.target_selection(selection.query_id).repo_ids == selection.repo_ids
    assert new_selection.repo_ids == (second.repository_id,)
    assert new_selection.query_id != selection.query_id

    import pytest

    from oh_goon.core.errors import StateConflict

    with pytest.raises(StateConflict, match="population changed"):
        service.materialize(
            DependencyTargetMaterializeRequest(
                filter=query, expected_population_id=population.population_id
            )
        )


def test_materialized_population_changes_when_same_repository_id_gets_new_instance(
    tmp_path: Path,
) -> None:
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

    proven = service.coverage_repositories("mlflow", coverage="absent_from_lock")
    missing = service.coverage_repositories("mlflow", coverage="no_current_evidence")

    assert [item.repository.repository_id for item in proven.items] == [non_matching.repository_id]
    assert proven.items[0].evidence.freshness == "current"
    assert proven.items[0].sources == ("uv.lock",)
    assert [item.repository.repository_id for item in missing.items] == [unknown.repository_id]
    assert missing.items[0].evidence.availability == "missing"
    assert missing.items[0].fleet_run_id is None

    proven_selection = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=DependencyTargetFilter(package_name="mlflow", coverage="absent_from_lock"),
            expected_population_id=proven.population_id,
        )
    )
    missing_selection = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=DependencyTargetFilter(package_name="mlflow", coverage="no_current_evidence"),
            expected_population_id=missing.population_id,
        )
    )
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
    _index(store, unknown_version, (("mlflow", "not-a-version"),), "c" * 40)
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
    selected = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=query, expected_population_id=population.population_id
        )
    )
    assert selected.repo_ids == (affected.repository_id,)
    assert selected.filter.advisory_id == "CVE-2026-TEST"


def test_dependency_search_total_and_offset_describe_full_matching_population(
    tmp_path: Path,
) -> None:
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


def test_missing_index_row_cannot_become_native_lock_absence(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repository")
    service, store = _service(tmp_path, [repository])
    _index(store, repository, (("mlflow", "2.17.2"),), "a" * 40)
    with store._database.transaction(write=True) as connection:
        connection.execute("DELETE FROM dependency_observations WHERE package_name='mlflow'")
    assert service.coverage_repositories("mlflow", coverage="absent_from_lock").total == 0
    assert service.overview("mlflow").freshness.no_matching_dependency == 0


def test_ecosystem_and_scoped_identity_survive_fleet_readback_and_predicate_selection(
    tmp_path: Path,
) -> None:
    repository = RepositoryIdentity(path=tmp_path / "mixed")
    service, store = _service(tmp_path, [repository])
    python = dependency_snapshot(repository.path, ("shared", "1.4.0"))
    node = dependency_snapshot(
        repository.path, ("shared", "2.1.0"), ("@scope/shared", "1.5.0"), ecosystem="npm"
    )
    with store._database.transaction(write=True) as connection:
        for snapshot, state in ((python, "a" * 40), (node, "b" * 40)):
            store._index_report(connection, dependency_report(repository, snapshot, state=state))

    search = service.search("shared")
    assert {(item.ecosystem, item.package_name) for item in search.items} == {
        ("pypi", "shared"),
        ("npm", "shared"),
        ("npm", "@scope/shared"),
    }
    assert [
        item.resolved_versions for item in service.repositories("shared", ecosystem="pypi").items
    ] == [("1.4.0",)]
    assert [
        item.resolved_versions for item in service.repositories("shared", ecosystem="npm").items
    ] == [("2.1.0",)]
    python_match = service.repositories("shared", ecosystem="pypi").items[0]
    node_match = service.repositories("shared", ecosystem="npm").items[0]
    assert python_match.component_ids == (python.dependencies[0].component_id,)
    assert node_match.component_ids == ("shared",)
    assert (python_match.ecosystem, python_match.component_ids) != (
        node_match.ecosystem,
        node_match.component_ids,
    )
    assert python_match.selection_ids == (python.dependencies[0].node_id,)
    assert node_match.selection_ids == tuple(
        item.node_id for item in node.dependencies if item.package_name == "shared"
    )
    assert python_match.contexts and node_match.contexts
    assert python_match.evidence.producer_assessment["repository_binding"]

    query = DependencyTargetFilter(
        package_name="@scope/shared",
        ecosystem="npm",
        version_specifier="^1.0.0",
        evidence="current",
    )
    population = service.repositories(
        "@scope/shared", ecosystem="npm", version_specifier="^1.0.0", evidence="current"
    )
    predicate = population.items[0].producer_projection["packages"][0]["version_predicate"]
    assert predicate["predicate"]["dialect"] == "npm-semver"
    assert predicate["counts"]["match"] == 1 and predicate["predicate_identity"]
    selection = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=query, expected_population_id=population.population_id
        )
    )
    assert selection.repo_ids == (repository.repository_id,)
    reopened = DependencyTargetMaterializationStore(LocalDatabase(store._database.path))
    assert reopened.get(selection.query_id).filter.ecosystem == "npm"
    assert reopened.get(selection.query_id).filter.package_name == "@scope/shared"

    advisory = service.advisory(
        "shared", ecosystem="npm", advisory_id="TEST", affected_specifier="^1.0.0"
    )
    assert advisory.affected_repositories == 0 and advisory.observed_not_affected == 1
    assert advisory.producer_predicates[0]["engine"]["name"] == "node-semver"
    assert (
        service.coverage_repositories(
            "@scope/shared", ecosystem="pypi", coverage="absent_from_lock"
        ).total
        == 1
    )
    assert (
        service.coverage_repositories(
            "@scope/shared", ecosystem="npm", coverage="absent_from_lock"
        ).total
        == 0
    )


def test_native_alias_queries_preserve_requested_text_and_canonical_populations(
    tmp_path: Path,
) -> None:
    repository = RepositoryIdentity(path=tmp_path / "mixed-aliases")
    service, store = _service(tmp_path, [repository])
    python = dependency_snapshot(repository.path, ("ML_Flow", "1.4.0"), ("mlflow", "9.0.0"))
    node = dependency_snapshot(
        repository.path,
        ("ML_Flow", "2.1.0"),
        ("MLXFlow", "3.0.0"),
        ("@Scope/ML_Flow", "4.0.0"),
        ecosystem="npm",
    )
    with store._database.transaction(write=True) as connection:
        for snapshot, state in ((python, "a" * 40), (node, "b" * 40)):
            store._index_report(connection, dependency_report(repository, snapshot, state=state))

    requested = " ML_Flow "
    search = service.search(requested)
    assert search.query == requested
    assert {(item.ecosystem, item.package_name) for item in search.items} == {
        ("pypi", "ml-flow"),
        ("npm", "ML_Flow"),
        ("npm", "@Scope/ML_Flow"),
    }
    assert {
        (identity.ecosystem, identity.requested_name, identity.canonical_name)
        for identity in search.query_identities
    } == {("pypi", requested, "ml-flow"), ("npm", requested, "ML_Flow")}
    assert service.search("ML_Flow", ecosystem="pypi").items[0].package_name == "ml-flow"
    assert service.repositories("ml-flow", ecosystem="npm").total == 0

    population = service.repositories(requested, ecosystem="pypi", evidence="current")
    assert population.total == 1 and population.package_name == "ml-flow"
    canonical = service.repositories("ml-flow", ecosystem="pypi", evidence="current")
    assert population.population_id == canonical.population_id
    assert population.items[0].resolved_versions == ("1.4.0",)
    selected = service.materialize(
        DependencyTargetMaterializeRequest(
            filter=DependencyTargetFilter(
                package_name=requested, ecosystem="pypi", evidence="current"
            ),
            expected_population_id=population.population_id,
        )
    )
    assert selected.filter.package_name == "ml-flow"
    assert selected.repo_ids == (repository.repository_id,)

    overview = service.overview(requested)
    coverage = service.coverage_repositories(requested, coverage="absent_from_lock")
    advisory = service.advisory(requested, advisory_id="TEST", affected_specifier="<2")
    for response in (population, overview, coverage, advisory):
        assert response.query_identity is not None
        assert response.query_identity.requested_name == requested
        assert response.query_identity.canonical_name == response.package_name == "ml-flow"
        assert response.query_identity.producer_evidence["schema"] == (
            "hashmarks.dependency-package-query-identity.v1"
        )
    assert advisory.affected_repositories == 1
    scoped = service.repositories("@Scope/ML_Flow", ecosystem="npm")
    assert scoped.package_name == "@Scope/ML_Flow" and scoped.total == 1
    assert scoped.query_identity is not None
    assert scoped.query_identity.producer_evidence["semantics"] == "opaque-exact-name.v1"


def test_truncated_native_package_projection_cannot_materialize_a_false_empty_population(
    tmp_path: Path,
) -> None:
    import json

    import pytest

    from oh_goon.core.errors import StateConflict
    from oh_goon.integrations.hashmarks import (
        observe_locked_dependencies,
        project_dependency_package,
    )

    repository = RepositoryIdentity(path=tmp_path / "bounded-inventory")
    service, store = _service(tmp_path, [repository])
    packages = {
        "": {"name": "fixture-application", "version": "1.0.0"},
        **{
            f"node_modules/parent{index:03}/node_modules/shared": {"version": "1.0.0"}
            for index in range(256)
        },
    }

    def capture(state: str):
        (repository.path / "package-lock.json").write_text(
            json.dumps(
                {
                    "name": "fixture-application",
                    "version": "1.0.0",
                    "lockfileVersion": 3,
                    "packages": packages,
                }
            )
        )
        observed = observe_locked_dependencies(repository.path, ecosystem="npm")
        assert observed.evidence.freshness == "current"
        assert observed.evidence.completeness == "complete"
        with store._database.transaction(write=True) as connection:
            store._index_report(connection, dependency_report(repository, observed, state=state))
        return observed

    baseline = capture("a" * 40)
    assert baseline.producer_projection["packages"][0]["truncation"] == "complete"
    requests = []
    for filters in (
        {"resolved_version": "9.0.0"},
        {"version_prefix": "9."},
        {"version_specifier": "=9.0.0"},
    ):
        population = service.repositories("shared", ecosystem="npm", evidence="current", **filters)
        assert population.total == 0
        request = DependencyTargetMaterializeRequest(
            filter=DependencyTargetFilter(
                package_name="shared", ecosystem="npm", evidence="current", **filters
            ),
            expected_population_id=population.population_id,
        )
        assert service.materialize(request).repo_ids == ()
        requests.append((filters, request))

    packages["node_modules/parent256/node_modules/shared"] = {"version": "9.0.0"}
    observed = capture("b" * 40)
    assert observed.producer_projection["truncation"] == "truncated"
    assert observed.producer_projection["packages"][0]["truncation"] == "truncated"
    assert len(observed.dependencies) == 256
    native_nodes = {row["node_id"] for row in observed.producer_observation["selections"]}
    assert len(native_nodes) == 257
    assert {item.node_id for item in observed.dependencies} < native_nodes
    assert {item.resolved_version for item in observed.dependencies} == {"1.0.0"}
    native = project_dependency_package(
        repository.path, observed, "shared", version_specifier="=9.0.0"
    ).projection
    assert native is not None
    predicate = native["packages"][0]["version_predicate"]
    assert predicate["counts"] == {"match": 1, "nonmatch": 256, "unclassified": 0}
    assert predicate["omitted_selection_count"] == 1
    assert native["packages"][0]["matches"][0]["selection"]["version"] == "9.0.0"

    for filters, request in requests:
        with pytest.raises(StateConflict, match="target population is unverified"):
            service.repositories("shared", ecosystem="npm", evidence="current", **filters)
        with pytest.raises(StateConflict, match="target population is unverified"):
            service.materialize(request)
        assert service.target_selection(request.expected_population_id).repo_ids == ()
    advisory = service.advisory(
        "shared", ecosystem="npm", advisory_id="TRUNCATED", affected_specifier="=9.0.0"
    )
    assert advisory.observed_not_affected == 0
    assert advisory.version_unclassified == 1
    assert (
        service.coverage_repositories("shared", ecosystem="npm", coverage="absent_from_lock").total
        == 0
    )


def test_version_prefix_is_literal_text_in_native_inventory_and_target_populations(
    tmp_path: Path,
) -> None:
    versions = ("9.0.0", "9_release", "9%release", "9\\release")
    repositories = [RepositoryIdentity(path=tmp_path / f"repository-{index}") for index in range(4)]
    service, store = _service(tmp_path, repositories)
    with store._database.transaction(write=True) as connection:
        for index, (repository, version) in enumerate(zip(repositories, versions, strict=True)):
            snapshot = dependency_snapshot(repository.path, ("shared", version), ecosystem="npm")
            assert snapshot.evidence.availability == "available"
            assert snapshot.dependencies[0].resolved_version == version
            store._index_report(
                connection,
                dependency_report(repository, snapshot, state=f"{index + 1:040x}"),
            )

    for prefix, expected in (
        ("9.", (repositories[0].repository_id,)),
        ("9_", (repositories[1].repository_id,)),
        ("9%", (repositories[2].repository_id,)),
        ("9\\", (repositories[3].repository_id,)),
        ("%", ()),
    ):
        population = service.repositories(
            "shared", ecosystem="npm", version_prefix=prefix, evidence="current"
        )
        assert tuple(item.repository.repository_id for item in population.items) == expected
        assert population.total == len(expected)
        selected = service.materialize(
            DependencyTargetMaterializeRequest(
                filter=DependencyTargetFilter(
                    package_name="shared",
                    ecosystem="npm",
                    version_prefix=prefix,
                    evidence="current",
                ),
                expected_population_id=population.population_id,
            )
        )
        assert selected.repo_ids == expected
        assert selected.filter.version_prefix == prefix
