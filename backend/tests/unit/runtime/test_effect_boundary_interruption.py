from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import (
    ExecutorPlacementLost,
    FleetExtensionInvalid,
    InvalidRunTransition,
    OperationNotFound,
    RepositoryStateChanged,
    StateConflict,
)
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.vocabulary import (
    EffectAttemptStatus,
    EffectBoundaryKind,
    FleetRunEventType,
    FleetRunStatus,
    OperationAction,
)
from oh_goon.core.vocabulary.fleet import FleetScope
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import DatabasePathIdentityLost, LocalDatabase
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


def test_contained_effect_crash_after_start_retires_attempt_without_unknown_state(
    tmp_path: Path,
) -> None:
    class ContainedAdapter(ExternalRunnerStub):
        def effect_boundary(self, _intent) -> EffectBoundaryKind:
            return EffectBoundaryKind.CONTAINED

        def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
            raise AssertionError("fault injector stops execution before adapter call")

    database = LocalDatabase(tmp_path / "contained-crash.db")
    journal = Journal(database)
    repository = _repository(tmp_path)
    operations, attempts, _, _ = direct_operation_stack(
        database,
        journal,
        ContainedAdapter(),
        faults=RaisingFaultInjector({FaultPoint.AFTER_DIRECT_EFFECT_STARTED}),
    )

    with pytest.raises(RuntimeError, match="AFTER_DIRECT_EFFECT_STARTED"):
        operations.run(repository, OperationAction.UPDATE)

    assert attempts.all() == []
    assert journal.history(repository) == []


def test_database_identity_loss_is_not_masked_by_interruption_settlement(tmp_path: Path) -> None:
    """An untrusted DB cannot also be used to persist cleanup for its own identity loss."""

    class IdentityLossFault:
        def hit(self, point: FaultPoint) -> None:
            if point is FaultPoint.AFTER_DIRECT_ATTEMPT_CLAIM:
                raise DatabasePathIdentityLost("primary admitted database identity lost")

    database = LocalDatabase(tmp_path / "identity-loss-primary.db")
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    execution = ExecutionCoordinator(database)
    repository = _repository(tmp_path)
    intent = resolve_capability_intent(OperationAction.UPDATE)
    operation_id = "identity-loss-primary"
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
        faults=IdentityLossFault(),
    )
    class CountingSettlementSession:
        def __init__(self, delegate) -> None:
            self._delegate = delegate
            self.settlement_calls = 0

        def mutate(self, mutate):
            self.settlement_calls += 1
            return self._delegate.mutate(mutate)

        def finish(self, mutate):
            self.settlement_calls += 1
            return self._delegate.finish(mutate)

    session = CountingSettlementSession(execution.activate(ticket))

    # The injected identity loss occurs after the direct claim and before preparation,
    # so any subsequent session mutation/finish would be interruption settlement.
    with pytest.raises(DatabasePathIdentityLost, match="primary admitted database identity lost"):
        effects.execute_direct_claimed(
            attempt=attempt,
            intent=intent,
            session=session,  # type: ignore[arg-type]
        )

    assert session.settlement_calls == 0
