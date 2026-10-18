from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import JournalConflict
from oh_goon.core.models.effect_identity import RepositoryEffectIdentity
from oh_goon.core.models.effect_origin import DirectEffectOrigin
from oh_goon.core.models.reconciliation import (
    ConfirmedNotAppliedRepositoryReconciliation,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import (
    EffectAttemptOwner,
    OperationAction,
    ReconciliationOutcome,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import capability_snapshot
from tests.factories.journal import commit_operation
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_state import baseline_for


def test_journal_refuses_reconciliation_after_canonical_operation_exists(tmp_path: Path) -> None:
    repository = tmp_path / "repo-existing-operation"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    identity = RepositoryIdentity(path=repository)
    operation_id = "op-existing-canonical"
    journal = Journal(LocalDatabase(tmp_path / "reconciliation-existing-operation.db"))
    commit_operation(
        journal,
        make_operation_report(
            identity,
            OperationAction.UPDATE,
            operation_id=operation_id,
            fleet_run_id=None,
        ),
    )
    state = baseline_for(repository)
    evidence = RepositoryEffectReconciliationEvidence(
        effect=RepositoryEffectIdentity(
            operation_id=operation_id,
            repository=identity,
            capability=capability_snapshot(OperationAction.UPDATE),
            origin=DirectEffectOrigin(kind=EffectAttemptOwner.DIRECT),
        ),
        reconciliation=ConfirmedNotAppliedRepositoryReconciliation(
            outcome=ReconciliationOutcome.CONFIRMED_NOT_APPLIED,
            reason="repository remains at admitted baseline",
            baseline=state,
            current=state,
        ),
    )

    with journal._database.transaction(write=True) as connection:
        with pytest.raises(JournalConflict, match="canonical report already exists"):
            journal.commit_effect_reconciliation_in(connection, evidence)
