from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import JournalConflict, OperationNotFound
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.vocabulary import (
    EffectAttemptStatus,
    EffectBoundaryKind,
    FleetRunEventType,
    FleetRunStatus,
    OperationAction,
)
from oh_goon.core.vocabulary.fleet import FleetScope
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from oh_goon.services.effect_recovery import RepositoryEffectRecovery
from tests.factories.capabilities import admit_test_run, capability_snapshot
from tests.factories.journal import commit_operation, start_repository, start_run
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_effect_runtime import effect_execution, effect_recovery
from tests.factories.repository_state import baseline_for


def _recovery(
    database: LocalDatabase, journal: Journal, attempts: RepositoryEffectAttemptStore
) -> RepositoryEffectRecovery:
    return effect_recovery(
        database, attempts, journal, ExecutionCoordinator(database), GitService()
    )


def _report(identity: RepositoryIdentity, run_id: str, operation_id: str):
    return make_operation_report(
        identity,
        OperationAction.INIT,
        fleet_run_id=run_id,
        operation_id=operation_id,
    )


def _lose_next_write_response(database: LocalDatabase, *, after_commit=None) -> None:
    original = database.transaction
    armed = True

    @contextmanager
    def ambiguous_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal armed
        with original(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and armed:
            armed = False
            if after_commit is not None:
                after_commit()
            raise RuntimeError("startup recovery commit response lost after commit")

    database.transaction = ambiguous_transaction  # type: ignore[method-assign]


def test_startup_claimed_settlement_replays_exact_evidence_after_lost_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "startup-claimed-ambiguous.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "claimed"
    repository.mkdir()
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )

    _lose_next_write_response(database)
    _recovery(database, journal, attempts).recover()

    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)


def test_startup_unknown_settlement_continues_to_reconciliation_after_lost_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "startup-unknown-ambiguous.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "unknown"
    repository.mkdir()
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

    _lose_next_write_response(database)
    _recovery(database, journal, attempts).recover()

    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)
    assert journal.read_view().latest_effect_reconciliation(attempt.operation_id) is not None


def test_startup_settlement_replay_rejects_superseding_attempt(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "startup-superseded-ambiguous.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "superseded"
    repository.mkdir()
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )

    def supersede() -> None:
        with database.transaction(write=True) as connection:
            attempts.begin_direct_in(
                connection,
                repository,
                capability_snapshot(OperationAction.UPDATE),
                target=None,
                baseline=baseline_for(repository),
                effect_boundary=EffectBoundaryKind.EXTERNAL,
                operation_id=attempt.operation_id,
            )

    _lose_next_write_response(database, after_commit=supersede)
    with pytest.raises(RuntimeError, match="startup recovery commit response lost"):
        _recovery(database, journal, attempts).recover()

    assert attempts.get(attempt.operation_id).status is EffectAttemptStatus.CLAIMED


def test_startup_settlement_failure_before_transaction_body_is_not_replayed(
    tmp_path: Path, monkeypatch
) -> None:
    database = LocalDatabase(tmp_path / "startup-precommit-failure.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "precommit"
    repository.mkdir()
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
    original = database.transaction

    @contextmanager
    def fail_write_before_body(*, write: bool = False, track_revision: bool = True):
        if write:
            raise RuntimeError("startup recovery failed before transaction body")
        with original(write=write, track_revision=track_revision) as connection:
            yield connection

    monkeypatch.setattr(database, "transaction", fail_write_before_body)
    with pytest.raises(RuntimeError, match="before transaction body"):
        _recovery(database, journal, attempts).recover()

    assert attempts.get(attempt.operation_id).status is EffectAttemptStatus.CLAIMED


def test_repository_effect_identity_is_deterministic_without_persisted_fleet_claim(
    tmp_path: Path,
) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    first = repository_attempt_operation_id("fleet-attempt", identity.repository_id)
    second = repository_attempt_operation_id("fleet-attempt", identity.repository_id)

    assert first == second
    assert first != repository_attempt_operation_id("other-run", identity.repository_id)


def test_identical_operation_commit_is_idempotent_but_identity_rebind_is_rejected(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "commit-idempotency.db")
    journal = Journal(database)
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "fleet-idempotent", OperationAction.INIT, [identity])
    start_run(journal, "fleet-idempotent")
    operation_id = repository_attempt_operation_id("fleet-idempotent", identity.repository_id)
    report = _report(identity, "fleet-idempotent", operation_id)
    start_repository(journal, "fleet-idempotent", identity)

    commit_operation(journal, report)
    revision = journal.read_view().revision
    commit_operation(journal, report)

    assert journal.read_view().revision == revision
    detail = journal.read_view().run_detail("fleet-idempotent")
    assert detail is not None and len(detail.operations) == 1

    changed = report.model_copy(
        update={"outcome": report.outcome.model_copy(update={"reason_code": ReasonCode.NO_UPDATES})}
    )
    with pytest.raises(JournalConflict, match="another report"):
        commit_operation(journal, changed)


