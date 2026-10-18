from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import OperationNotFound
from oh_goon.core.vocabulary import EffectBoundaryKind, OperationAction
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import capability_snapshot
from tests.factories.repository_effect_runtime import effect_recovery
from tests.factories.repository_state import baseline_for


def test_started_contained_effect_is_retired_without_reconciliation_or_quarantine(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "contained.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.CONTAINED,
        )
        attempts.start_effect_in(connection, attempt.operation_id)

    effect_recovery(
        database,
        attempts,
        journal,
        ExecutionCoordinator(database),
        GitService(),
    ).recover()

    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)
    assert journal.history(repository) == []
