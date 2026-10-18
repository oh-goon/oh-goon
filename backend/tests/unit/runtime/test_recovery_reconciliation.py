from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest

from oh_goon.core.errors import OperationNotFound, RepositoryEffectQuarantined, StateConflict
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.vocabulary import (
    CommandSafety,
    EffectAttemptStatus,
    EffectBoundaryKind,
    OperationAction,
    ReconciliationOutcome,
)
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import read_durable_revision
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.fault_injection import FaultPoint, RaisingFaultInjector
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.effect_quarantine import (
    effect_quarantines,
    require_effects_allowed_for_repositories,
)
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.reconciliation import RepositoryReconciler
from tests.factories.capabilities import capability_snapshot
from tests.factories.journal import commit_operation
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_effect_runtime import effect_recovery
from tests.factories.repository_state import baseline_for
from tests.factories.runtime_stack import direct_operation_stack
from tests.fakes.capability_runners import ExternalRunnerStub


class _HeldReconciler:
    """Observe exactly one native reconciliation under the atomic admission fence."""

    def __init__(self, entered: Event, release: Event) -> None:
        self._delegate = RepositoryReconciler(GitService())
        self._entered = entered
        self._release = release
        self._lock = Lock()
        self.calls = 0

    def reconcile(self, *args, **kwargs):
        with self._lock:
            self.calls += 1
        self._entered.set()
        assert self._release.wait(timeout=5), "test must release held native observation"
        return self._delegate.reconcile(*args, **kwargs)


class _SuccessfulAdapter(ExternalRunnerStub):
    def __init__(self) -> None:
        self.calls = 0

    def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
        self.calls += 1
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


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return repository


def _unknown_attempt(
    database: LocalDatabase, attempts: RepositoryEffectAttemptStore, repository: Path
):
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
        return attempts.mark_unknown_in(connection, attempt.operation_id, note="process ended")


def _recovery(database: LocalDatabase, journal: Journal, attempts: RepositoryEffectAttemptStore):
    return effect_recovery(
        database, attempts, journal, ExecutionCoordinator(database), GitService()
    )


def test_startup_recovery_never_reclassifies_a_live_execution_attempt(tmp_path: Path) -> None:
    path = tmp_path / "live-owner.db"
    owner_database = LocalDatabase(path)
    owner_attempts = RepositoryEffectAttemptStore(owner_database)
    owner_execution = ExecutionCoordinator(owner_database)
    repository = _repository(tmp_path)

    ticket, attempt = owner_execution.reserve_direct_with(
        expected_durable_revision=owner_database.durable_revision(),
        mutate=lambda connection: owner_attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        ),
    )

    observer_database = LocalDatabase(path)
    observer_attempts = RepositoryEffectAttemptStore(observer_database)
    observer_journal = Journal(observer_database)
    observer_execution = ExecutionCoordinator(observer_database)

    effect_recovery(
        observer_database,
        observer_attempts,
        observer_journal,
        observer_execution,
        GitService(),
    ).recover()

    assert observer_attempts.get(attempt.operation_id).status is EffectAttemptStatus.CLAIMED
    owner_execution.activate(ticket).finish(
        lambda connection: owner_attempts.retire_unstarted_in(connection, attempt.operation_id)
    )


def test_unchanged_worktree_effect_is_confirmed_safe_to_retry(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)

    recovered = _recovery(database, journal, attempts).reconcile(attempt.operation_id)

    assert recovered.reconciliation.outcome is ReconciliationOutcome.CONFIRMED_NOT_APPLIED
    assert recovered.reconciliation.retry_safe
    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)