def test_restart_after_effect_start_marks_fleet_failed_and_reconciles_one_generic_attempt(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "unknown-effect.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = tmp_path / "api"
    repository.mkdir()
    identity = RepositoryIdentity(name="api", path=repository)
    admit_test_run(journal, "fleet-unknown", OperationAction.UPDATE, [identity])
    start_run(journal, "fleet-unknown")
    operation_id = repository_attempt_operation_id("fleet-unknown", identity.repository_id)
    with database.transaction(write=True) as connection:
        attempts.begin_fleet_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            fleet_run_id="fleet-unknown",
            operation_id=operation_id,
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
        attempts.start_effect_in(connection, operation_id)
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")

    _recovery(database, journal, attempts).recover()
    detail = journal.read_view().run_detail("fleet-unknown")
    recovered = attempts.get(operation_id)

    assert detail is not None and detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure is not None
    assert detail.run.failure.code == "REPOSITORY_EFFECT_OUTCOME_UNKNOWN"
    assert recovered.status is EffectAttemptStatus.PARTIALLY_APPLIED
    assert recovered.fleet_run_id == "fleet-unknown"


def test_parallel_repository_commit_order_projects_only_durable_progress(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "parallel-progress.db")
    journal = Journal(database)
    identities = [
        RepositoryIdentity(name=f"repo-{index}", path=tmp_path / f"repo-{index}")
        for index in range(4)
    ]
    fleet_run_id = "fleet-parallel-progress"
    admit_test_run(journal, fleet_run_id, OperationAction.INIT, identities)
    start_run(journal, fleet_run_id)

    for completed, index in enumerate((2, 0, 3, 1), start=1):
        identity = identities[index]
        operation_id = repository_attempt_operation_id(fleet_run_id, identity.repository_id)
        start_repository(journal, fleet_run_id, identity)
        commit_operation(journal, _report(identity, fleet_run_id, operation_id))
        detail = journal.read_view().run_detail(fleet_run_id)
        assert detail is not None
        assert detail.run.status is FleetRunStatus.RUNNING
        assert detail.run.progress.completed == completed

    final = journal.read_view().run_detail(fleet_run_id)
    assert final is not None
    assert final.run.status is FleetRunStatus.RUNNING
    assert final.run.progress.completed == len(identities)


def test_restart_terminalizes_running_run_with_all_results_exactly_once(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "complete-after-last-result.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    identities = [
        RepositoryIdentity(name="first", path=tmp_path / "first"),
        RepositoryIdentity(name="second", path=tmp_path / "second"),
    ]
    fleet_run_id = "fleet-complete-after-last-result"
    admit_test_run(journal, fleet_run_id, OperationAction.INIT, identities)
    start_run(journal, fleet_run_id)
    for identity in identities:
        operation_id = repository_attempt_operation_id(fleet_run_id, identity.repository_id)
        start_repository(journal, fleet_run_id, identity)
        commit_operation(journal, _report(identity, fleet_run_id, operation_id))

    before = journal.read_view().run_detail(fleet_run_id)
    assert before is not None
    assert before.run.status is FleetRunStatus.RUNNING
    assert before.run.progress.completed == 2
    assert not any(
        event.event_type is FleetRunEventType.RUN_COMPLETED
        for event in journal.run_events(fleet_run_id).events
    )

    _recovery(database, journal, attempts).recover()
    _recovery(database, journal, attempts).recover()

    after = journal.read_view().run_detail(fleet_run_id)
    assert after is not None
    assert after.run.status is FleetRunStatus.COMPLETED
    assert after.run.progress.completed == 2
    assert sum(
        event.event_type is FleetRunEventType.RUN_COMPLETED
        for event in journal.run_events(fleet_run_id).events
    ) == 1


def test_restart_completes_run_when_all_repository_results_are_already_durable(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "durable-complete.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "fleet-complete-recovery", OperationAction.INIT, [identity])
    start_run(journal, "fleet-complete-recovery")
    operation_id = repository_attempt_operation_id(
        "fleet-complete-recovery", identity.repository_id
    )
    start_repository(journal, "fleet-complete-recovery", identity)
    commit_operation(journal, _report(identity, "fleet-complete-recovery", operation_id))

    _recovery(database, journal, attempts).recover()
    detail = journal.read_view().run_detail("fleet-complete-recovery")

    assert detail is not None
    assert detail.run.status is FleetRunStatus.COMPLETED
    assert detail.run.progress.completed == 1


def test_restart_with_partial_durable_results_blocks_whole_run_replay(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "partial-recovery.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    first = RepositoryIdentity(name="first", path=tmp_path / "first")
    second = RepositoryIdentity(name="second", path=tmp_path / "second")
    admit_test_run(journal, "fleet-partial", OperationAction.INIT, [first, second])
    start_run(journal, "fleet-partial")
    operation_id = repository_attempt_operation_id("fleet-partial", first.repository_id)
    start_repository(journal, "fleet-partial", first)
    commit_operation(journal, _report(first, "fleet-partial", operation_id))

    _recovery(database, journal, attempts).recover()
    detail = journal.read_view().run_detail("fleet-partial")

    assert detail is not None and detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure is not None
    assert detail.run.failure.code == "RUN_PARTIALLY_COMMITTED_BY_RESTART"
    assert detail.run.failure.retryable is False
    assert detail.run.progress.completed == 1


def test_restart_before_any_repository_effect_is_retryable(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "safe-retry.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "fleet-safe-retry", OperationAction.INIT, [identity])
    start_run(journal, "fleet-safe-retry")

    _recovery(database, journal, attempts).recover()
    detail = journal.read_view().run_detail("fleet-safe-retry")

    assert detail is not None and detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure is not None
    assert detail.run.failure.code == "RUN_INTERRUPTED_BY_RESTART"
    assert detail.run.failure.retryable is True


def test_fleet_repository_effect_uses_generic_attempt_identity_and_retires_it_with_report(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "attempt-forwarding.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = tmp_path / "api"
    repository.mkdir()
    identity = RepositoryIdentity(name="api", path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    admit_test_run(journal, "fleet-forward", OperationAction.INIT, [identity])
    start_run(journal, "fleet-forward")
    ticket = execution.reserve_fleet_with(
        "fleet-forward",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    captured: list[str] = []

    class _Prepared:
        execution_input_id = None

        def execute(self, _request):
            raise AssertionError("CapturingExecutor.execute owns execution")

        def close(self) -> None:
            return None

    class CapturingExecutor:
        def effect_boundary(self, _intent):
            return EffectBoundaryKind.EXTERNAL

        def prepare(self, request):
            return _Prepared()

        def execute(self, prepared, request):
            captured.append(request.attempt.operation_id)
            return OperationExecution(
                stdout="",
                stderr="",
                report=make_operation_report(
                    request.attempt.repository,
                    request.attempt.action,
                    fleet_run_id=request.attempt.fleet_run_id,
                    operation_id=request.attempt.operation_id,
                ),
            )

    session = execution.activate(ticket)
    effects = effect_execution(
        database,
        CapturingExecutor(),  # type: ignore[arg-type]
        journal,
        attempts,
        GitService(),
    )
    effects.execute_fleet_target(
        resolve_capability_intent(OperationAction.INIT),
        resolve_capability_intent(OperationAction.INIT).definition.durable_snapshot(),
        entry,
        session,
    )
    session.finish(lambda _connection: None)

    operation_id = repository_attempt_operation_id("fleet-forward", identity.repository_id)
    assert captured == [operation_id]
    assert journal.operation(operation_id) is not None
    with pytest.raises(OperationNotFound):
        attempts.get(operation_id)
