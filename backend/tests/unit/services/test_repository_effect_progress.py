from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import ExecutorPlacementLost
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.vocabulary import EffectBoundaryKind, FleetScope, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import admit_test_run, capability_snapshot
from tests.factories.journal import start_run
from tests.factories.repository_effect_runtime import effect_execution
from tests.factories.repository_state import baseline_for


def test_fleet_preclaim_activity_localizes_identity_and_claim_boundaries(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    identity = RepositoryIdentity(name=repository.name, path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    admit_test_run(journal, "fleet-progress", OperationAction.UPDATE, [identity])
    start_run(journal, "fleet-progress")
    ticket = execution.reserve_fleet_with(
        "fleet-progress",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]

    class GitStub:
        def fingerprint(self, path, _cancel_requested):
            return baseline_for(path)

    class ExecutorStub:
        def effect_boundary(self, _intent):
            return EffectBoundaryKind.EXTERNAL

    effects = effect_execution(
        database,
        ExecutorStub(),  # type: ignore[arg-type]
        journal,
        attempts,
        GitStub(),  # type: ignore[arg-type]
    )
    session = execution.activate(ticket)
    activity: list[str] = []

    def reject_placement(_connection) -> None:
        raise ExecutorPlacementLost("executor trust was revoked")

    with pytest.raises(ExecutorPlacementLost, match="trust was revoked"):
        effects.execute_fleet_target(
            resolve_capability_intent(OperationAction.UPDATE),
            capability_snapshot(OperationAction.UPDATE),
            entry,
            session,
            validate_authority_in=reject_placement,
            activity=activity.append,
        )
    session.finish(lambda _connection: None)

    assert activity == [
        "Observing repository identity for repo",
        "Repository identity observed for repo",
        "Claiming repository execution attempt for repo",
    ]
