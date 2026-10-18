from __future__ import annotations

from pathlib import Path

import pytest

import oh_goon.runtime.effect_attempts as effect_attempt_runtime
from oh_goon.core.effect_attempt_transitions import start_effect_attempt
from oh_goon.core.errors import OperationNotFound
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import EffectBoundaryKind, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.repository_effect_settlement import (
    RepositoryEffectInterruptionDisposition,
    RepositoryEffectSettlement,
)
from tests.factories.capabilities import capability_snapshot
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_state import baseline_for


def _stack(tmp_path: Path, boundary: EffectBoundaryKind):
    database = LocalDatabase(tmp_path / f"{boundary.value.lower()}.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / boundary.value.lower()
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=boundary,
        )
    return database, journal, attempts, repository, attempt


def test_claimed_interruption_retires_from_durable_attempt_state(tmp_path: Path) -> None:
    database, journal, attempts, _, attempt = _stack(tmp_path, EffectBoundaryKind.EXTERNAL)
    owner = RepositoryEffectSettlement(database, journal, attempts)

    with database.transaction(write=True) as connection:
        disposition = owner.settle_interruption_in(
            connection, attempt.operation_id, unknown_note="crash"
        )

    assert disposition is RepositoryEffectInterruptionDisposition.RESOLVED
    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)


def test_started_contained_interruption_is_retry_safe(tmp_path: Path) -> None:
    database, journal, attempts, _, attempt = _stack(tmp_path, EffectBoundaryKind.CONTAINED)
    owner = RepositoryEffectSettlement(database, journal, attempts)
    with database.transaction(write=True) as connection:
        attempts.start_effect_in(connection, attempt.operation_id)
        disposition = owner.settle_interruption_in(
            connection, attempt.operation_id, unknown_note="crash"
        )

    assert disposition is RepositoryEffectInterruptionDisposition.RESOLVED
    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)


def test_started_external_interruption_becomes_unknown(tmp_path: Path) -> None:
    database, journal, attempts, _, attempt = _stack(tmp_path, EffectBoundaryKind.EXTERNAL)
    owner = RepositoryEffectSettlement(database, journal, attempts)
    with database.transaction(write=True) as connection:
        attempts.start_effect_in(connection, attempt.operation_id)
        disposition = owner.settle_interruption_in(
            connection, attempt.operation_id, unknown_note="crash"
        )

    current = attempts.get(attempt.operation_id)
    assert disposition is RepositoryEffectInterruptionDisposition.OUTCOME_UNKNOWN
    assert current.status.value == "OUTCOME_UNKNOWN"
    assert current.note == "crash"  # type: ignore[union-attr]


def test_existing_unknown_interruption_is_idempotent(tmp_path: Path) -> None:
    database, journal, attempts, _, attempt = _stack(tmp_path, EffectBoundaryKind.EXTERNAL)
    owner = RepositoryEffectSettlement(database, journal, attempts)
    with database.transaction(write=True) as connection:
        attempts.start_effect_in(connection, attempt.operation_id)
        unknown = attempts.mark_unknown_in(connection, attempt.operation_id, note="first")
        disposition = owner.settle_interruption_in(
            connection, attempt.operation_id, unknown_note="second"
        )

    assert disposition is RepositoryEffectInterruptionDisposition.OUTCOME_UNKNOWN
    assert attempts.get(attempt.operation_id) == unknown


def test_canonical_report_wins_over_stale_coordination(tmp_path: Path) -> None:
    database, journal, attempts, repository, attempt = _stack(tmp_path, EffectBoundaryKind.EXTERNAL)
    owner = RepositoryEffectSettlement(database, journal, attempts)
    report = make_operation_report(
        repository,
        OperationAction.UPDATE,
        operation_id=attempt.operation_id,
        fleet_run_id=None,
    )
    with database.transaction(write=True) as connection:
        attempts.start_effect_in(connection, attempt.operation_id)
        journal.commit_operation_in(connection, report)
        disposition = owner.settle_interruption_in(
            connection, attempt.operation_id, unknown_note="ignored"
        )

    assert disposition is RepositoryEffectInterruptionDisposition.RESOLVED
    assert journal.operation(attempt.operation_id) == report
    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)


def test_transition_cannot_change_flat_effect_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _, attempts, _, attempt = _stack(tmp_path, EffectBoundaryKind.EXTERNAL)
    other_repository = tmp_path / "other"
    other_repository.mkdir()

    def corrupt_transition(current, *, transitioned_at):
        transitioned = start_effect_attempt(current, transitioned_at=transitioned_at)
        return transitioned.model_copy(
            update={"repository": RepositoryIdentity(path=other_repository)}
        )

    monkeypatch.setattr(effect_attempt_runtime, "start_effect_attempt", corrupt_transition)

    with pytest.raises(ValueError, match="cannot change repository identity"):
        with database.transaction(write=True) as connection:
            attempts.start_effect_in(connection, attempt.operation_id)

    assert attempts.get(attempt.operation_id) == attempt
