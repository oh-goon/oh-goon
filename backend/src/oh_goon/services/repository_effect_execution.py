from __future__ import annotations

import sqlite3
from collections.abc import Callable
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentReservation
from dataclasses import replace

from oh_goon.core.capability_model import ResolvedCapabilityIntent
from oh_goon.core.errors import (
    ExecutorGenerationSuperseded,
    ExecutorPlacementLost,
    ExecutorTrustRevoked,
    OwnershipLost,
    StateConflict,
)
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.effect_attempt import ClaimedRepositoryEffectAttempt
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.vocabulary import EffectBoundaryKind, FleetRunEventType
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import DatabasePathIdentityLost
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionSession
from oh_goon.runtime.fault_injection import FaultInjector, FaultPoint, NoFaultInjector
from oh_goon.runtime.journal import Journal
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.services.capability_executor import CapabilityExecutor
from oh_goon.services.repository_effect_fence import RepositoryEffectFence
from oh_goon.services.repository_effect_preparation import mutate_pre_effect_attempt
from oh_goon.services.repository_effect_settlement import RepositoryEffectSettlement


class RepositoryEffectExecution:
    """The single executable lifecycle for physical repository effects.

    Direct and Fleet differ only in admission/provenance and in whether settling one
    repository also closes the process-wide execution lease.  Fencing, effect start,
    runner execution, canonical commit, and uncertainty classification are owned here
    exactly once.
    """

    def __init__(
        self,
        executor: CapabilityExecutor,
        journal: Journal,
        attempts: RepositoryEffectAttemptStore,
        git: GitService,
        *,
        faults: FaultInjector | None = None,
        effect_fence: RepositoryEffectFence,
        settlement: RepositoryEffectSettlement,
    ) -> None:
        self._executor = executor
        self._journal = journal
        self._attempts = attempts
        self._git = git
        self._faults = faults or NoFaultInjector()
        self._effect_fence = effect_fence
        self._settlement = settlement

    def effect_boundary(self, intent: ResolvedCapabilityIntent) -> EffectBoundaryKind:
        return self._executor.effect_boundary(intent)

    def execute_direct_claimed(
        self,
        *,
        attempt: ClaimedRepositoryEffectAttempt,
        intent: ResolvedCapabilityIntent,
        session: ExecutionSession,
    ) -> OperationExecution:
        """Execute the one physical lifecycle after Direct admission has claimed it."""
        return self._execute_claimed(
            attempt=attempt,
            intent=intent,
            session=session,
            claim_fault=FaultPoint.AFTER_DIRECT_ATTEMPT_CLAIM,
            effect_started_fault=FaultPoint.AFTER_DIRECT_EFFECT_STARTED,
            close_execution=True,
        )

    def execute_fleet_target(
        self,
        intent: ResolvedCapabilityIntent,
        capability: CapabilitySnapshot,
        entry: IncludedRepositoryCatalogEntry,
        session: ExecutionSession,
        isolation_backend: PlanIsolationBackendEvidence | None = None,
        *,
        repository_admission: PlanRepositoryAdmissionEvidence | None = None,
        validate_authority_in: Callable[[sqlite3.Connection], None] | None = None,
        activity: Callable[[str], None] | None = None,
        local_lace_intent_for: Callable[..., LocalLaceIntentReservation] | None = None,
        admitted_run: FleetRunDetail | None = None,
    ) -> OperationExecution:
        """Execute one Fleet target under the already-admitted Fleet owner."""
        if session.cancel_requested():
            raise OperationCancelledError("Fleet run cancellation requested")
        fleet_run_id = session.require_fleet_run_id()
        repository = entry.repository.path
        if activity is not None:
            activity(f"Observing repository identity for {repository.name}")
        baseline = self._git.fingerprint(repository, session.cancel_requested)
        if activity is not None:
            activity(f"Repository identity observed for {repository.name}")
        operation_id = repository_attempt_operation_id(
            fleet_run_id,
            entry.repository.repository_id,
        )

        def claim(connection: sqlite3.Connection):
            if session.cancel_requested_in(connection):
                raise OperationCancelledError(
                    "Fleet run cancellation requested before repository claim"
                )
            if validate_authority_in is not None:
                validate_authority_in(connection)
            return self._attempts.begin_fleet_in(
                connection,
                repository,
                capability,
                fleet_run_id=fleet_run_id,
                operation_id=operation_id,
                target=intent.target,
                baseline=baseline,
                effect_boundary=self.effect_boundary(intent),
            )

        if activity is not None:
            activity(f"Claiming repository execution attempt for {repository.name}")
        attempt = mutate_pre_effect_attempt(session, self._attempts, operation_id, claim)
        if activity is not None:
            activity(f"Repository execution attempt claimed for {repository.name}")
        return self._execute_claimed(
            attempt=attempt,
            intent=intent,
            session=session,
            claim_fault=FaultPoint.AFTER_REPOSITORY_CLAIMED,
            effect_started_fault=FaultPoint.AFTER_REPOSITORY_EFFECT_STARTED,
            close_execution=False,
            isolation_backend=isolation_backend,
            repository_admission=repository_admission,
            validate_authority_in=validate_authority_in,
            activity=activity,
            local_lace_intent_for=local_lace_intent_for,
            admitted_run=admitted_run,
        )

    def _execute_claimed(
        self,
        *,
        attempt: ClaimedRepositoryEffectAttempt,
        intent: ResolvedCapabilityIntent,
        session: ExecutionSession,
        claim_fault: FaultPoint,
        effect_started_fault: FaultPoint,
        close_execution: bool,
        isolation_backend: PlanIsolationBackendEvidence | None = None,
        repository_admission: PlanRepositoryAdmissionEvidence | None = None,
        validate_authority_in: Callable[[sqlite3.Connection], None] | None = None,
        activity: Callable[[str], None] | None = None,
        local_lace_intent_for: Callable[..., LocalLaceIntentReservation] | None = None,
        admitted_run: FleetRunDetail | None = None,
    ) -> OperationExecution:
        result: OperationExecution | None = None
        prepared = None
        try:
            self._faults.hit(claim_fault)
            request = CapabilityExecutionRequest(
                attempt=attempt,
                definition=intent.definition,
                cancel_requested=session.cancel_requested,
                termination_requested=self._authority_termination_requested(
                    session, validate_authority_in
                ),
                isolation_backend=isolation_backend,
                repository_admission=repository_admission,
                local_lace_intent_for=local_lace_intent_for,
                admitted_run=admitted_run,
            )
            if activity is not None:
                activity("Preparing immutable execution inputs for " + attempt.repository.path.name)
            prepared = self._prepare(request, session=session)
            if activity is not None:
                activity(
                    "Execution inputs ready for "
                    + attempt.repository.path.name
                    + "; entering workload"
                )
            def start_effect(connection: sqlite3.Connection):
                if validate_authority_in is not None:
                    validate_authority_in(connection)
                return self._start_effect_in(connection, attempt)

            mutate_pre_effect_attempt(
                session,
                self._attempts,
                attempt.operation_id,
                start_effect,
            )
            self._faults.hit(effect_started_fault)
            result = self._executor.execute(prepared, request)
            self._faults.hit(FaultPoint.BEFORE_OPERATION_COMMIT)
            def commit_success(connection: sqlite3.Connection) -> None:
                if validate_authority_in is not None:
                    validate_authority_in(connection)
                self._settlement.commit_success_in(
                    connection, result.report, attempt.operation_id
                )

            _settle(session, commit_success, close_execution=close_execution)
            self._record_post_settlement_maintenance(prepared, attempt)
            prepared = None
            return result
        except BaseException as exc:
            replayed = _canonical_execution_after_ambiguous_settlement(
                self._journal, attempt.operation_id, result, exc
            )
            if replayed is not None:
                return replayed
            if isinstance(exc, DatabasePathIdentityLost):
                # The durable database is the authority needed to record interruption
                # evidence. Once that exact admitted SQLite identity is lost, a second
                # settlement transaction cannot be trusted and can only mask the
                # primary identity failure with another fresh-open error. Preserve the
                # first diagnostic and leave recovery to the fail-closed DB owner.
                raise
            self._record_interruption(
                session,
                attempt.operation_id,
                error=exc,
                close_execution=close_execution,
            )
            raise
        finally:
            if prepared is not None:
                prepared.close()

    @staticmethod
    def _authority_termination_requested(
        session: ExecutionSession,
        validate_authority_in: Callable[[sqlite3.Connection], None] | None,
    ) -> Callable[[], TerminationSignal | None] | None:
        if validate_authority_in is None:
            return None

        def observe() -> TerminationSignal | None:
            try:
                session.observe(validate_authority_in)
            except ExecutorTrustRevoked:
                return TerminationSignal(
                    cause="trust_revoke",
                    reason="executor_trust_revoked",
                )
            except ExecutorGenerationSuperseded:
                return TerminationSignal(
                    cause="operator",
                    reason="executor_generation_superseded",
                )
            except ExecutorPlacementLost:
                return TerminationSignal(
                    cause="operator",
                    reason="exact_executor_authority_lost",
                )
            except StateConflict:
                return TerminationSignal(
                    cause="operator",
                    reason="fleet_execution_authority_changed",
                )
            return None

        return observe

    def _start_effect_in(
        self, connection: sqlite3.Connection, attempt: ClaimedRepositoryEffectAttempt
    ):
        """Cross EFFECT_STARTED and Fleet REPOSITORY_STARTED as one durable semantic cut."""
        started = self._attempts.start_effect_in(connection, attempt.operation_id)
        if attempt.fleet_run_id is not None:
            self._journal.record_execution_lifecycle_in(
                connection,
                fleet_run_id=attempt.fleet_run_id,
                repository_id=attempt.repository.repository_id,
                operation_id=attempt.operation_id,
                event_type=FleetRunEventType.REPOSITORY_STARTED,
            )
        return started

    def _prepare(
        self, request: CapabilityExecutionRequest, *, session: ExecutionSession
    ):
        """Freeze execution inputs under the one repository-effect lifecycle owner."""
        self._effect_fence.verify(
            request.attempt.repository.path, request.attempt.baseline, session.cancel_requested
        )
        with session.preparation_cancellation_observer() as observe_preparation_cancellation:
            prepared = self._executor.prepare(
                replace(request, cancel_requested=observe_preparation_cancellation)
            )
        try:
            execution_input_id = prepared.execution_input_id
            if execution_input_id is not None:
                mutate_pre_effect_attempt(
                    session,
                    self._attempts,
                    request.attempt.operation_id,
                    lambda connection: self._attempts.bind_execution_input_in(
                        connection, request.attempt.operation_id, execution_input_id
                    ),
                )
            self._effect_fence.verify(
                request.attempt.repository.path,
                request.attempt.baseline,
                session.cancel_requested,
            )
            return prepared
        except BaseException:
            prepared.close()
            raise

    def _record_post_settlement_maintenance(self, prepared, attempt) -> None:
        prepared.close()
        receipt = getattr(prepared, "maintenance_receipt", None)
        if receipt is None or attempt.fleet_run_id is None:
            return
        self._journal.record_storage_maintenance(
            fleet_run_id=attempt.fleet_run_id,
            repository_id=attempt.repository.repository_id,
            operation_id=attempt.operation_id,
            receipt=receipt,
        )

    def _record_interruption(
        self,
        session: ExecutionSession,
        operation_id: str,
        *,
        error: BaseException,
        close_execution: bool,
    ) -> None:
        note = (
            "Execution ended after externally visible effects could have started "
            f"but before a canonical report was committed: {error.__class__.__name__}"
        )
        expected = None

        def settle(connection):
            nonlocal expected
            expected = self._settlement.settle_interruption_evidence_in(
                connection,
                operation_id,
                unknown_note=note,
            )
            return expected.disposition

        try:
            _settle(session, settle, close_execution=close_execution)
        except OwnershipLost:
            # Ownership loss is fail-closed. Recovery classifies the same durable row.
            return
        except BaseException:
            if expected is None or not self._settlement.interruption_evidence_matches(expected):
                raise
            # The interruption fact committed; preserve the original execution error.
            return


def _settle(session: ExecutionSession, mutate, *, close_execution: bool):
    if close_execution:
        return session.finish(mutate)
    return session.mutate(mutate)


def _canonical_execution_after_ambiguous_settlement(
    journal: Journal,
    operation_id: str,
    result: OperationExecution | None,
    error: BaseException,
) -> OperationExecution | None:
    """Resolve an uncertain settlement response from immutable canonical evidence."""
    if result is None:
        return None
    committed = journal.operation(operation_id)
    if committed is None:
        return None
    if committed != result.report:
        raise StateConflict(
            f"Canonical operation identity {operation_id} does not match the executed report"
        ) from error
    return OperationExecution(stdout=result.stdout, stderr=result.stderr, report=committed)
