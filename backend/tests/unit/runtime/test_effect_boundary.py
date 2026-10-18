from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import (
    ExecutorPlacementLost,
    ExecutorTrustRevoked,
    FleetExtensionInvalid,
    InvalidRunTransition,
    OperationNotFound,
    RepositoryStateChanged,
    StateConflict,
)
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.vocabulary import (
    EffectAttemptStatus,
    EffectBoundaryKind,
    FleetRunEventType,
    FleetRunStatus,
    OperationAction,
)
from oh_goon.core.vocabulary.fleet import FleetScope
from oh_goon.engine.command import CommandAuthorityTerminatedError, run_command
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.fault_injection import FaultPoint, RaisingFaultInjector
from oh_goon.runtime.journal import Journal
from oh_goon.services.capability_executor import CapabilityExecutor
from tests.factories.capabilities import admit_test_run, capability_snapshot
from tests.factories.journal import start_run
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_effect_runtime import effect_execution, effect_recovery
from tests.factories.repository_state import baseline_for
from tests.factories.runtime_stack import direct_operation_stack
from tests.fakes.capability_runners import ExternalRunnerStub, fixed_capability_runners


class _CountingAdapter(ExternalRunnerStub):
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


class _LiveAuthorityAdapter(ExternalRunnerStub):
    def __init__(self, revoke) -> None:
        self._revoke = revoke

    def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
        def observe(event) -> None:
            if event.kind == "PROCESS_STARTED":
                self._revoke()

        run_command(
            sys.executable,
            "-S",
            "-c",
            "import time; time.sleep(5)",
            timeout=5,
            cancel_requested=request.cancel_requested,
            termination_requested=request.termination_requested,
            process_observer=observe,
        )
        raise AssertionError("authority-terminated command unexpectedly returned")


class _RejectingPreflightExecutor:
    def __init__(self) -> None:
        self.execute_calls = 0

    def effect_boundary(self, _intent) -> EffectBoundaryKind:
        return EffectBoundaryKind.EXTERNAL

    def prepare(self, _request: CapabilityExecutionRequest):
        raise FleetExtensionInvalid("extension binding changed before effects")

    def execute(self, _prepared) -> OperationExecution:
        self.execute_calls += 1
        raise AssertionError("execute must not run after failed preparation")


class _AmbiguousFinishSession:
    """Delegate the owner transaction, then simulate losing its successful response."""

    def __init__(self, session, *, after_commit=None) -> None:
        self._session = session
        self._after_commit = after_commit

    def cancel_requested(self) -> bool:
        return self._session.cancel_requested()

    @contextmanager
    def preparation_cancellation_observer(self):
        with self._session.preparation_cancellation_observer() as observer:
            yield observer

    def mutate(self, mutate):
        return self._session.mutate(mutate)

    def finish(self, mutate):
        self._session.finish(mutate)
        if self._after_commit is not None:
            self._after_commit()
        raise RuntimeError("settlement response lost after commit")


class _MutateAfterDirectClaim:
    def __init__(self, repository: Path) -> None:
        self._repository = repository

    def hit(self, point: FaultPoint) -> None:
        if point is FaultPoint.AFTER_DIRECT_ATTEMPT_CLAIM:
            (self._repository / "pyproject.toml").write_text(
                "[project]\nname='changed-between-claim-and-effect'\n",
                encoding="utf-8",
            )


class _MutateAfterFleetClaim:
    def __init__(self, repository: Path) -> None:
        self._repository = repository

    def hit(self, point: FaultPoint) -> None:
        if point is FaultPoint.AFTER_REPOSITORY_CLAIMED:
            (self._repository / "pyproject.toml").write_text(
                "[project]\nname='changed-between-claim-and-effect'\n",
                encoding="utf-8",
            )


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return repository


