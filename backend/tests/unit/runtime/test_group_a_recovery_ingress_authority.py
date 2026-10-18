"""M185: recovery repository observation must be fenced by the original lease."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from threading import Event

import pytest

from oh_goon.core.errors import ExecutionBusy, OperationNotFound, RuntimeSafetyStale, StateConflict
from oh_goon.core.vocabulary import EffectBoundaryKind, OperationAction
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from oh_goon.services.effect_recovery import RepositoryEffectRecovery
from oh_goon.services.reconciliation import RepositoryReconciler
from oh_goon.services.repository_effect_settlement import RepositoryEffectSettlement
from tests.factories.capabilities import capability_snapshot
from tests.factories.repository_state import baseline_for


def _stack(tmp_path: Path):
    database = LocalDatabase(tmp_path / "recovery-ingress.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    git = GitService()
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='recovery-proof'\n", encoding="utf-8"
    )
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
        attempts.start_effect_in(connection, attempt.operation_id)
        attempt = attempts.mark_unknown_in(connection, attempt.operation_id, note="interrupted")
    recovery = RepositoryEffectRecovery(
        database,
        attempts,
        journal,
        execution,
        RepositoryReconciler(journal, git),
        RepositoryEffectSettlement(database, journal, attempts),
    )
    return database, attempts, journal, execution, git, attempt, recovery


def test_live_lease_denies_native_recovery_observation_before_work(
    tmp_path: Path, monkeypatch
) -> None:
    database, attempts, journal, execution, git, attempt, recovery = _stack(tmp_path)
    observed = []
    monkeypatch.setattr(GitService, "fingerprint", lambda _self, path: observed.append(path))
    ticket, _ = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )
    revision = journal.read_view().revision
    try:
        with pytest.raises(ExecutionBusy):
            recovery.reconcile(attempt.operation_id, expected_record_version=attempt.record_version)
        assert observed == []
        assert attempts.get(attempt.operation_id) == attempt
        assert journal.read_view().revision == revision
    finally:
        execution.activate(ticket).finish(lambda _connection: None)


def test_stale_recovery_version_never_observes_repository(tmp_path: Path, monkeypatch) -> None:
    _database, attempts, journal, _execution, git, attempt, recovery = _stack(tmp_path)
    observed = []
    monkeypatch.setattr(GitService, "fingerprint", lambda _self, path: observed.append(path))
    with pytest.raises(StateConflict, match="stale"):
        recovery.reconcile(attempt.operation_id, expected_record_version=attempt.record_version + 1)
    assert observed == []
    assert attempts.get(attempt.operation_id) == attempt
    assert journal.read_view().latest_effect_reconciliation(attempt.operation_id) is None


def test_failed_native_observation_does_not_settle_attempt_or_hold_lease(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database, attempts, journal, execution, git, attempt, recovery = _stack(tmp_path)
    revision = journal.read_view().revision

    def broken_fingerprint(_self: GitService, _repository: Path):
        raise RuntimeError("native repository fingerprint failed")

    monkeypatch.setattr(GitService, "fingerprint", broken_fingerprint)
    with pytest.raises(RuntimeError, match="native repository fingerprint failed"):
        recovery.reconcile(attempt.operation_id)
    assert attempts.get(attempt.operation_id) == attempt
    assert journal.read_view().revision == revision
    assert journal.read_view().latest_effect_reconciliation(attempt.operation_id) is None
    ticket, _ = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )
    execution.activate(ticket).finish(lambda _connection: None)


def test_native_recovery_observation_pins_original_read_cut_until_finished(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database, attempts, journal, execution, git, attempt, recovery = _stack(tmp_path)
    entered, release, admission_entered = Event(), Event(), Event()
    original_fingerprint = GitService.fingerprint
    original_revision = database.durable_revision()

    def held_fingerprint(self: GitService, repository: Path):
        entered.set()
        assert release.wait(timeout=5), "test must release the native observation"
        return original_fingerprint(self, repository)

    monkeypatch.setattr(GitService, "fingerprint", held_fingerprint)

    def reserve_after_observation_starts():
        admission_entered.set()
        return execution.reserve_direct_with(
            expected_durable_revision=original_revision,
            mutate=lambda _connection: None,
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        reconciled = pool.submit(recovery.reconcile, attempt.operation_id)
        try:
            assert entered.wait(timeout=3)
            admission = pool.submit(reserve_after_observation_starts)
            assert admission_entered.wait(timeout=3)
            with pytest.raises(FutureTimeout):
                admission.result(timeout=0.1)
        finally:
            release.set()
        evidence = reconciled.result(timeout=6)
        with pytest.raises(RuntimeSafetyStale):
            admission.result(timeout=6)
    assert evidence.reconciliation.retry_safe
    assert journal.read_view().latest_effect_reconciliation(attempt.operation_id) == evidence
    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)
    ticket, _ = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )
    execution.activate(ticket).finish(lambda _connection: None)
