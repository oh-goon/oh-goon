from __future__ import annotations

from oh_goon.core.effect_attempts import (
    EXTERNAL_OUTCOME_UNCERTAIN_STATUSES,
    OPERATOR_REQUIRED_EFFECT_STATUSES,
)
from oh_goon.core.errors import ExecutionBusy, OperationNotFound, StateConflict
from oh_goon.core.models.effect_attempt import (
    RepositoryEffectAttempt,
    effect_attempt_reconciliation,
)
from oh_goon.core.models.reconciliation import (
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    ProcessStartedEvent,
    ProcessStateUnknownEvent,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    EffectAttemptStatus,
    EffectBoundaryKind,
    FleetRunEventType,
    FleetRunStatus,
    FleetRunTerminalIntent,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.enforcer_lace_restart_reclaim_fence import (
    require_no_original_lace_intent_before_reclamation_in,
)
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.executor_capacity import FleetExecutorCapacityReservations
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.journal_view import JournalReadView
from oh_goon.services.reconciliation import RepositoryReconciler
from oh_goon.services.repository_effect_settlement import (
    RepositoryEffectInterruptionDisposition,
    RepositoryEffectInterruptionEvidence,
    RepositoryEffectSettlement,
)


class RepositoryEffectRecovery:
    """One startup/operator recovery path for unresolved Direct and Fleet effects."""

    def __init__(
        self,
        database: LocalDatabase,
        attempts: RepositoryEffectAttemptStore,
        journal: Journal,
        execution: ExecutionCoordinator,
        reconciler: RepositoryReconciler,
        settlement: RepositoryEffectSettlement,
    ) -> None:
        self._database = database
        self._attempts = attempts
        self._journal = journal
        self._execution = execution
        self._reconciler = reconciler
        self._settlement = settlement

    def recover(self) -> None:
        """Recover only when no live Direct/Fleet execution owner exists."""
        if self._execution.active_lease() is not None:
            return
        recovering_run_id: str | None = None
        try:
            recovering_run_id = self._terminalize_interrupted_fleet()
            for attempt in self._attempts.recovery_candidates():
                self._recover_attempt(attempt)
            if recovering_run_id is not None:
                with self._database.transaction(write=True) as connection:
                    self._execution.assert_idle(connection)
                    self._journal.record_reconciliation_lifecycle_in(
                        connection,
                        recovering_run_id,
                        FleetRunEventType.RECONCILIATION_COMPLETED,
                    )
        except ExecutionBusy:
            # Another process acquired execution ownership after the initial check.
            # Stop recovery immediately; the unresolved RECONCILIATION_STARTED fact is
            # canonical evidence that recovery did not settle.
            return
        except BaseException as exc:
            self._record_recovery_failure(recovering_run_id, exc)
            raise

    def _record_recovery_failure(self, run_id: str | None, exc: BaseException) -> None:
        if run_id is None:
            return
        try:
            with self._database.transaction(write=True) as connection:
                self._execution.assert_idle(connection)
                self._journal.record_reconciliation_lifecycle_in(
                    connection,
                    run_id,
                    FleetRunEventType.RECONCILIATION_FAILED,
                    reason=f"{type(exc).__name__}: {exc}",
                )
        except BaseException:
            pass

    def _recover_attempt(self, observed: RepositoryEffectAttempt) -> None:
        note = (
            "Application ended after externally visible effects could have started "
            "but before a canonical operation report was committed"
        )
        try:
            disposition = self._settle_startup_interruption(observed.operation_id, note)
            if disposition is RepositoryEffectInterruptionDisposition.OUTCOME_UNKNOWN:
                self.reconcile(observed.operation_id)
        except OperationNotFound:
            # Another recovery writer may have completed the same idempotent cleanup.
            return

    def _settle_startup_interruption(
        self,
        operation_id: str,
        note: str,
    ) -> RepositoryEffectInterruptionDisposition:
        expected: RepositoryEffectInterruptionEvidence | None = None
        try:
            with self._database.transaction(write=True) as connection:
                self._execution.assert_idle(connection)
                expected = self._settlement.settle_interruption_evidence_in(
                    connection,
                    operation_id,
                    unknown_note=note,
                )
        except BaseException:
            if expected is None or not self._settlement.interruption_evidence_matches(expected):
                raise
            return expected.disposition
        return expected.disposition

    def reconcile(
        self, operation_id: str, *, expected_record_version: int | None = None
    ) -> RepositoryEffectReconciliationEvidence:
        # M200: bind the idle lease check, native observation and settlement to one
        # original SQLite write authority. A prior read-cut followed by a separate
        # settlement write-cut left a gap where a new direct owner could acquire
        # a lease after fingerprinting but before the reconciliation was durable.
        # BEGIN IMMEDIATE fences concurrent admission before repository observation;
        # on observation failure the same transaction rolls back without mutation.
        with self._database.transaction(write=True) as connection:
            view = self._journal.read_view_from_connection(connection)
            try:
                current = self._attempts.get_in(connection, operation_id)
            except OperationNotFound:
                current = None
            if current is None:
                existing = _reconciliation_replay(view, operation_id)
                if existing is not None and not existing.reconciliation.requires_operator:
                    return existing
                raise OperationNotFound(operation_id)
            if (
                expected_record_version is not None
                and current.record_version != expected_record_version
            ):
                raise StateConflict(f"Effect recovery command is stale: {operation_id}")
            if current.status in OPERATOR_REQUIRED_EFFECT_STATUSES:
                return _operator_reconciliation_evidence(current, view)
            if current.status is not EffectAttemptStatus.OUTCOME_UNKNOWN:
                raise StateConflict(f"Effect attempt is not reconcilable: {operation_id}")
            self._execution.assert_idle(connection)
            result = self._reconciler.reconcile(
                current.repository.path,
                current.operation_id,
                safety=current.safety,
                baseline=current.baseline,
            )
            evidence = RepositoryEffectReconciliationEvidence(
                effect=current.identity,
                reconciliation=result,
            )
            self._settlement.apply_reconciliation_in(connection, current, evidence)
            return evidence

    def acknowledge(
        self,
        operation_id: str,
        *,
        note: str,
        expected_record_version: int | None = None,
    ) -> RepositoryEffectAcknowledgementEvidence:
        note = note.strip()
        if not note:
            raise StateConflict("Recovery acknowledgement note must not be empty")
        current, view = _recovery_read_cut(
            self._database, self._attempts, self._journal, operation_id
        )
        if current is None:
            existing = _acknowledgement_replay(view, operation_id, note)
            if existing is None:
                raise OperationNotFound(operation_id)
            return existing
        if (
            expected_record_version is not None
            and current.record_version != expected_record_version
        ):
            raise StateConflict(f"Effect acknowledgement command is stale: {operation_id}")
        reconciliation = _operator_reconciliation_evidence(current, view).reconciliation
        evidence = RepositoryEffectAcknowledgementEvidence(
            effect=current.identity,
            reconciliation=reconciliation,
            note=note,
            acknowledged_at=utc_now(),
        )
        try:
            with self._database.transaction(write=True) as connection:
                self._execution.assert_idle(connection)
                self._settlement.acknowledge_in(connection, current, evidence)
        except (OperationNotFound, StateConflict):
            existing = _acknowledgement_replay(self._journal.read_view(), operation_id, note)
            if existing is None:
                raise
            return existing
        return evidence

    def _terminalize_interrupted_fleet(self) -> str | None:
        if self._journal.active_run_detail() is None:
            return None
        with self._database.transaction(write=True) as connection:
            detail = self._journal.read_view_from_connection(connection).active_run_detail()
            if detail is None:
                return None
            run = detail.run
            # B7: a dead original Fleet lease may hold signed LACE resource
            # intents. Do NOT let assert_idle() reclaim it before the original
            # issuer can transfer cleanup-only custody. One SQLite writer cut.
            require_no_original_lace_intent_before_reclamation_in(
                connection,
                run.fleet_run_id,
            )
            self._execution.assert_idle(connection)
            unresolved = self._attempts.by_fleet_run_in(connection, run.fleet_run_id)
            uncertain = [
                attempt
                for attempt in unresolved
                if attempt.effect_boundary is EffectBoundaryKind.EXTERNAL
                and attempt.status in EXTERNAL_OUTCOME_UNCERTAIN_STATUSES
            ]
            operation_ids = tuple(sorted(attempt.operation_id for attempt in unresolved))
            self._journal.record_reconciliation_lifecycle_in(
                connection,
                run.fleet_run_id,
                FleetRunEventType.RECONCILIATION_REQUIRED,
                operation_ids=operation_ids,
                reason="Controller restart found a non-terminal Fleet run without a live execution owner",
            )
            self._journal.record_reconciliation_lifecycle_in(
                connection, run.fleet_run_id, FleetRunEventType.RECONCILIATION_STARTED
            )
            self._record_interrupted_process_uncertainty(connection, detail)
            self._terminalize_recovered_run(connection, run, uncertain)
            FleetExecutorCapacityReservations.release_run_in(connection, run.fleet_run_id)
            return run.fleet_run_id

    def _record_interrupted_process_uncertainty(self, connection, detail) -> None:
        settled = {
            later.physical_execution_id
            for later in detail.lifecycle_events
            if isinstance(later, (DescendantsQuiescentEvent, ProcessStateUnknownEvent))
        }
        for event in detail.lifecycle_events:
            if type(event) is not ProcessStartedEvent or event.physical_execution_id in settled:
                continue
            common = dict(
                fleet_run_id=detail.run.fleet_run_id,
                repository_id=event.repository_id,
                operation_id=event.operation_id,
                step_id=event.step_id,
                step_order=event.step_order,
                attempt_number=event.attempt_number,
                physical_execution_id=event.physical_execution_id,
            )
            self._journal.record_execution_lifecycle_in(
                connection,
                event_type=FleetRunEventType.PROCESS_STATE_UNKNOWN,
                pid=event.pid,
                process_identity=event.process_identity,
                **common,
            )
            self._journal.record_execution_lifecycle_in(
                connection, event_type=FleetRunEventType.OUTPUT_QUARANTINED, **common
            )

    def _terminalize_recovered_run(self, connection, run, uncertain) -> None:
        if run.status is FleetRunStatus.CANCELLING:
            self._journal.terminalize_run_in(
                connection, run.fleet_run_id, FleetRunTerminalIntent.CANCEL
            )
            return
        if uncertain:
            self._journal.terminalize_run_in(
                connection,
                run.fleet_run_id,
                FleetRunTerminalIntent.FAIL,
                FleetRunFailure.uncertain_repository_effects(len(uncertain)),
            )
            return
        if run.progress.completed == run.progress.total:
            self._journal.terminalize_run_in(
                connection, run.fleet_run_id, FleetRunTerminalIntent.COMPLETE
            )
            return
        failure = (
            FleetRunFailure.partial_recovery(
                completed=run.progress.completed, total=run.progress.total
            )
            if run.progress.completed > 0
            else FleetRunFailure.recovery(
                "Application restarted before any repository operation durably completed"
            )
        )
        self._journal.terminalize_run_in(
            connection, run.fleet_run_id, FleetRunTerminalIntent.FAIL, failure
        )


def _recovery_read_cut(
    database: LocalDatabase,
    attempts: RepositoryEffectAttemptStore,
    journal: Journal,
    operation_id: str,
) -> tuple[RepositoryEffectAttempt | None, JournalReadView]:
    with database.transaction() as connection:
        view = journal.read_view_from_connection(connection)
        try:
            current = attempts.get_in(connection, operation_id)
        except OperationNotFound:
            current = None
    return current, view


def _reconciliation_replay(
    view: JournalReadView,
    operation_id: str,
) -> RepositoryEffectReconciliationEvidence | None:
    if view.effect_acknowledgement(operation_id) is not None:
        raise StateConflict(f"Effect attempt was already acknowledged: {operation_id}")
    return view.latest_effect_reconciliation(operation_id)


def _acknowledgement_replay(
    view: JournalReadView,
    operation_id: str,
    note: str,
) -> RepositoryEffectAcknowledgementEvidence | None:
    existing = view.effect_acknowledgement(operation_id)
    if existing is not None and existing.note != note:
        raise StateConflict(
            f"Effect attempt {operation_id} was already acknowledged with a different note"
        )
    return existing


def _operator_reconciliation_evidence(
    attempt: RepositoryEffectAttempt,
    view: JournalReadView,
) -> RepositoryEffectReconciliationEvidence:
    """Replay the exact classification that put one attempt into operator quarantine."""
    reconciliation = effect_attempt_reconciliation(attempt)
    existing = view.latest_effect_reconciliation(attempt.operation_id)
    if (
        reconciliation is None
        or existing is None
        or existing.effect != attempt.identity
        or existing.reconciliation != reconciliation
    ):
        raise StateConflict(
            f"Effect attempt {attempt.operation_id} has operator-required state without matching Journal evidence"
        )
    return existing