def _begin_direct_attempt(
    database: LocalDatabase,
    attempts: RepositoryEffectAttemptStore,
    repository: Path,
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


def test_direct_repository_change_after_claim_never_reaches_adapter(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    repository = _repository(tmp_path)
    adapter = _CountingAdapter()
    operations, attempts, _, _ = direct_operation_stack(
        database, journal, adapter, faults=_MutateAfterDirectClaim(repository)
    )

    with pytest.raises(RepositoryStateChanged):
        operations.run(repository, OperationAction.UPDATE)

    assert adapter.calls == 0
    assert attempts.all() == []
    assert journal.history(repository) == []


def test_direct_effect_started_is_the_only_crash_boundary_that_becomes_unknown(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    repository = _repository(tmp_path)
    operations, attempts, _, _ = direct_operation_stack(
        database,
        journal,
        _CountingAdapter(),
        faults=RaisingFaultInjector({FaultPoint.AFTER_DIRECT_EFFECT_STARTED}),
    )

    with pytest.raises(RuntimeError, match="AFTER_DIRECT_EFFECT_STARTED"):
        operations.run(repository, OperationAction.UPDATE)

    attempt = attempts.all()[0]
    assert attempt.status is EffectAttemptStatus.OUTCOME_UNKNOWN
    assert attempt.quarantines_repository
    assert journal.operation(attempt.operation_id) is None


def test_capability_preflight_failure_retires_unstarted_attempt_without_quarantine(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "preflight.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    intent = resolve_capability_intent(OperationAction.UPDATE)
    baseline = baseline_for(repository)
    operation_id = "preflight-failure"
    ticket, _attempt = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: attempts.begin_direct_in(
            connection,
            repository,
            intent.definition.durable_snapshot(),
            target=intent.target,
            baseline=baseline,
            effect_boundary=EffectBoundaryKind.EXTERNAL,
            operation_id=operation_id,
        ),
    )
    executor = _RejectingPreflightExecutor()
    effects = effect_execution(
        database,
        executor,  # type: ignore[arg-type]
        journal,
        attempts,
        GitService(),
    )

    with pytest.raises(FleetExtensionInvalid, match="binding changed"):
        effects.execute_direct_claimed(
            attempt=_attempt,
            intent=intent,
            session=execution.activate(ticket),
        )

    assert executor.execute_calls == 0
    assert attempts.all() == []
    assert execution.active_lease() is None
    assert journal.operation(operation_id) is None


def test_claimed_attempt_recovers_without_quarantine(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    repository = _repository(tmp_path)
    attempt = _begin_direct_attempt(database, attempts, repository)

    effect_recovery(
        database,
        attempts,
        journal,
        ExecutionCoordinator(database),
        GitService(),
    ).recover()

    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)


def test_fleet_authority_revalidation_precedes_repository_claim(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    identity = RepositoryIdentity(name=repository.name, path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    admit_test_run(journal, "fleet-placement-fence", OperationAction.UPDATE, [identity])
    start_run(journal, "fleet-placement-fence")
    ticket = execution.reserve_fleet_with(
        "fleet-placement-fence",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]

    class ExecutorStub:
        def effect_boundary(self, _intent):
            return EffectBoundaryKind.EXTERNAL

        def prepare(self, _request):
            raise AssertionError("prepare must not run after placement revalidation fails")

    effects = effect_execution(
        database,
        ExecutorStub(),
        journal,
        attempts,
        GitService(),  # type: ignore[arg-type]
    )
    session = execution.activate(ticket)

    def reject_placement(_connection) -> None:
        raise ExecutorPlacementLost("executor trust was revoked")

    with pytest.raises(ExecutorPlacementLost, match="trust was revoked"):
        effects.execute_fleet_target(
            resolve_capability_intent(OperationAction.UPDATE),
            capability_snapshot(OperationAction.UPDATE),
            entry,
            session,
            validate_authority_in=reject_placement,
        )
    session.finish(lambda _connection: None)

    with pytest.raises(OperationNotFound):
        attempts.get("fleet-placement-fence:" + identity.repository_id)


def test_fleet_authority_is_revalidated_at_last_pre_effect_cut(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "pre-effect-authority.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    identity = RepositoryIdentity(name=repository.name, path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    run_id = "fleet-pre-effect-authority"
    intent = resolve_capability_intent(OperationAction.UPDATE)
    admit_test_run(journal, run_id, OperationAction.UPDATE, [identity])
    start_run(journal, run_id)
    ticket = execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    adapter = _CountingAdapter()
    effects = effect_execution(
        database,
        CapabilityExecutor(fixed_capability_runners(adapter)),
        journal,
        attempts,
        GitService(),
    )
    checks = 0

    def validate(_connection) -> None:
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise ExecutorPlacementLost("executor authority changed before effect start")

    session = execution.activate(ticket)
    with pytest.raises(ExecutorPlacementLost, match="before effect start"):
        effects.execute_fleet_target(
            intent,
            intent.definition.durable_snapshot(),
            entry,
            session,
            validate_authority_in=validate,
        )
    session.finish(lambda _connection: None)

    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    assert checks == 2
    assert adapter.calls == 0
    with pytest.raises(OperationNotFound):
        attempts.get(operation_id)
    assert [event.event_type for event in journal.run_events(run_id).events] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_STARTED,
    ]


def test_fleet_authority_is_revalidated_before_canonical_success(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "success-authority.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    identity = RepositoryIdentity(name=repository.name, path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    run_id = "fleet-success-authority"
    intent = resolve_capability_intent(OperationAction.UPDATE)
    admit_test_run(journal, run_id, OperationAction.UPDATE, [identity])
    start_run(journal, run_id)
    ticket = execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    adapter = _CountingAdapter()
    effects = effect_execution(
        database,
        CapabilityExecutor(fixed_capability_runners(adapter)),
        journal,
        attempts,
        GitService(),
    )
    checks = 0

    def validate(_connection) -> None:
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise ExecutorPlacementLost("executor authority changed before success")

    session = execution.activate(ticket)
    with pytest.raises(ExecutorPlacementLost, match="before success"):
        effects.execute_fleet_target(
            intent,
            intent.definition.durable_snapshot(),
            entry,
            session,
            validate_authority_in=validate,
        )
    session.finish(lambda _connection: None)

    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    assert checks == 3
    assert adapter.calls == 1
    assert journal.operation(operation_id) is None
    assert attempts.get(operation_id).status is EffectAttemptStatus.OUTCOME_UNKNOWN
    assert FleetRunEventType.REPOSITORY_STARTED in {
        event.event_type for event in journal.run_events(run_id).events
    }


def test_live_executor_authority_loss_terminates_owned_physical_execution(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "live-authority.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    identity = RepositoryIdentity(name=repository.name, path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    run_id = "fleet-live-authority"
    intent = resolve_capability_intent(OperationAction.UPDATE)
    admit_test_run(journal, run_id, OperationAction.UPDATE, [identity])
    start_run(journal, run_id)
    ticket = execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    revoked = False

    def revoke() -> None:
        nonlocal revoked
        revoked = True

    adapter = _LiveAuthorityAdapter(revoke)
    effects = effect_execution(
        database,
        CapabilityExecutor(fixed_capability_runners(adapter)),
        journal,
        attempts,
        GitService(),
    )

    def validate(_connection) -> None:
        if revoked:
            raise ExecutorTrustRevoked("executor trust was revoked")

    session = execution.activate(ticket)
    with pytest.raises(CommandAuthorityTerminatedError) as raised:
        effects.execute_fleet_target(
            intent,
            intent.definition.durable_snapshot(),
            entry,
            session,
            validate_authority_in=validate,
        )
    session.finish(lambda _connection: None)

    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    assert raised.value.request.cause == "trust_revoke"
    assert raised.value.request.reason == "executor_trust_revoked"
    assert attempts.get(operation_id).status is EffectAttemptStatus.OUTCOME_UNKNOWN
    assert journal.operation(operation_id) is None


def test_fleet_repository_change_after_claim_blocks_effect_start_and_adapter(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    identity = RepositoryIdentity(name=repository.name, path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    admit_test_run(journal, "fleet-fence", OperationAction.UPDATE, [identity])
    start_run(journal, "fleet-fence")
    ticket = execution.reserve_fleet_with(
        "fleet-fence",
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]

    class ExecutorStub:
        calls = 0

        def effect_boundary(self, _intent):
            return EffectBoundaryKind.EXTERNAL

        def prepare(self, _request):
            raise AssertionError("prepare must not run after a failed repository fence")

        def execute(self, _prepared):
            self.calls += 1
            raise AssertionError("execute must not run after a failed repository fence")

    executor = ExecutorStub()
    effects = effect_execution(
        database,
        executor,  # type: ignore[arg-type]
        journal,
        attempts,
        GitService(),
        faults=_MutateAfterFleetClaim(repository),
    )

    session = execution.activate(ticket)
    with pytest.raises(RepositoryStateChanged):
        effects.execute_fleet_target(
            resolve_capability_intent(OperationAction.UPDATE),
            resolve_capability_intent(OperationAction.UPDATE).definition.durable_snapshot(),
            entry,
            session,
        )
    session.finish(lambda _connection: None)

    assert executor.calls == 0
    assert [
        item for item in attempts.recovery_candidates() if item.fleet_run_id == "fleet-fence"
    ] == []
    assert [event.event_type for event in journal.run_events("fleet-fence").events] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_STARTED,
    ]


def test_fleet_effect_start_and_repository_lifecycle_commit_atomically(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "atomic-start.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    identity = RepositoryIdentity(name=repository.name, path=repository)
    entry = IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)
    run_id = "fleet-atomic-start"
    intent = resolve_capability_intent(OperationAction.UPDATE)
    admit_test_run(journal, run_id, OperationAction.UPDATE, [identity])
    start_run(journal, run_id)
    ticket = execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    effects = effect_execution(
        database,
        CapabilityExecutor(fixed_capability_runners(_CountingAdapter())),
        journal,
        attempts,
        GitService(),
        faults=RaisingFaultInjector({FaultPoint.AFTER_REPOSITORY_EFFECT_STARTED}),
    )

    session = execution.activate(ticket)
    with pytest.raises(RuntimeError, match="AFTER_REPOSITORY_EFFECT_STARTED"):
        effects.execute_fleet_target(
            intent,
            intent.definition.durable_snapshot(),
            entry,
            session,
        )
    session.finish(lambda _connection: None)

    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    assert attempts.get(operation_id).status is EffectAttemptStatus.OUTCOME_UNKNOWN
    assert [event.event_type for event in journal.run_events(run_id).events] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_STARTED,
        FleetRunEventType.REPOSITORY_STARTED,
    ]


def test_fleet_attempt_rejects_noncanonical_operation_identity(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "noncanonical-attempt.db")
    attempts = RepositoryEffectAttemptStore(database)
    repository = _repository(tmp_path)

    with database.transaction(write=True) as connection:
        with pytest.raises(StateConflict, match="canonical"):
            attempts.begin_fleet_in(
                connection,
                repository,
                capability_snapshot(OperationAction.UPDATE),
                fleet_run_id="fleet-noncanonical",
                operation_id="op-not-canonical",
                target=None,
                baseline=baseline_for(repository),
                effect_boundary=EffectBoundaryKind.EXTERNAL,
            )


def test_restart_after_claim_before_effect_is_retryable(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = _repository(tmp_path)
    identity = RepositoryIdentity(name=repository.name, path=repository)
    admit_test_run(journal, "fleet-pre-effect", OperationAction.UPDATE, [identity])
    start_run(journal, "fleet-pre-effect")
    with database.transaction(write=True) as connection:
        attempt = attempts.begin_fleet_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            fleet_run_id="fleet-pre-effect",
            operation_id=repository_attempt_operation_id(
                "fleet-pre-effect", identity.repository_id
            ),
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )

    effect_recovery(
        database,
        attempts,
        journal,
        ExecutionCoordinator(database),
        GitService(),
    ).recover()
    detail = journal.read_view().run_detail("fleet-pre-effect")

    assert detail is not None
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure is not None
    assert detail.run.failure.code == "RUN_INTERRUPTED_BY_RESTART"
    assert detail.run.failure.retryable
    with pytest.raises(OperationNotFound):
        attempts.get(attempt.operation_id)


def test_canonical_commit_and_attempt_retirement_roll_back_together(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    repository = _repository(tmp_path)
    identity = RepositoryIdentity(name=repository.name, path=repository)
    admit_test_run(journal, "fleet-lineage", OperationAction.UPDATE, [identity])
    start_run(journal, "fleet-lineage")
    operation_id = repository_attempt_operation_id("fleet-lineage", identity.repository_id)
    with database.transaction(write=True) as connection:
        attempts.begin_fleet_in(
            connection,
            repository,
            capability_snapshot(OperationAction.UPDATE),
            fleet_run_id="fleet-lineage",
            operation_id=operation_id,
            target=None,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
    report = make_operation_report(
        repository,
        OperationAction.UPDATE,
        fleet_run_id="fleet-lineage",
        operation_id=operation_id,
    )

    with pytest.raises(
        InvalidRunTransition, match="requires matching canonical REPOSITORY_STARTED"
    ):
        with database.transaction(write=True) as connection:
            journal.commit_operation_in(connection, report)
            attempts.retire_committed_in(connection, operation_id)

    assert journal.operation(operation_id) is None
    assert attempts.get(operation_id).status is EffectAttemptStatus.CLAIMED


def test_ambiguous_direct_settlement_replays_canonical_success(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    adapter = _CountingAdapter()
    intent = resolve_capability_intent(OperationAction.UPDATE)
    baseline = baseline_for(repository)
    operation_id = "direct-ambiguous-settlement"

    ticket, _attempt = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: attempts.begin_direct_in(
            connection,
            repository,
            intent.definition.durable_snapshot(),
            target=intent.target,
            baseline=baseline,
            effect_boundary=EffectBoundaryKind.EXTERNAL,
            operation_id=operation_id,
        ),
    )
    effects = effect_execution(
        database,
        CapabilityExecutor(fixed_capability_runners(adapter)),
        journal,
        attempts,
        GitService(),
    )

    result = effects.execute_direct_claimed(
        attempt=_attempt,
        intent=intent,
        session=_AmbiguousFinishSession(execution.activate(ticket)),  # type: ignore[arg-type]
    )

    assert adapter.calls == 1
    assert result.report == journal.require_operation(operation_id)
    assert result.returncode == result.report.outcome.returncode
    with pytest.raises(OperationNotFound):
        attempts.get(operation_id)
    assert execution.active_lease() is None


def test_ambiguous_unknown_settlement_preserves_original_effect_error(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "ambiguous-unknown.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    intent = resolve_capability_intent(OperationAction.UPDATE)
    operation_id = "ambiguous-unknown"
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
        CapabilityExecutor(fixed_capability_runners(_CountingAdapter())),
        journal,
        attempts,
        GitService(),
        faults=RaisingFaultInjector({FaultPoint.AFTER_DIRECT_EFFECT_STARTED}),
    )

    with pytest.raises(RuntimeError, match="AFTER_DIRECT_EFFECT_STARTED"):
        effects.execute_direct_claimed(
            attempt=attempt,
            intent=intent,
            session=_AmbiguousFinishSession(execution.activate(ticket)),  # type: ignore[arg-type]
        )

    assert attempts.get(operation_id).status is EffectAttemptStatus.OUTCOME_UNKNOWN
    assert execution.active_lease() is None


def test_ambiguous_contained_interruption_preserves_original_effect_error(tmp_path: Path) -> None:
    class ContainedRunner(_CountingAdapter):
        def effect_boundary(self, _intent) -> EffectBoundaryKind:
            return EffectBoundaryKind.CONTAINED

    database = LocalDatabase(tmp_path / "ambiguous-contained.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    intent = resolve_capability_intent(OperationAction.UPDATE)
    operation_id = "ambiguous-contained"
    ticket, attempt = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: attempts.begin_direct_in(
            connection,
            repository,
            intent.definition.durable_snapshot(),
            target=intent.target,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.CONTAINED,
            operation_id=operation_id,
        ),
    )
    effects = effect_execution(
        database,
        CapabilityExecutor(fixed_capability_runners(ContainedRunner())),
        journal,
        attempts,
        GitService(),
        faults=RaisingFaultInjector({FaultPoint.AFTER_DIRECT_EFFECT_STARTED}),
    )

    with pytest.raises(RuntimeError, match="AFTER_DIRECT_EFFECT_STARTED"):
        effects.execute_direct_claimed(
            attempt=attempt,
            intent=intent,
            session=_AmbiguousFinishSession(execution.activate(ticket)),  # type: ignore[arg-type]
        )

    with pytest.raises(OperationNotFound):
        attempts.get(operation_id)
    assert execution.active_lease() is None


def test_interruption_replay_rejects_superseding_attempt_after_lost_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "ambiguous-interruption-superseded.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    intent = resolve_capability_intent(OperationAction.UPDATE)
    operation_id = "ambiguous-interruption-superseded"
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

    def supersede() -> None:
        with database.transaction(write=True) as connection:
            attempts.begin_direct_in(
                connection,
                repository,
                intent.definition.durable_snapshot(),
                target=intent.target,
                baseline=baseline_for(repository),
                effect_boundary=EffectBoundaryKind.EXTERNAL,
                operation_id=operation_id,
            )

    effects = effect_execution(
        database,
        CapabilityExecutor(fixed_capability_runners(_CountingAdapter())),
        journal,
        attempts,
        GitService(),
        faults=RaisingFaultInjector({FaultPoint.AFTER_DIRECT_ATTEMPT_CLAIM}),
    )

    with pytest.raises(RuntimeError, match="settlement response lost after commit"):
        effects.execute_direct_claimed(
            attempt=attempt,
            intent=intent,
            session=_AmbiguousFinishSession(execution.activate(ticket), after_commit=supersede),  # type: ignore[arg-type]
        )

    assert attempts.get(operation_id).status is EffectAttemptStatus.CLAIMED
