from __future__ import annotations

import json
from pathlib import Path

from oh_goon.core.models.fleet_observation import ObservationSourceEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.dependency_observations import DependencyObservationStore
from oh_goon.runtime.journal import Journal
from tests.factories.dependency_observations import dependency_report, dependency_snapshot


def test_complete_snapshot_atomically_replaces_removed_dependency(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "observations.db")
    store = DependencyObservationStore(database, Journal(database))
    repository = RepositoryIdentity(path=tmp_path / "repo")

    first = dependency_report(
        repository,
        dependency_snapshot(repository.path, ("mlflow", "2.17.2"), ("requests", "2.32.0")),
        state="a" * 40,
    )
    second = dependency_report(
        repository, dependency_snapshot(repository.path, ("requests", "2.32.1")), state="b" * 40
    )

    with database.transaction(write=True) as connection:
        store._index_report(connection, first)
    assert len(store.package_rows("mlflow")) == 1

    with database.transaction(write=True) as connection:
        store._index_report(connection, second)
    assert store.package_rows("mlflow") == []
    assert [row["resolved_version"] for row in store.package_rows("requests")] == ["2.32.1"]
    assert repository.repository_id in store.observed_repository_ids()
    reopened_database = LocalDatabase(database.path)
    reopened = DependencyObservationStore(reopened_database, Journal(reopened_database))
    persisted = reopened.snapshot_rows()[0]["snapshot_json"]
    assert tuple(
        ObservationSourceEvidence.model_validate(item) for item in json.loads(persisted)["sources"]
    ) == (second.evidence.plan.steps[0].observations[0].sources[0],)


def test_lock_edit_invalidates_evidence_without_another_git_observation(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "observations.db")
    store = DependencyObservationStore(database, Journal(database))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    snapshot = dependency_snapshot(repository.path, ("mlflow", "2.17.2"))
    observed = dependency_report(repository, snapshot, state="a" * 40)
    with database.transaction(write=True) as connection:
        store._index_report(connection, observed)
    assert store.evidence_state(store.package_rows("mlflow")[0]) == "current"
    lock = repository.path / "uv.lock"
    lock.write_text(lock.read_text().replace("2.17.2", "2.17.3"))
    assert store.evidence_state(store.package_rows("mlflow")[0]) == "stale"


def test_manifest_edits_do_not_expand_lock_inventory_claims(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "observations.db")
    store = DependencyObservationStore(database, Journal(database))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    snapshot = dependency_snapshot(repository.path, ("mlflow", "2.17.2"))
    observed = dependency_report(repository, snapshot, state="a" * 40)
    with database.transaction(write=True) as connection:
        store._index_report(connection, observed)
    (repository.path / "pyproject.toml").write_text('[project]\ndependencies=["new-package"]\n')
    assessment = store.assessment(store.package_rows("mlflow")[0])
    assert assessment.freshness == "current"
    assert assessment.scope == "locked-inventory"
    (repository.path / "uv.lock").unlink()
    assert not store.assessment(store.package_rows("mlflow")[0]).absence_admissible