def test_startup_recovery_leaves_operator_required_quarantine_stable(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "stable-quarantine.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
    recovery = _recovery(database, journal, attempts)
    first = recovery.reconcile(attempt.operation_id)
    persisted = attempts.get(attempt.operation_id)
    first_revision = journal.read_view().revision

    assert first.reconciliation.outcome is ReconciliationOutcome.PARTIALLY_APPLIED
    recovery.recover()

    unchanged = attempts.get(attempt.operation_id)
    assert unchanged == persisted
    assert journal.read_view().revision == first_revision


def test_operator_required_reconciliation_retry_replays_existing_evidence(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "operator-reconcile-replay.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
    recovery = _recovery(database, journal, attempts)

    first = recovery.reconcile(attempt.operation_id)
    persisted = attempts.get(attempt.operation_id)
    first_revision = journal.read_view().revision

    replayed = recovery.reconcile(attempt.operation_id)

    assert first.reconciliation.outcome is ReconciliationOutcome.PARTIALLY_APPLIED
    assert replayed == first
    assert attempts.get(attempt.operation_id) == persisted
    assert journal.read_view().revision == first_revision


def test_terminal_reconciliation_replays_existing_journal_evidence(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "reconcile-replay.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)
    recovery = _recovery(database, journal, attempts)

    first = recovery.reconcile(attempt.operation_id)
    first_revision = journal.read_view().revision
    replayed = recovery.reconcile(attempt.operation_id)

    assert first.reconciliation.outcome is ReconciliationOutcome.CONFIRMED_NOT_APPLIED
    assert replayed == first
    assert journal.read_view().revision == first_revision


def test_changed_worktree_effect_remains_quarantined(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")

    recovered = _recovery(database, journal, attempts).reconcile(attempt.operation_id)

    assert recovered.reconciliation.outcome is ReconciliationOutcome.PARTIALLY_APPLIED
    persisted = attempts.get(attempt.operation_id)
    assert persisted.status is EffectAttemptStatus.PARTIALLY_APPLIED
    with pytest.raises(RepositoryEffectQuarantined):
        require_effects_allowed_for_repositories(
            CommandSafety.WORKTREE_MUTATION,
            [recovered.effect.repository.repository_id],
            effect_quarantines(attempts.all()),
        )


def test_environment_mutation_is_indeterminate_without_canonical_report(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    repository = _repository(tmp_path)

    result = RepositoryReconciler(GitService()).reconcile(
        repository,
        "op-environment",
        report=journal.operation("op-environment"),
        safety=CommandSafety.ENVIRONMENT_MUTATION,
        baseline=baseline_for(repository),
    )

    assert result.outcome is ReconciliationOutcome.INDETERMINATE
    assert not result.retry_safe


def test_reconciliation_compare_and_set_rejects_stale_writer(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)
    observed = attempts.get(attempt.operation_id)
    (repository / "pyproject.toml").write_text(
        "[project]\nname='changed-before-stale-reconciliation'\n", encoding="utf-8"
    )
    result = RepositoryReconciler(GitService()).reconcile(
        repository,
        attempt.operation_id,
        report=journal.operation(attempt.operation_id),
        safety=attempt.safety,
        baseline=attempt.baseline,
    )
    with database.transaction(write=True) as connection:
        attempts.apply_reconciliation_in(
            connection, attempt.operation_id, result, expected_revision=observed.record_version
        )

    with pytest.raises(StateConflict), database.transaction(write=True) as connection:
        attempts.apply_reconciliation_in(
            connection, attempt.operation_id, result, expected_revision=observed.record_version
        )


def test_invariant_auditor_is_read_only_and_plans_inspection_for_quarantine(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    schedules = ScheduleExecutionStore(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
    _recovery(database, journal, attempts).reconcile(attempt.operation_id)
    auditor = SystemInvariantAuditor(database, journal, execution, attempts, schedules)

    with database.transaction() as connection:
        before = read_durable_revision(connection)
    audit = auditor.audit()
    plan = auditor.repair_plan(audit)
    with database.transaction() as connection:
        after = read_durable_revision(connection)

    assert before == after
    assert any(item.code == "REPOSITORY_EFFECT_QUARANTINED" for item in audit.findings)
    action = next(item for item in plan.actions if item.target == attempt.operation_id)
    assert action.action == "inspect-repository-effect"
    assert action.requires_confirmation
    assert not action.destructive


def test_fault_after_claim_retires_unstarted_attempt_atomically(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    repository = _repository(tmp_path)
    operations, attempts, _, _ = direct_operation_stack(
        database,
        journal,
        _SuccessfulAdapter(),
        faults=RaisingFaultInjector({FaultPoint.AFTER_DIRECT_ATTEMPT_CLAIM}),
    )

    with pytest.raises(RuntimeError, match="AFTER_DIRECT_ATTEMPT_CLAIM"):
        operations.run(repository, OperationAction.UPDATE)

    # Same-process failures before EFFECT_STARTED are known not to have produced an
    # external effect, so the owner retires the attempt while releasing its lease.
    assert attempts.all() == []
    _recovery(database, journal, attempts).recover()
    assert attempts.all() == []
    assert journal.read_view().history(repository) == []


def test_committed_direct_operation_replays_without_an_effect_attempt(tmp_path: Path) -> None:
    from oh_goon.core.errors import OperationNotFound

    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    repository = _repository(tmp_path)
    adapter = _SuccessfulAdapter()
    operations, attempts, _, _ = direct_operation_stack(database, journal, adapter)
    operation_id = "op-direct-retry"

    committed = operations.run(repository, OperationAction.UPDATE, operation_id=operation_id)

    reports = journal.read_view().history(repository)
    assert reports == [committed.report]
    assert journal.operation(operation_id) == committed.report
    with pytest.raises(OperationNotFound):
        attempts.get(operation_id)

    replayed = operations.run(repository, OperationAction.UPDATE, operation_id=operation_id)
    assert replayed.report == committed.report
    assert replayed.returncode == committed.report.outcome.returncode == 0
    assert adapter.calls == 1


def test_direct_operation_identity_rejects_rebinding_after_commit(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    repository = _repository(tmp_path)
    operations, _, _, _ = direct_operation_stack(database, journal, _SuccessfulAdapter())
    operation_id = "op-fixed-direct"

    operations.run(repository, OperationAction.UPDATE, operation_id=operation_id)

    with pytest.raises(StateConflict, match="already bound to another repository effect"):
        operations.run(repository, OperationAction.PLAN, operation_id=operation_id)


def test_reconciler_prefers_canonical_report_and_read_only_is_safe(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    repository = _repository(tmp_path)
    applied_id = "op-applied"
    commit_operation(
        journal,
        make_operation_report(
            repository,
            OperationAction.UPDATE,
            fleet_run_id=None,
            operation_id=applied_id,
        ),
    )
    reconciler = RepositoryReconciler(GitService())

    applied = reconciler.reconcile(
        repository,
        applied_id,
        report=journal.operation(applied_id),
        safety=CommandSafety.WORKTREE_MUTATION,
        baseline=baseline_for(repository),
    )
    read_only = reconciler.reconcile(
        repository,
        "op-read-only",
        report=journal.operation("op-read-only"),
        safety=CommandSafety.READ_ONLY,
        baseline=baseline_for(repository),
    )

    assert applied.outcome is ReconciliationOutcome.CONFIRMED_APPLIED
    assert applied.outcome is ReconciliationOutcome.CONFIRMED_APPLIED
    assert "journal_report_present" not in type(applied).model_fields
    assert read_only.outcome is ReconciliationOutcome.CONFIRMED_NOT_APPLIED


def test_started_attempt_repair_plan_is_generic_not_direct_specific(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    schedules = ScheduleExecutionStore(database)
    auditor = SystemInvariantAuditor(database, journal, execution, attempts, schedules)
    repository = _repository(tmp_path)
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )

    plan = auditor.repair_plan(auditor.audit())
    action = next(item for item in plan.actions if item.target == attempt.operation_id)
    assert action.action == "reconcile-effect-attempt"
    assert not action.requires_confirmation


def test_typed_recovery_protocol_rejects_unknown_action_shapes() -> None:
    from pydantic import TypeAdapter, ValidationError

    from oh_goon.core.models.recovery import InspectLeaseFinding, RepairAction

    with pytest.raises(ValidationError, match="target"):
        InspectLeaseFinding(
            finding_type="INSPECT_LEASE",
            code="LEASE",
            severity="ERROR",
            message="lease needs inspection",
        )

    with pytest.raises(ValidationError):
        TypeAdapter(RepairAction).validate_python(
            {
                "action": "unknown-repair",
                "target": "x",
                "destructive": False,
                "requires_confirmation": False,
                "explanation": "not a supported recovery action",
            }
        )


def test_recovered_mutation_is_a_journal_invalidator_for_prior_results(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    commit_operation(
        journal,
        make_operation_report(
            repository,
            OperationAction.INIT,
            operation_id="init-before-uncertain-update",
            fleet_run_id=None,
        ),
    )
    before = journal.current_capability(repository, "initialize")
    assert before is not None and before.freshness.value == "CURRENT"

    attempt = _unknown_attempt(database, attempts, repository)
    (repository / "pyproject.toml").write_text(
        "[project]\nname='changed-by-uncertain-effect'\n", encoding="utf-8"
    )
    recovered = _recovery(database, journal, attempts).reconcile(attempt.operation_id)

    assert recovered.reconciliation.outcome is ReconciliationOutcome.PARTIALLY_APPLIED
    after = journal.current_capability(repository, "initialize")
    assert after is not None and after.freshness.value == "STALE"
    with database.transaction() as connection:
        kinds = [
            row[0]
            for row in connection.execute("SELECT kind FROM journal_entries ORDER BY revision")
        ]
    assert kinds == ["OPERATION", "EFFECT_RECONCILIATION"]


def test_confirmed_not_applied_recovery_evidence_does_not_invalidate_prior_results(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    commit_operation(
        journal,
        make_operation_report(
            repository,
            OperationAction.INIT,
            operation_id="init-before-safe-recovery",
            fleet_run_id=None,
        ),
    )
    attempt = _unknown_attempt(database, attempts, repository)

    recovered = _recovery(database, journal, attempts).reconcile(attempt.operation_id)

    assert recovered.reconciliation.outcome is ReconciliationOutcome.CONFIRMED_NOT_APPLIED
    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)
    current = journal.current_capability(repository, "initialize")
    assert current is not None and current.freshness.value == "CURRENT"


def test_reconciliation_attempt_and_journal_evidence_commit_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)
    (repository / "pyproject.toml").write_text(
        "[project]\nname='changed-before-failed-evidence-write'\n", encoding="utf-8"
    )
    before_revision = journal.read_view().revision

    def fail_evidence_write(*args, **kwargs) -> None:
        raise RuntimeError("journal evidence write failed")

    monkeypatch.setattr(journal, "commit_effect_reconciliation_in", fail_evidence_write)
    with pytest.raises(RuntimeError, match="journal evidence write failed"):
        _recovery(database, journal, attempts).reconcile(attempt.operation_id)

    unchanged = attempts.get(attempt.operation_id)
    assert unchanged.status is EffectAttemptStatus.OUTCOME_UNKNOWN
    assert unchanged.record_version == attempt.record_version
    assert journal.read_view().revision == before_revision


def test_concurrent_reconciliation_retries_replay_one_canonical_result(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "concurrent-reconcile.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)
    entered, release = Event(), Event()
    observed = _HeldReconciler(entered, release)
    recovery = effect_recovery(
        database,
        attempts,
        journal,
        ExecutionCoordinator(database),
        GitService(),
        reconciler=observed,  # type: ignore[arg-type]
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(recovery.reconcile, attempt.operation_id)
        try:
            assert entered.wait(timeout=3)
            second = pool.submit(recovery.reconcile, attempt.operation_id)
            with pytest.raises(FutureTimeout):
                second.result(timeout=0.1)
        finally:
            release.set()
        results = [first.result(timeout=8), second.result(timeout=8)]

    assert observed.calls == 1, "a later retry must replay the Journal, not observe twice"
    assert results[0] == results[1]
    assert journal.read_view().latest_effect_reconciliation(attempt.operation_id) == results[0]
    with database.transaction() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM journal_entries WHERE kind = 'EFFECT_RECONCILIATION'"
        ).fetchone()[0]
    assert count == 1


def test_concurrent_same_note_acknowledgements_replay_one_canonical_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "concurrent-ack.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _unknown_attempt(database, attempts, repository)
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
    recovery = _recovery(database, journal, attempts)
    recovery.reconcile(attempt.operation_id)

    barrier = Barrier(2)
    original_get_in = attempts.get_in

    def synchronized_get_in(connection, operation_id: str):
        current = original_get_in(connection, operation_id)
        barrier.wait(timeout=5)
        return current

    monkeypatch.setattr(attempts, "get_in", synchronized_get_in)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(recovery.acknowledge, attempt.operation_id, note="inspected")
            for _ in range(2)
        ]
        results = [future.result(timeout=10) for future in futures]

    assert results[0] == results[1]
    assert results[0].note == "inspected"
    with database.transaction() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM journal_entries WHERE kind = 'EFFECT_ACKNOWLEDGEMENT'"
        ).fetchone()[0]
    assert count == 1
