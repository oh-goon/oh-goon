from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.cli import main as cli_main
from oh_goon.core.errors import ErrorKind, ExecutionBusy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.product_identity import canonical_repository_path, repository_id
from oh_goon.core.vocabulary import FleetRunFailureStage, FleetRunTerminalIntent, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import start_run, terminalize_run


def test_repository_identity_is_stable_for_equivalent_canonical_paths(tmp_path: Path) -> None:
    repository = tmp_path / "repos" / "api"
    repository.mkdir(parents=True)
    equivalent = repository.parent / "." / "api"

    assert canonical_repository_path(equivalent) == repository.resolve()
    assert repository_id(equivalent) == repository_id(repository)
    assert repository_id(repository).startswith("repo-")


def test_execution_coordinator_never_implicitly_releases_admitted_work(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    coordinator = ExecutionCoordinator(database)
    ticket = coordinator.reserve_direct_with(
        expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
    )[0]
    session = coordinator.activate(ticket)

    with pytest.raises(RuntimeError, match="boom"):
        raise RuntimeError("boom")

    with pytest.raises(ExecutionBusy):
        coordinator.reserve_direct_with(
            expected_durable_revision=database.durable_revision(),
            mutate=lambda _connection: None,
        )
    session.finish(lambda _connection: None)


def test_execution_session_treats_lost_sqlite_lease_as_cancellation(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    coordinator = ExecutionCoordinator(database)
    ticket = coordinator.reserve_fleet_with(
        "fleet-test",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]

    session = coordinator.activate(ticket)
    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM execution_lease WHERE singleton = 1")
    assert session.cancel_requested() is True


def test_dev_explain_run_traces_durable_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database_path = tmp_path / "state.db"
    monkeypatch.setenv("OH_GOON_DB", str(database_path))
    database = LocalDatabase(database_path)
    journal = Journal(database)
    repository = RepositoryIdentity(name="api", path=tmp_path / "api")

    admit_test_run(journal, "fleet-trace", OperationAction.INIT, [repository])
    start_run(journal, "fleet-trace")
    terminalize_run(
        journal,
        "fleet-trace",
        FleetRunTerminalIntent.FAIL,
        FleetRunFailure(
            code="FIXTURE_FAILURE",
            kind=ErrorKind.INTERNAL,
            stage=FleetRunFailureStage.EXECUTION,
            message="fixture failure",
        ),
    )

    assert cli_main(["dev", "explain-run", "fleet-trace"]) == 0
    output = capsys.readouterr().out
    assert "Run: fleet-trace" in output
    assert "Capability: init" in output
    assert "RUN_ADMITTED" in output
    assert "RUN_FAILED" in output
