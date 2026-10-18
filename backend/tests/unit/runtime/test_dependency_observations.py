from __future__ import annotations

from datetime import timedelta
from pathlib import Path

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
        dependency_snapshot(("mlflow", "2.17.2"), ("requests", "2.32.0")),
        state="a" * 40,
    )
    second = dependency_report(
        repository, dependency_snapshot(("requests", "2.32.1")), state="b" * 40
    )

    with database.transaction(write=True) as connection:
        store._index_report(connection, first)
    assert len(store.package_rows("mlflow")) == 1

    with database.transaction(write=True) as connection:
        store._index_report(connection, second)
    assert store.package_rows("mlflow") == []
    assert [row["resolved_version"] for row in store.package_rows("requests")] == ["2.32.1"]
    assert repository.repository_id in store.observed_repository_ids()


def test_newer_repository_state_marks_dependency_evidence_stale(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "observations.db")
    store = DependencyObservationStore(database, Journal(database))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    observed = dependency_report(
        repository, dependency_snapshot(("mlflow", "2.17.2")), state="a" * 40
    )

    with database.transaction(write=True) as connection:
        store._index_report(connection, observed)
        connection.execute(
            "UPDATE repository_observation_heads SET repository_state_id=? WHERE repository_id=?",
            ("state-newer", repository.repository_id),
        )

    row = store.package_rows("mlflow")[0]
    assert store.evidence_state(row) == "stale"


def test_old_matching_snapshot_is_aged_not_silently_current(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "observations.db")
    store = DependencyObservationStore(database, Journal(database))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    observed = dependency_report(
        repository, dependency_snapshot(("mlflow", "2.17.2")), state="a" * 40
    )

    with database.transaction(write=True) as connection:
        store._index_report(connection, observed)
    row = store.package_rows("mlflow")[0]
    assert (
        store.evidence_state(row, now=observed.provenance.finished_at + timedelta(days=8)) == "aged"
    )
