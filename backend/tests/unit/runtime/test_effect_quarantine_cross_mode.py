from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import OperationNotFound, RepositoryEffectQuarantined
from oh_goon.core.models.effect_origin import FleetEffectOrigin
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.vocabulary import (
    CommandSafety,
    EffectAttemptOwner,
    EffectAttemptStatus,
    EffectBoundaryKind,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.core.vocabulary.fleet import FleetScope
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.capability_executor import CapabilityExecutor
from oh_goon.services.effect_quarantine import (
    effect_quarantines,
    require_effects_allowed_for_repositories,
)
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.recovery import RecoveryService
from oh_goon.services.repository_effect_preparation import mutate_pre_effect_attempt
from oh_goon.services.runtime_safety import RuntimeSafetyService
from tests.factories.capabilities import admit_test_run, capability_snapshot
from tests.factories.journal import start_run
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_effect_runtime import effect_execution, effect_recovery
from tests.factories.repository_state import baseline_for
from tests.fakes.capability_runners import ExternalRunnerStub, fixed_capability_runners


def _repo(root: Path, name: str = "api") -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return repository


def _begin_unknown_direct(
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
        return attempts.mark_unknown_in(
            connection, attempt.operation_id, note="direct process ended"
        )


def _begin_unknown_fleet(
    database: LocalDatabase,
    attempts: RepositoryEffectAttemptStore,
    repository: Path,
    *,
    fleet_run_id: str,
):
    identity = RepositoryIdentity(name=repository.name, path=repository)
    operation_id = repository_attempt_operation_id(fleet_run_id, identity.repository_id)
    with database.transaction(write=True) as connection:
        attempts.begin_fleet_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            fleet_run_id=fleet_run_id,
            operation_id=operation_id,
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
        attempts.start_effect_in(connection, operation_id)
        return attempts.mark_unknown_in(connection, operation_id, note="fleet process ended")


def _recovery_service(
    database: LocalDatabase,
    journal: Journal,
    attempts: RepositoryEffectAttemptStore,
) -> RecoveryService:
    execution = ExecutionCoordinator(database)
    recovery = effect_recovery(database, attempts, journal, execution, GitService())
    auditor = SystemInvariantAuditor(
        database,
        journal,
        execution,
        attempts,
        ScheduleExecutionStore(database),
    )
    safety = RuntimeSafetyService(auditor, RuntimeSafetyGate())
    safety.refresh()
    return RecoveryService(recovery, safety)


def test_fleet_cancel_race_stops_before_repository_claim(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "cancel-race.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, FleetExecutorPool(database, None))
    repository = _repo(tmp_path / "cancel-race")
    identity = RepositoryIdentity(name=repository.name, path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    admit_test_run(journal, "fleet-cancel-race", OperationAction.UPDATE, [identity])
    start_run(journal, "fleet-cancel-race")
    ticket = execution.reserve_fleet_with(
        "fleet-cancel-race",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    underlying = execution.activate(ticket)

    class CancelBeforeClaimSession:
        def __getattr__(self, name):
            return getattr(underlying, name)

        def cancel_requested(self):
            return False

    class GitStub:
        def fingerprint(self, _repository, _cancel_requested):
            lifecycle.cancel("fleet-cancel-race")
            return baseline_for(repository)

    class ExecutorStub:
        def effect_boundary(self, _intent):
            return EffectBoundaryKind.EXTERNAL

        def prepare(self, _request):
            raise AssertionError("prepare must not run after cancellation wins the claim race")

    effects = effect_execution(
        database,
        ExecutorStub(),
        journal,
        attempts,
        GitStub(),  # type: ignore[arg-type]
    )
    with pytest.raises(OperationCancelledError, match="before repository claim"):
        effects.execute_fleet_target(
            resolve_capability_intent(OperationAction.UPDATE),
            capability_snapshot(OperationAction.UPDATE),
            entry,
            CancelBeforeClaimSession(),  # type: ignore[arg-type]
        )

    with pytest.raises(OperationNotFound):
        attempts.get(repository_attempt_operation_id("fleet-cancel-race", identity.repository_id))
    lifecycle.finish(underlying, FleetRunTerminalIntent.CANCEL)


def test_direct_and_fleet_effects_project_from_one_quarantine_source(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    direct_repo = _repo(tmp_path / "direct")
    fleet_repo = _repo(tmp_path / "fleet")
    direct = _begin_unknown_direct(database, attempts, direct_repo)
    fleet = _begin_unknown_fleet(database, attempts, fleet_repo, fleet_run_id="fleet-1")

    quarantines = effect_quarantines(attempts.all())

    assert {item.operation_id for item in quarantines} == {direct.operation_id, fleet.operation_id}
    by_id = {item.operation_id: item for item in quarantines}
    assert by_id[direct.operation_id].source is EffectAttemptOwner.DIRECT
    assert by_id[direct.operation_id].origin.kind is EffectAttemptOwner.DIRECT
    assert by_id[fleet.operation_id].source is EffectAttemptOwner.FLEET
    assert isinstance(by_id[fleet.operation_id].origin, FleetEffectOrigin)
    assert by_id[fleet.operation_id].origin.fleet_run_id == "fleet-1"


def test_quarantine_blocks_only_requested_mutating_repository(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    blocked_repo = _repo(tmp_path / "blocked")
    other_repo = _repo(tmp_path / "other")
    blocked = _begin_unknown_direct(database, attempts, blocked_repo)
    quarantines = effect_quarantines(attempts.all())

    with pytest.raises(RepositoryEffectQuarantined):
        require_effects_allowed_for_repositories(
            CommandSafety.WORKTREE_MUTATION,
            [blocked.repository.repository_id],
            quarantines,
        )
    require_effects_allowed_for_repositories(
        CommandSafety.WORKTREE_MUTATION,
        [RepositoryIdentity(name="other", path=other_repo).repository_id],
        quarantines,
    )
    require_effects_allowed_for_repositories(
        CommandSafety.READ_ONLY,
        [blocked.repository.repository_id],
        quarantines,
    )


def test_direct_lease_and_effect_claim_rollback_together(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repo(tmp_path)

    def fail_after_claim(connection):
        attempts.begin_direct_in(
            connection,
            repository,
            capability_snapshot(OperationAction.VERIFY),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
        raise RuntimeError("fail admission")

    with pytest.raises(RuntimeError, match="fail admission"):
        execution.reserve_direct_with(
            expected_durable_revision=database.durable_revision(),
            mutate=fail_after_claim,
        )

    assert execution.active_lease() is None
    assert attempts.all() == []


def test_fleet_restart_terminalizes_before_effect_reconciliation(tmp_path: Path) -> None:
    class ExplodingReconciler:
        def reconcile(self, *args, **kwargs):
            raise RuntimeError("reconciliation crashed")

    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = _repo(tmp_path)
    identity = RepositoryIdentity(name=repository.name, path=repository)
    admit_test_run(journal, "fleet-recovery-order", OperationAction.UPDATE, [identity])
    start_run(journal, "fleet-recovery-order")
    attempt = _begin_unknown_fleet(
        database,
        attempts,
        repository,
        fleet_run_id="fleet-recovery-order",
    )

    with pytest.raises(RuntimeError, match="reconciliation crashed"):
        effect_recovery(
            database,
            attempts,
            journal,
            ExecutionCoordinator(database),
            GitService(),
            reconciler=ExplodingReconciler(),  # type: ignore[arg-type]
        ).recover()

    detail = journal.read_view().run_detail("fleet-recovery-order")
    assert detail is not None and detail.run.status is FleetRunStatus.FAILED
    assert journal.active_run() is None
    assert attempts.get(attempt.operation_id).status is EffectAttemptStatus.OUTCOME_UNKNOWN


def test_recovery_service_uses_same_reconcile_and_acknowledge_path_for_fleet(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = _repo(tmp_path)
    attempt = _begin_unknown_fleet(database, attempts, repository, fleet_run_id="fleet-op")
    (repository / "pyproject.toml").write_text(
        "[project]\nname='changed-after-effect'\n", encoding="utf-8"
    )
    recovery = _recovery_service(database, journal, attempts)

    reconciled = recovery.reconcile(
        attempt.operation_id, expected_record_version=attempt.record_version
    )
    item = next(
        item for item in reconciled.effect_quarantines if item.operation_id == attempt.operation_id
    )
    assert item.reconciliation is not None and item.reconciliation.requires_operator

    attempt_after_reconcile = attempts.get(attempt.operation_id)
    resolved = recovery.acknowledge(
        attempt.operation_id,
        note="repository inspected",
        expected_record_version=attempt_after_reconcile.record_version,
    )
    assert all(item.operation_id != attempt.operation_id for item in resolved.effect_quarantines)
    from oh_goon.core.errors import OperationNotFound, StateConflict

    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)
    with database.transaction() as connection:
        kinds = [
            row[0]
            for row in connection.execute("SELECT kind FROM journal_entries ORDER BY revision")
        ]
    assert kinds[-2:] == ["EFFECT_RECONCILIATION", "EFFECT_ACKNOWLEDGEMENT"]
    revision = journal.read_view().revision

    # The operation id is the natural identity of the one acknowledgement command.
    # Losing the first response and retrying the same note must not append evidence twice.
    replayed = recovery.acknowledge(
        attempt.operation_id,
        note="repository inspected",
        expected_record_version=attempt_after_reconcile.record_version,
    )
    assert all(item.operation_id != attempt.operation_id for item in replayed.effect_quarantines)
    assert journal.read_view().revision == revision
    with pytest.raises(StateConflict, match="different note"):
        recovery.acknowledge(
            attempt.operation_id,
            note="different operator decision",
            expected_record_version=attempt_after_reconcile.record_version,
        )


def test_source_is_provenance_not_a_recovery_strategy(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    direct_repo = _repo(tmp_path / "direct")
    fleet_repo = _repo(tmp_path / "fleet")
    direct = _begin_unknown_direct(database, attempts, direct_repo)
    fleet = _begin_unknown_fleet(database, attempts, fleet_repo, fleet_run_id="fleet-one-owner")

    recovery = effect_recovery(
        database,
        attempts,
        journal,
        ExecutionCoordinator(database),
        GitService(),
    )
    direct_result = recovery.reconcile(direct.operation_id)
    fleet_result = recovery.reconcile(fleet.operation_id)

    assert direct_result.reconciliation.outcome is fleet_result.reconciliation.outcome
    assert direct_result.effect.origin.kind is EffectAttemptOwner.DIRECT
    assert fleet_result.effect.origin.kind is EffectAttemptOwner.FLEET


def test_operator_reconciliation_retry_uses_one_attempt_and_journal_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "operator-one-cut.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = _repo(tmp_path)
    attempt = _begin_unknown_direct(database, attempts, repository)
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
    recovery = effect_recovery(
        database, attempts, journal, ExecutionCoordinator(database), GitService()
    )
    first = recovery.reconcile(attempt.operation_id)
    _forbid_standalone_recovery_reads(monkeypatch, attempts, journal)
    assert recovery.reconcile(attempt.operation_id) == first


def test_terminal_reconciliation_replay_uses_one_attempt_and_journal_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "terminal-one-cut.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = _repo(tmp_path)
    attempt = _begin_unknown_direct(database, attempts, repository)
    recovery = effect_recovery(
        database, attempts, journal, ExecutionCoordinator(database), GitService()
    )
    first = recovery.reconcile(attempt.operation_id)
    _forbid_standalone_recovery_reads(monkeypatch, attempts, journal)
    assert recovery.reconcile(attempt.operation_id) == first


def test_acknowledgement_uses_one_attempt_and_journal_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "ack-one-cut.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = _repo(tmp_path)
    attempt = _begin_unknown_direct(database, attempts, repository)
    (repository / "pyproject.toml").write_text("[project]\nname='changed'\n", encoding="utf-8")
    recovery = effect_recovery(
        database, attempts, journal, ExecutionCoordinator(database), GitService()
    )
    reconciled = recovery.reconcile(attempt.operation_id)
    _forbid_standalone_recovery_reads(monkeypatch, attempts, journal)
    acknowledged = recovery.acknowledge(attempt.operation_id, note="inspected")
    assert acknowledged.reconciliation == reconciled.reconciliation


def _forbid_standalone_recovery_reads(
    monkeypatch: pytest.MonkeyPatch,
    attempts: RepositoryEffectAttemptStore,
    journal: Journal,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery must use one caller-owned database read cut")

    monkeypatch.setattr(attempts, "get", forbidden)
    monkeypatch.setattr(journal, "read_view", forbidden)


class _CountingEffectRunner(ExternalRunnerStub):
    def __init__(self) -> None:
        self.calls = 0

    def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
        self.calls += 1
        report = make_operation_report(
            request.attempt.repository,
            request.attempt.action,
            fleet_run_id=request.attempt.fleet_run_id,
            operation_id=request.attempt.operation_id,
        )
        return OperationExecution(stdout="", stderr="", report=report)


class _BindingEffectRunner(_CountingEffectRunner):
    def prepare(self, request: CapabilityExecutionRequest):
        prepared = super().prepare(request)
        prepared.execution_input_id = "execution-input-a"
        return prepared


class _AmbiguousAttemptMutationSession:
    def __init__(self, session, *, after_commit=None) -> None:
        self._session = session
        self._lost = False
        self._after_commit = after_commit

    def __getattr__(self, name):
        return getattr(self._session, name)

    def mutate(self, mutate):
        result = self._session.mutate(mutate)
        if not self._lost:
            self._lost = True
            if self._after_commit is not None:
                self._after_commit()
            raise RuntimeError("effect-attempt commit response lost after commit")
        return result


def _direct_effect_case(tmp_path: Path, runner: ExternalRunnerStub):
    database = LocalDatabase(tmp_path / "effect-replay.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repo(tmp_path / "direct-effect")
    intent = resolve_capability_intent(OperationAction.UPDATE)
    operation_id = "effect-replay"
    ticket, attempt = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: attempts.begin_direct_in(
            connection,
            repository,
            intent.definition.durable_snapshot(),
            target=intent.target,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
            operation_id=operation_id,
        ),
    )
    effects = effect_execution(
        database,
        CapabilityExecutor(fixed_capability_runners(runner)),
        journal,
        attempts,
        GitService(),
    )
    return database, journal, attempts, execution, intent, operation_id, ticket, attempt, effects


def test_fleet_claim_replays_exact_attempt_after_ambiguous_commit_response(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "fleet-claim-replay.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repo(tmp_path / "fleet-effect")
    identity = RepositoryIdentity(name=repository.name, path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    admit_test_run(journal, "fleet-claim-replay", OperationAction.UPDATE, [identity])
    start_run(journal, "fleet-claim-replay")
    ticket = execution.reserve_fleet_with(
        "fleet-claim-replay",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    runner = _CountingEffectRunner()
    effects = effect_execution(
        database,
        CapabilityExecutor(fixed_capability_runners(runner)),
        journal,
        attempts,
        GitService(),
    )
    session = execution.activate(ticket)

    result = effects.execute_fleet_target(
        resolve_capability_intent(OperationAction.UPDATE),
        capability_snapshot(OperationAction.UPDATE),
        entry,
        _AmbiguousAttemptMutationSession(session),  # type: ignore[arg-type]
    )
    session.finish(lambda _connection: None)

    assert runner.calls == 1
    assert journal.operation(result.report.provenance.operation_id) == result.report
    with pytest.raises(OperationNotFound):
        attempts.get(result.report.provenance.operation_id)


def test_execution_input_binding_replays_after_ambiguous_commit_response(tmp_path: Path) -> None:
    runner = _BindingEffectRunner()
    _, journal, attempts, execution, intent, operation_id, ticket, attempt, effects = (
        _direct_effect_case(tmp_path, runner)
    )

    result = effects.execute_direct_claimed(
        attempt=attempt,
        intent=intent,
        session=_AmbiguousAttemptMutationSession(execution.activate(ticket)),  # type: ignore[arg-type]
    )

    assert runner.calls == 1
    assert result.report == journal.require_operation(operation_id)
    with pytest.raises(OperationNotFound):
        attempts.get(operation_id)


def test_effect_started_marker_replays_before_runner_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    runner = _CountingEffectRunner()
    _, journal, attempts, execution, intent, operation_id, ticket, attempt, effects = (
        _direct_effect_case(tmp_path, runner)
    )

    result = effects.execute_direct_claimed(
        attempt=attempt,
        intent=intent,
        session=_AmbiguousAttemptMutationSession(execution.activate(ticket)),  # type: ignore[arg-type]
    )

    assert runner.calls == 1
    assert result.report == journal.require_operation(operation_id)
    with pytest.raises(OperationNotFound):
        attempts.get(operation_id)


def test_pre_effect_replay_rejects_a_later_attempt_revision(tmp_path: Path) -> None:
    runner = _CountingEffectRunner()
    _, _, attempts, execution, _, operation_id, ticket, _, _ = _direct_effect_case(tmp_path, runner)
    session = execution.activate(ticket)

    def supersede() -> None:
        session.mutate(lambda connection: attempts.start_effect_in(connection, operation_id))

    with pytest.raises(RuntimeError, match="commit response lost"):
        mutate_pre_effect_attempt(
            _AmbiguousAttemptMutationSession(session, after_commit=supersede),  # type: ignore[arg-type]
            attempts,
            operation_id,
            lambda connection: attempts.bind_execution_input_in(
                connection, operation_id, "execution-input-a"
            ),
        )

    assert attempts.get(operation_id).status is EffectAttemptStatus.EFFECT_STARTED
