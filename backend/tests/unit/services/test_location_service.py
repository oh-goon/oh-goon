from pathlib import Path

import pytest

from oh_goon.core.errors import StateConflict

from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.locations import LocationService
from tests.factories.journal import commit_operation
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report


def test_location_change_does_not_destroy_operation_history(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    identity = RepositoryIdentity(name="repo", path=repository)
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    journal = state.journal
    commit_operation(
        journal,
        make_operation_report(
            identity, OperationAction.INIT, operation_id="op-history", fleet_run_id=None
        ),
    )
    service = LocationService(state)

    service.add(tmp_path)

    assert journal.require_operation("op-history").repository == identity


def test_remove_requires_exact_displayed_location_revision(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    state = local_state(LocalDatabase(tmp_path / "locations.db"))
    service = LocationService(state)
    service.add(first)
    _, _, displayed_revision = service.snapshot()
    service.add(second)

    with pytest.raises(StateConflict, match="locations changed"):
        service.remove(first, expected_revision=displayed_revision)

    locations, _, _ = service.snapshot()
    assert locations == [first.resolve(), second.resolve()]
