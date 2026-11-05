from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from math import isfinite
from threading import TIMEOUT_MAX
from time import monotonic
from typing import TypedDict

from oh_goon.core.effect_attempts import (
    AUTOMATIC_RECOVERY_EFFECT_STATUSES,
    EXTERNAL_OUTCOME_UNCERTAIN_STATUSES,
    OPERATOR_REQUIRED_EFFECT_STATUSES,
)
from oh_goon.core.errors import ExecutionBusy, OperationNotFound, StateConflict
from oh_goon.core.models.effect_attempt import (
    RepositoryEffectAttempt,
    effect_attempt_reconciliation,
)
from oh_goon.core.models.reconciliation import (
    IndeterminateRepositoryReconciliation,
    PartiallyAppliedRepositoryReconciliation,
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.models.run import FleetRunDetail, FleetRunFailure, FleetRunState
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    OutputAbandonedEvent,
    OutputFinalizedEvent,
    OutputQuarantinedEvent,
    ProcessExitedEvent,
    ProcessStartedEvent,
    ProcessStartingEvent,
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
from oh_goon.runtime.process_group import (
    process_session_exists,
    wait_for_process_session_quiescence,
)
from oh_goon.services.reconciliation import RepositoryReconciler
from oh_goon.services.repository_effect_settlement import (
    RepositoryEffectInterruptionDisposition,
    RepositoryEffectInterruptionEvidence,
    RepositoryEffectSettlement,
)


class _InterruptedProcessFields(TypedDict):
    fleet_run_id: str
    repository_id: str
    operation_id: str
    step_id: str | None
    step_order: int | None
    attempt_number: int | None
    physical_execution_id: str


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

    def recover(
        self, *, fleet_run_id: str | None = None, quiescence_timeout_seconds: float = 0.0
    ) -> None:
        """Recover idle execution, optionally restricted to one owned worker exit.

        Ordinary startup retains its existing global reconciliation. Runtime
        shutdown supplies an exact Run and cannot sweep unrelated effects.
        """
        if (
            isinstance(quiescence_timeout_seconds, bool)
            or not isinstance(quiescence_timeout_seconds, (int, float))
            or not isfinite(quiescence_timeout_seconds)
            or not 0 <= quiescence_timeout_seconds <= TIMEOUT_MAX
            or (fleet_run_id is None and quiescence_timeout_seconds != 0)
        ):
            raise ValueError("Native quiescence wait requires a finite bounded worker-exit scope")
        if fleet_run_id is not None:
            if type(fleet_run_id) is not str or not fleet_run_id:
                raise StateConflict("Worker-exit recovery requires an exact Fleet Run identity")
            with self._database.transaction() as connection:
                detail = self._journal.read_view_from_connection(connection).required_run_detail(
                    fleet_run_id
                )
                pending = self._attempts.by_fleet_run_in(connection, fleet_run_id)
                terminal_scope = detail.run.status.terminal and not any(
                    attempt.status in AUTOMATIC_RECOVERY_EFFECT_STATUSES for attempt in pending
                )
            if quiescence_timeout_seconds:
                # Wait outside SQLite. This observation grants no recovery
                # authority: the original writer below revalidates both worker
                # death and native session absence before any durable mutation.
                deadline = monotonic() + quiescence_timeout_seconds
                for event in _unsettled_process_events(detail):
                    if type(
                        event
                    ) is ProcessStartedEvent and not wait_for_process_session_quiescence(
                        event.pid, timeout=max(0.0, deadline - monotonic())
                    ):
                        raise ExecutionBusy(
                            "Original command session has not established quiescence"
                        )
            if terminal_scope:
                # Another runtime may already own fresh work. A settled
                # terminal scope needs no global-idle mutation or sweep.
                _require_original_process_quiescence(detail, attempts=pending)
                return
        if self._execution.active_lease() is not None:
            if fleet_run_id is not None:
                raise ExecutionBusy("Live execution custody prevents worker-exit recovery")
            return
        recovering_run_id: str | None = None
        try:
            recovering_run_id = self._terminalize_interrupted_fleet(fleet_run_id=fleet_run_id)
            for attempt in self._attempts.recovery_candidates():
                if fleet_run_id is not None and attempt.fleet_run_id != fleet_run_id:
                    continue
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
            if fleet_run_id is not None:
                raise
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
            report = view.operation(operation_id)
            if report is not None and report.resource_settlement == "UNRESOLVED":
                raise StateConflict(
                    "Original process/resource cleanup is unresolved; filesystem reconciliation cannot prove quiescence"
                )
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
            if current.fleet_run_id is not None:
                _require_original_process_quiescence(
                    view.required_run_detail(current.fleet_run_id), attempts=(current,)
                )
            self._execution.assert_idle(connection)
            result = self._reconciler.reconcile(
                current.repository.path,
                current.operation_id,
                report=view.operation(current.operation_id),
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
        report = view.operation(operation_id)
        if report is not None and report.resource_settlement == "UNRESOLVED":
            raise StateConflict(
                "Original process/resource cleanup is unresolved; operator acknowledgement cannot prove quiescence"
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
        assert isinstance(
            reconciliation,
            (PartiallyAppliedRepositoryReconciliation, IndeterminateRepositoryReconciliation),
        )
        evidence = RepositoryEffectAcknowledgementEvidence(
            effect=current.identity,
            reconciliation=reconciliation,
            note=note,
            acknowledged_at=utc_now(),
        )
        try:
            with self._database.transaction(write=True) as connection:
                if current.fleet_run_id is not None:
                    _require_original_process_quiescence(
                        self._journal.read_view_from_connection(connection).required_run_detail(
                            current.fleet_run_id
                        ),
                        attempts=(current,),
                    )
                self._execution.assert_idle(connection)
                self._settlement.acknowledge_in(connection, current, evidence)
        except (OperationNotFound, StateConflict):
            existing = _acknowledgement_replay(self._journal.read_view(), operation_id, note)
            if existing is None:
                raise
            return existing
        return evidence

    def _terminalize_interrupted_fleet(self, *, fleet_run_id: str | None = None) -> str | None:
        if fleet_run_id is None and self._journal.active_run_detail() is None:
            return None
        with self._database.transaction(write=True) as connection:
            view = self._journal.read_view_from_connection(connection)
            detail = (
                view.required_run_detail(fleet_run_id)
                if fleet_run_id is not None
                else view.active_run_detail()
            )
            if detail is None:
                return None
            run = detail.run
            # B7: protected original custody must refuse the generic route
            # before any portable worker-recovery interpretation or reclaim.
            require_no_original_lace_intent_before_reclamation_in(connection, run.fleet_run_id)
            unresolved = self._attempts.by_fleet_run_in(connection, run.fleet_run_id)
            if fleet_run_id is not None:
                if detail.run.status.terminal:
                    _require_original_process_quiescence(detail, attempts=unresolved)
                    return None
                self._execution.assert_dead_fleet_worker_in(connection, fleet_run_id)
            else:
                retained = self._execution.retained_lease_in(connection)
                if (
                    retained is not None
                    and retained.custody is not None
                    and retained.custody.phase == "WORKER_OWNED"
                ):
                    self._execution.assert_dead_fleet_worker_in(connection, detail.run.fleet_run_id)
            quiescent_processes = _require_original_process_quiescence(detail, attempts=unresolved)
            self._execution.assert_idle(connection)
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
            self._record_interrupted_process_uncertainty(
                connection, detail, quiescent_processes=quiescent_processes
            )
            self._terminalize_recovered_run(connection, run, uncertain)
            FleetExecutorCapacityReservations.release_run_in(connection, run.fleet_run_id)
            return run.fleet_run_id

    def _record_interrupted_process_uncertainty(
        self,
        connection: sqlite3.Connection,
        detail: FleetRunDetail,
        *,
        quiescent_processes: frozenset[str],
    ) -> None:
        settled = {
            later.physical_execution_id
            for later in detail.lifecycle_events
            if isinstance(later, DescendantsQuiescentEvent)
        }
        uncertain = {
            later.physical_execution_id
            for later in detail.lifecycle_events
            if isinstance(later, ProcessStateUnknownEvent)
        }
        exited = {
            later.physical_execution_id
            for later in detail.lifecycle_events
            if isinstance(later, ProcessExitedEvent)
        }
        output_disposed = {
            later.physical_execution_id
            for later in detail.lifecycle_events
            if isinstance(
                later, (OutputFinalizedEvent, OutputAbandonedEvent, OutputQuarantinedEvent)
            )
        }
        for event in detail.lifecycle_events:
            if type(event) is not ProcessStartedEvent or event.physical_execution_id in settled:
                continue
            common: _InterruptedProcessFields = dict(
                fleet_run_id=detail.run.fleet_run_id,
                repository_id=event.repository_id,
                operation_id=event.operation_id,
                step_id=event.step_id,
                step_order=event.step_order,
                attempt_number=event.attempt_number,
                physical_execution_id=event.physical_execution_id,
            )
            result_unknown = (
                event.physical_execution_id not in exited
                or event.physical_execution_id in uncertain
            )
            if event.physical_execution_id not in uncertain and result_unknown:
                self._journal.record_execution_lifecycle_in(
                    connection,
                    event_type=FleetRunEventType.PROCESS_STATE_UNKNOWN,
                    pid=event.pid,
                    process_identity=event.process_identity,
                    **common,
                )
            if result_unknown and event.physical_execution_id not in output_disposed:
                self._journal.record_execution_lifecycle_in(
                    connection, event_type=FleetRunEventType.OUTPUT_QUARANTINED, **common
                )
            if event.physical_execution_id in quiescent_processes:
                # Resource absence cannot replace a known exit or fill a lost
                # process result. Preserve both the original result and output
                # disposition without inventing an exit code or finalization.
                self._journal.record_execution_lifecycle_in(
                    connection,
                    event_type=FleetRunEventType.DESCENDANTS_QUIESCENT,
                    pid=event.pid,
                    process_identity=event.process_identity,
                    **common,
                )

    def _terminalize_recovered_run(
        self,
        connection: sqlite3.Connection,
        run: FleetRunState,
        uncertain: Sequence[RepositoryEffectAttempt],
    ) -> None:
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
        if run.status is FleetRunStatus.RUNNING and run.progress.completed == run.progress.total:
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


def _unsettled_process_events(detail: FleetRunDetail) -> tuple[ProcessStartingEvent, ...]:
    settled_operations = {
        report.provenance.operation_id
        for report in detail.operations
        if report.resource_settlement == "SETTLED"
    }
    settled_processes = {
        event.physical_execution_id
        for event in detail.lifecycle_events
        if isinstance(event, DescendantsQuiescentEvent)
    }
    return tuple(
        event
        for event in detail.lifecycle_events
        if isinstance(event, ProcessStartingEvent)
        and event.operation_id not in settled_operations
        and event.physical_execution_id not in settled_processes
    )


def _require_original_process_quiescence(
    detail: FleetRunDetail, *, attempts: Sequence[RepositoryEffectAttempt] = ()
) -> frozenset[str]:
    """Reuse original result/native group authority; worker death is insufficient."""
    settled_operations = {
        report.provenance.operation_id
        for report in detail.operations
        if report.resource_settlement == "SETTLED"
    }
    settled_processes = {
        event.physical_execution_id
        for event in detail.lifecycle_events
        if isinstance(event, DescendantsQuiescentEvent)
    }
    quiescent_processes = set(settled_processes)
    started_processes = {
        event.physical_execution_id
        for event in detail.lifecycle_events
        if type(event) is ProcessStartedEvent
    }
    observed_operations = {
        event.operation_id
        for event in detail.lifecycle_events
        if isinstance(event, ProcessStartingEvent)
    }
    if any(
        attempt.effect_boundary is EffectBoundaryKind.EXTERNAL
        and attempt.status in EXTERNAL_OUTCOME_UNCERTAIN_STATUSES
        and attempt.operation_id not in settled_operations
        and attempt.operation_id not in observed_operations
        for attempt in attempts
    ):
        raise StateConflict(
            "Original native process evidence is unavailable for the unresolved Fleet effect"
        )
    for event in detail.lifecycle_events:
        if type(event) is ProcessStartedEvent and event.operation_id in settled_operations:
            quiescent_processes.add(event.physical_execution_id)
    for event in _unsettled_process_events(detail):
        if (
            type(event) is ProcessStartingEvent
            and event.physical_execution_id not in started_processes
        ):
            raise StateConflict(
                "Original Step process identity is unavailable for worker-exit recovery"
            )
        if type(event) is ProcessStartedEvent:
            if process_session_exists(event.pid) is not False:
                raise ExecutionBusy("Original command session has not established quiescence")
            quiescent_processes.add(event.physical_execution_id)
    return frozenset(quiescent_processes)


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
