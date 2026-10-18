from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from oh_goon.core.capability_registry import CapabilityRegistry, capability_definitions
from oh_goon.core.models.effect_attempt import (
    OutcomeUnknownRepositoryEffectAttempt,
    RepositoryEffectAttempt,
    effect_attempt_reconciliation,
)
from oh_goon.core.models.reconciliation import PartiallyAppliedRepositoryReconciliation
from oh_goon.core.vocabulary import (
    EffectAttemptStatus,
    EffectBoundaryKind,
    OperationAction,
    ReconciliationOutcome,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from tests.factories.capabilities import capability_snapshot
from tests.factories.repository_state import baseline_for


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return repository


def _claim_direct(
    database: LocalDatabase, attempts: RepositoryEffectAttemptStore, repository: Path
):
    with database.transaction(write=True) as connection:
        return attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )


def test_unresolved_effect_attempt_has_transition_time_but_no_resolution_time(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    repository = _repository(tmp_path)
    attempts = RepositoryEffectAttemptStore(database)
    claimed = _claim_direct(database, attempts, repository)
    with database.transaction(write=True) as connection:
        attempts.start_effect_in(connection, claimed.operation_id)
        unknown = attempts.mark_unknown_in(
            connection, claimed.operation_id, note="effect outcome unknown"
        )

    assert isinstance(unknown, OutcomeUnknownRepositoryEffectAttempt)
    assert unknown.updated_at >= unknown.started_at
    assert not hasattr(unknown, "resolved_at")


def test_rereconciliation_preserves_previous_evidence(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    repository = _repository(tmp_path)
    attempts = RepositoryEffectAttemptStore(database)
    claimed = _claim_direct(database, attempts, repository)
    with database.transaction(write=True) as connection:
        attempts.start_effect_in(connection, claimed.operation_id)
        attempts.mark_unknown_in(connection, claimed.operation_id, note="effect outcome unknown")
    observed = attempts.get(claimed.operation_id)
    result = PartiallyAppliedRepositoryReconciliation(
        outcome=ReconciliationOutcome.PARTIALLY_APPLIED,
        reason="repository changed but canonical result is absent",
        baseline=claimed.baseline,
        current=baseline_for(repository),
    )
    with database.transaction(write=True) as connection:
        partial = attempts.apply_reconciliation_in(
            connection,
            claimed.operation_id,
            result,
            expected_revision=observed.record_version,
        )
    assert partial is not None
    reread = attempts.get(partial.operation_id)

    assert reread is not partial
    assert effect_attempt_reconciliation(reread) == result
    assert not hasattr(reread, "resolved_at")


def test_effect_attempt_sql_contains_only_unresolved_coordination_columns(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    with database.transaction() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(repository_effect_attempts)")
        }

    assert "resolved_at" not in columns


def test_effect_attempt_sql_requires_reconciliation_for_applied_or_partial_states(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    repository = _repository(tmp_path)
    attempts = RepositoryEffectAttemptStore(database)
    claimed = _claim_direct(database, attempts, repository)

    for status in ("PARTIALLY_APPLIED", "INDETERMINATE"):
        with pytest.raises(sqlite3.IntegrityError):
            with database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE repository_effect_attempts SET status=? WHERE operation_id=?",
                    (status, claimed.operation_id),
                )


def test_generic_attempt_union_rejects_operator_states_without_evidence() -> None:
    adapter = TypeAdapter(RepositoryEffectAttempt)
    base = {
        "operation_id": "op-1",
        "repository_id": "repo-1",
        "repository_path": "/tmp/repo-1",
        "capability": capability_snapshot(OperationAction.UPDATE),
        "origin": {"kind": "DIRECT"},
        "baseline": {
            "state_id": "state-1",
            "head_sha": None,
            "branch": None,
            "pyproject_sha256": None,
            "uv_lock_sha256": None,
            "worktree_dirty": False,
        },
        "started_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    with pytest.raises(ValidationError):
        adapter.validate_python({**base, "status": EffectAttemptStatus.PARTIALLY_APPLIED})
    with pytest.raises(ValidationError):
        adapter.validate_python({**base, "status": "ACKNOWLEDGED"})


def test_capability_registry_rejects_ambiguous_intent_key() -> None:
    definitions = capability_definitions()
    duplicate_intent = replace(definitions[0], capability_id="alternate-init")
    with pytest.raises(ValueError, match="Capability intent must resolve uniquely"):
        CapabilityRegistry((*definitions, duplicate_intent))
