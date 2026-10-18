from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from oh_goon.core.errors import StateConflict
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.models.repository import (
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
    RepositoryIdentity,
)
from oh_goon.core.vocabulary.fleet import FleetScope
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.exclusions import write_exclusion
from oh_goon.runtime.state import LocalState
from oh_goon.services.repositories import RepositoryService
from tests.factories.local_state import local_state


class _OneCutDiscovery:
    def __init__(self, entry: RepositoryCatalogEntry) -> None:
        self.entry = entry
        self.calls = 0

    def discover(self, locations, exclusions):
        del locations, exclusions
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("filesystem changed after durable commit")
        return [self.entry]


def _service(tmp_path: Path) -> tuple[RepositoryService, _OneCutDiscovery, LocalState, Path]:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    identity = RepositoryIdentity(name="repo", path=repository)
    discovery = _OneCutDiscovery(
        IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    )
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    return RepositoryService(state, discovery), discovery, state, repository


def test_exclude_response_uses_admitted_repository_identity_without_rediscovery(
    tmp_path: Path,
) -> None:
    service, discovery, _state, repository = _service(tmp_path)

    result = service.exclude(str(repository), "maintenance", repository_subject_instance_id(repository))

    assert discovery.calls == 1
    assert result.repository.path == repository.resolve()
    assert result.scope is FleetScope.EXCLUDED
    assert result.exclusion_reason == "maintenance"
    assert result.excluded_at is not None


def test_include_response_uses_admitted_repository_identity_without_rediscovery(
    tmp_path: Path,
) -> None:
    service, discovery, state, repository = _service(tmp_path)

    # Seed the durable exclusion without consuming the discovery cut.
    state.exclude(
        repository,
        "maintenance",
        expected_durable_revision=state.read_view().durable_revision,
    )
    result = service.include(str(repository), repository_subject_instance_id(repository))

    assert discovery.calls == 1
    assert result.repository.path == repository.resolve()
    assert result.scope is FleetScope.INCLUDED
    assert result.exclusion_reason is None
    assert result.excluded_at is None


def _lose_first_write_response(database: LocalDatabase, message: str):
    original_transaction = database.transaction
    raised = False

    @contextmanager
    def ambiguous_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal raised
        with original_transaction(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and not raised:
            raised = True
            raise RuntimeError(message)

    return ambiguous_transaction


def test_exclude_replays_exact_record_after_ambiguous_commit_response(tmp_path: Path) -> None:
    service, _discovery, state, repository = _service(tmp_path)
    state.database.transaction = _lose_first_write_response(
        state.database, "exclude commit response lost after commit"
    )  # type: ignore[method-assign]

    result = service.exclude(str(repository), "maintenance", repository_subject_instance_id(repository))

    assert result.exclusion_reason == "maintenance"
    assert state.exclusions() == [
        {
            "path": str(repository.resolve()),
            "reason": "maintenance",
            "excluded_at": result.excluded_at.isoformat(),
        }
    ]


def test_include_replays_exact_absence_after_ambiguous_commit_response(tmp_path: Path) -> None:
    service, _discovery, state, repository = _service(tmp_path)
    state.exclude(
        repository,
        "maintenance",
        expected_durable_revision=state.read_view().durable_revision,
    )
    state.database.transaction = _lose_first_write_response(
        state.database, "include commit response lost after commit"
    )  # type: ignore[method-assign]

    result = service.include(str(repository), repository_subject_instance_id(repository))

    assert result.scope is FleetScope.INCLUDED
    assert state.exclusions() == []


def test_exclude_does_not_replay_after_later_scope_revision(tmp_path: Path) -> None:
    service, _discovery, state, repository = _service(tmp_path)
    database = state.database
    original_transaction = database.transaction
    injected = False

    @contextmanager
    def superseded_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal injected
        with original_transaction(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and not injected:
            injected = True
            with original_transaction(write=True) as connection:
                write_exclusion(connection, repository, "newer operator decision")
            raise RuntimeError("exclude response lost and later revision committed")

    database.transaction = superseded_transaction  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="later revision committed"):
        service.exclude(str(repository), "maintenance", repository_subject_instance_id(repository))

    assert state.exclusions()[0]["reason"] == "newer operator decision"


def test_exclude_precommit_failure_does_not_replay(tmp_path: Path) -> None:
    service, _discovery, state, repository = _service(tmp_path)
    database = state.database
    original_transaction = database.transaction

    @contextmanager
    def failing_transaction(*, write: bool = False, track_revision: bool = True):
        with original_transaction(write=write, track_revision=track_revision) as connection:
            yield connection
            if write:
                raise RuntimeError("exclude failed before commit")

    database.transaction = failing_transaction  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="before commit"):
        service.exclude(str(repository), "maintenance", repository_subject_instance_id(repository))

    assert state.exclusions() == []


def test_scope_mutation_rejects_recreated_repository_at_same_path(tmp_path: Path) -> None:
    service, _discovery, _state, repository = _service(tmp_path)
    expected_instance = repository_subject_instance_id(repository)

    (repository / ".git").rmdir()
    repository.rmdir()
    repository.mkdir()
    (repository / ".git").mkdir()

    with pytest.raises(StateConflict, match="Repository instance changed"):
        service.exclude(str(repository), "maintenance", expected_instance)
