from __future__ import annotations

import sqlite3
from pathlib import Path

from oh_goon.core.errors import InvalidRunTransition, JournalConflict
from oh_goon.core.models.fleet import OperationState
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.reconciliation import (
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    FleetRunDetail,
    FleetRunFailure,
    FleetRunState,
)
from oh_goon.core.models.run_admission import (
    ExplicitRepositoryScope,
    FleetRepositoryScope,
    FleetRunAdmissionIntent,
)
from oh_goon.core.models.run_events import (
    FleetRunEvent,
    FleetRunEventBatch,
    RepositoryCompletedEvent,
    RunAdmittedEvent,
    RunCancelledEvent,
    RunCancelRequestedEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    StorageMaintenanceRecordedEvent,
)
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceReceipt
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.run_projection import project_fleet_run_detail
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal_admission_validation import (
    _require_valid_readmission,
    _require_valid_rerun,
)
from oh_goon.runtime.journal_execution_lifecycle import (
    _execution_lifecycle_event,
    _execution_lifecycle_identity,
    _execution_lifecycle_payload,
    reconciliation_lifecycle_event,
)
from oh_goon.runtime.journal_facts import (
    FleetOperationCommitted,
    OperationCommitted,
    RunEventCommitted,
)
from oh_goon.runtime.journal_store import JournalFactStore
from oh_goon.runtime.journal_view import JournalReadView


def _admitted_event(
    fleet_run_id: str,
    intent: FleetRunAdmissionIntent,
    repository_scope: FleetRepositoryScope,
    targets: list[RepositoryIdentity],
) -> RunAdmittedEvent:
    return RunAdmittedEvent(
        event_type=FleetRunEventType.RUN_ADMITTED,
        sequence=1,
        fleet_run_id=fleet_run_id,
        source=intent.source,
        target=intent.target,
        capability=intent.capability,
        repository_scope=repository_scope,
        targets=targets,
        execution=intent.execution,
        repository_admissions=intent.repository_admissions,
        application_launch=intent.application_launch,
        scheduling=intent.scheduling,
    )


def _terminal_event(
    state: FleetRunState,
    fleet_run_id: str,
    sequence: int,
    intent: FleetRunTerminalIntent,
    failure: FleetRunFailure | None,
) -> FleetRunEvent:
    if state.status is FleetRunStatus.CANCELLING or intent is FleetRunTerminalIntent.CANCEL:
        return RunCancelledEvent(
            event_type=FleetRunEventType.RUN_CANCELLED,
            sequence=sequence,
            fleet_run_id=fleet_run_id,
        )
    if intent is FleetRunTerminalIntent.COMPLETE:
        return RunCompletedEvent(
            event_type=FleetRunEventType.RUN_COMPLETED,
            sequence=sequence,
            fleet_run_id=fleet_run_id,
        )
    if failure is None:
        raise InvalidRunTransition("Failed terminalization requires a structured failure")
    return RunFailedEvent(
        event_type=FleetRunEventType.RUN_FAILED,
        sequence=sequence,
        fleet_run_id=fleet_run_id,
        failure=failure,
    )


class Journal:
    """Append-only SQLite writer for canonical operation and FleetRun facts."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database
        self._facts = JournalFactStore(database)

    def read_view(self) -> JournalReadView:
        with self._database.transaction() as connection:
            return self.read_view_from_connection(connection)

    def read_view_from_connection(self, connection: sqlite3.Connection) -> JournalReadView:
        return self._facts.read_view(connection)

    def commit_operation_in(
        self,
        connection: sqlite3.Connection,
        report: OperationReport,
    ) -> None:
        """Commit canonical operation evidence inside a caller-owned write transaction.

        Execution owners use this form so ownership proof and Journal mutation share one
        BEGIN IMMEDIATE boundary.  The Journal still owns validation and fact shape.
        """
        canonical = report.model_copy(deep=True)
        operation_id = canonical.provenance.operation_id
        view = self._facts.read_view(connection)
        existing = view.operation(operation_id)
        if existing is not None:
            if existing == canonical:
                return
            raise JournalConflict(
                f"Operation identity {operation_id} is already bound to another report"
            )
        fleet_run_id = canonical.provenance.fleet_run_id
        if fleet_run_id is None:
            self._facts.append_operation(connection, canonical)
            return
        run_events = view.run_events(fleet_run_id).events
        candidate = RepositoryCompletedEvent(
            event_type=FleetRunEventType.REPOSITORY_COMPLETED,
            sequence=len(run_events) + 1,
            fleet_run_id=fleet_run_id,
            operation_id=operation_id,
        )

        def resolver(candidate_id: str) -> OperationReport | None:
            if candidate_id == operation_id:
                return canonical
            return view.operation(candidate_id)

        project_fleet_run_detail([*run_events, candidate], resolver)
        self._facts.append_fleet_operation(connection, canonical, candidate)

    def execution_lifecycle_observer(
        self, fleet_run_id: str, repository_id: str, operation_id: str
    ):
        """Bind one runner callback to the exact durable run/repository attempt identity."""

        def observe(
            step_id: str,
            step_order: int,
            event_type: str,
            *,
            attempt_number: int | None = None,
            physical_execution_id: str | None = None,
            pid: int | None = None,
            process_identity: str | None = None,
            returncode: int | None = None,
            termination_reason: str | None = None,
            output_count: int | None = None,
        ) -> None:
            self.record_execution_lifecycle(
                fleet_run_id=fleet_run_id,
                repository_id=repository_id,
                operation_id=operation_id,
                event_type=FleetRunEventType(event_type),
                step_id=step_id,
                step_order=step_order,
                attempt_number=attempt_number,
                physical_execution_id=physical_execution_id,
                pid=pid,
                process_identity=process_identity,
                returncode=returncode,
                termination_reason=termination_reason,
                output_count=output_count,
            )

        return observe

    def record_execution_lifecycle_in(
        self,
        connection: sqlite3.Connection,
        *,
        fleet_run_id: str,
        repository_id: str,
        operation_id: str,
        event_type: FleetRunEventType,
        step_id: str | None = None,
        step_order: int | None = None,
        attempt_number: int | None = None,
        physical_execution_id: str | None = None,
        pid: int | None = None,
        process_identity: str | None = None,
        returncode: int | None = None,
        termination_reason: str | None = None,
        output_count: int | None = None,
    ) -> None:
        """Append one live lifecycle fact inside a caller-owned write transaction."""
        view = self._facts.read_view(connection)
        events = view.run_events(fleet_run_id).events
        common = dict(
            sequence=len(events) + 1,
            fleet_run_id=fleet_run_id,
            repository_id=repository_id,
            operation_id=operation_id,
        )
        candidate = _execution_lifecycle_event(
            event_type,
            common,
            step_id,
            step_order,
            attempt_number,
            physical_execution_id,
            pid,
            process_identity,
            returncode,
            termination_reason,
            output_count,
        )
        identity = _execution_lifecycle_identity(candidate)
        for existing in events:
            if _execution_lifecycle_identity(existing) != identity:
                continue
            if _execution_lifecycle_payload(existing) == _execution_lifecycle_payload(candidate):
                return
            raise JournalConflict(
                f"Conflicting lifecycle replay for {event_type.value}: {identity}"
            )
        project_fleet_run_detail([*events, candidate], view.operation)
        self._facts.append_run_event(connection, candidate)

    def record_execution_lifecycle(
        self,
        *,
        fleet_run_id: str,
        repository_id: str,
        operation_id: str,
        event_type: FleetRunEventType,
        step_id: str | None = None,
        step_order: int | None = None,
        attempt_number: int | None = None,
        physical_execution_id: str | None = None,
        pid: int | None = None,
        process_identity: str | None = None,
        returncode: int | None = None,
        termination_reason: str | None = None,
        output_count: int | None = None,
    ) -> None:
        """Append one live execution transition to the canonical Fleet journal."""
        with self._database.transaction(write=True) as connection:
            self.record_execution_lifecycle_in(
                connection,
                fleet_run_id=fleet_run_id,
                repository_id=repository_id,
                operation_id=operation_id,
                event_type=event_type,
                step_id=step_id,
                step_order=step_order,
                attempt_number=attempt_number,
                physical_execution_id=physical_execution_id,
                pid=pid,
                process_identity=process_identity,
                returncode=returncode,
                termination_reason=termination_reason,
                output_count=output_count,
            )

    def record_storage_maintenance(
        self,
        *,
        fleet_run_id: str,
        repository_id: str,
        operation_id: str,
        receipt: FleetContentMaintenanceReceipt,
    ) -> None:
        with self._database.transaction() as connection:
            view = self._facts.read_view(connection)
            events = view.run_events(fleet_run_id).events
            candidate = StorageMaintenanceRecordedEvent(
                event_type=FleetRunEventType.STORAGE_MAINTENANCE_RECORDED,
                sequence=len(events) + 1,
                fleet_run_id=fleet_run_id,
                repository_id=repository_id,
                operation_id=operation_id,
                receipt=receipt,
            )
            project_fleet_run_detail([*events, candidate], view.operation)
            self._facts.append_run_event(connection, candidate)

    def commit_effect_reconciliation_in(
        self,
        connection: sqlite3.Connection,
        evidence: RepositoryEffectReconciliationEvidence,
    ) -> None:
        """Append immutable recovery evidence inside the attempt CAS transaction."""
        view = self._facts.read_view(connection)
        if view.operation(evidence.effect.operation_id) is not None:
            raise JournalConflict(
                f"Cannot reconcile operation {evidence.effect.operation_id}: canonical report already exists"
            )
        self._facts.append_effect_reconciliation(connection, evidence)

    def commit_effect_acknowledgement_in(
        self,
        connection: sqlite3.Connection,
        evidence: RepositoryEffectAcknowledgementEvidence,
    ) -> None:
        """Append immutable operator acknowledgement in the quarantine-release transaction."""
        self._facts.append_effect_acknowledgement(connection, evidence)

    def run_details_changed_after(self, revision: int) -> tuple[int, tuple[FleetRunDetail, ...]]:
        """Return current details for Fleet runs touched after one Journal revision.

        This is a generic read-side observation boundary. Callers receive canonical current
        run details; execution never calls or waits for downstream observers.
        """
        if revision < 0:
            raise ValueError("Journal revision must be zero or greater")
        with self._database.transaction() as connection:
            current_revision = self._facts.current_revision(connection)
            if current_revision <= revision:
                return current_revision, ()
            changed = self._facts.load_entries_after(connection, revision)
            view = self.read_view_from_connection(connection)
            run_ids: set[str] = set()
            for entry in changed:
                if isinstance(entry, (RunEventCommitted, FleetOperationCommitted)):
                    run_ids.add(entry.event.fleet_run_id)
            details = tuple(
                detail
                for run_id in sorted(run_ids)
                if (detail := view.run_detail(run_id)) is not None
            )
            return view.revision, details

    def operation_reports_after(
        self, revision: int
    ) -> tuple[int, tuple[tuple[int, OperationReport], ...]]:
        """Return canonical operation reports after one revision for read projections."""
        if revision < 0:
            raise ValueError("Journal revision must be zero or greater")
        current, entries = self._facts.entries_after(revision)
        reports = tuple(
            (entry.revision, entry.report)
            for entry in entries
            if isinstance(entry, (OperationCommitted, FleetOperationCommitted))
        )
        return current, reports

    def operation(self, operation_id: str) -> OperationReport | None:
        return self.read_view().operation(operation_id)

    def require_operation(self, operation_id: str) -> OperationReport:
        return self.read_view().require_operation(operation_id)

    def current_capability(
        self,
        repository: Path | str,
        capability_id: str,
        target: OperationTarget | None = None,
    ) -> OperationState | None:
        return self.read_view().current_capability(repository, capability_id, target)

    def history(
        self,
        repository: Path | str,
        *,
        action: OperationAction | None = None,
    ) -> list[OperationReport]:
        return self.read_view().history(repository, action=action)

    def prepare_run_admission_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        intent: FleetRunAdmissionIntent,
        repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
    ) -> RunAdmittedEvent:
        """M166 immutable pre-commit capture using the *operational Journal* owner.

        This is an observation/projection, not a persisted Run or permission to
        execute. It must run under the caller's SQLite transaction. The issuer
        path currently requires a previously committed Run and CANNOT consume
        this capture for live pre-work evidence yet (M167+).
        """
        admitted, _ = self._prepare_run_admission_in(
            connection,
            fleet_run_id,
            intent,
            repository_scope,
            targets,
        )
        return admitted

    def _prepare_run_admission_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        intent: FleetRunAdmissionIntent,
        repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
    ) -> tuple[RunAdmittedEvent, FleetRunState]:
        if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
            raise JournalConflict(
                "pre-work Fleet admission requires an existing SQLite transaction"
            )
        view = self._facts.read_view(connection)
        active = view.active_run()
        if active is not None:
            raise JournalConflict(
                f"Fleet run {active.fleet_run_id} ({active.action.value}) is already "
                f"{active.status.value.lower()}"
            )
        if view.run_detail(fleet_run_id) is not None:
            raise JournalConflict(f"Fleet run already exists: {fleet_run_id}")
        _require_valid_readmission(view, fleet_run_id, intent, repository_scope, targets)
        _require_valid_rerun(view, fleet_run_id, intent, repository_scope, targets)
        admitted = _admitted_event(fleet_run_id, intent, repository_scope, targets)
        state = project_fleet_run_detail([admitted], view.operation).run
        return admitted, state

    def admit_run_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        intent: FleetRunAdmissionIntent,
        repository_scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
    ) -> FleetRunState:
        # The live legacy admission and the future pre-work projection MUST
        # share this validator/event constructor. Do not create a second owner.
        admitted, state = self._prepare_run_admission_in(
            connection,
            fleet_run_id,
            intent,
            repository_scope,
            targets,
        )
        if intent.source is FleetRunSource.SCHEDULED:
            from oh_goon.runtime.schedule_executions import ScheduleExecutionStore

            if (
                intent.schedule_run_id is None
                or len(targets) != 1
                or not isinstance(repository_scope, ExplicitRepositoryScope)
                or len(repository_scope.repository_ids) != 1
            ):
                raise JournalConflict("Scheduled admission requires one exact repository child")
            ScheduleExecutionStore(self._database).bind_admitted_child_in(
                connection,
                schedule_run_id=intent.schedule_run_id,
                repository_id=repository_scope.repository_ids[0],
                fleet_run_id=fleet_run_id,
                capability=intent.capability,
                target=intent.target,
            )
        self._facts.append_run_event(connection, admitted)
        return state

    def start_run_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
    ) -> FleetRunState:
        view = self._facts.read_view(connection)
        events = view.run_events(fleet_run_id).events
        candidate = RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=len(events) + 1,
            fleet_run_id=fleet_run_id,
        )
        state = project_fleet_run_detail([*events, candidate], view.operation).run
        self._facts.append_run_event(connection, candidate)
        return state

    def record_reconciliation_lifecycle_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        event_type: FleetRunEventType,
        *,
        operation_ids: tuple[str, ...] = (),
        reason: str | None = None,
    ) -> FleetRunState:
        """Persist controller-recovery lifecycle in the same canonical run journal."""
        view = self._facts.read_view(connection)
        events = list(view.run_events(fleet_run_id).events)
        common = dict(sequence=len(events) + 1, fleet_run_id=fleet_run_id)
        try:
            candidate = reconciliation_lifecycle_event(event_type, common, operation_ids, reason)
        except ValueError as exc:
            raise JournalConflict(str(exc)) from exc
        state = project_fleet_run_detail([*events, candidate], view.operation).run
        self._facts.append_run_event(connection, candidate)
        return state

    def request_cancel_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
    ) -> FleetRunState:
        view = self._facts.read_view(connection)
        events = view.run_events(fleet_run_id).events
        candidate = RunCancelRequestedEvent(
            event_type=FleetRunEventType.RUN_CANCEL_REQUESTED,
            sequence=len(events) + 1,
            fleet_run_id=fleet_run_id,
        )
        state = project_fleet_run_detail([*events, candidate], view.operation).run
        self._facts.append_run_event(connection, candidate)
        return state

    def terminalize_run_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        intent: FleetRunTerminalIntent,
        failure: FleetRunFailure | None = None,
    ) -> FleetRunState:
        view = self._facts.read_view(connection)
        batch = view.run_events(fleet_run_id)
        state = batch.run
        if state.status.terminal:
            return state
        events = list(batch.events)
        if (
            intent is FleetRunTerminalIntent.CANCEL
            and state.status is not FleetRunStatus.CANCELLING
        ):
            requested = RunCancelRequestedEvent(
                event_type=FleetRunEventType.RUN_CANCEL_REQUESTED,
                sequence=len(events) + 1,
                fleet_run_id=fleet_run_id,
            )
            state = project_fleet_run_detail([*events, requested], view.operation).run
            self._facts.append_run_event(connection, requested)
            events.append(requested)
        candidate = _terminal_event(state, fleet_run_id, len(events) + 1, intent, failure)
        terminal = project_fleet_run_detail([*events, candidate], view.operation).run
        self._facts.append_run_event(connection, candidate)
        return terminal

    def active_run(self) -> FleetRunState | None:
        return self.read_view().active_run()

    def active_run_detail(self) -> FleetRunDetail | None:
        return self.read_view().active_run_detail()

    def run_events(
        self, fleet_run_id: str, after: int = 0, *, limit: int | None = None
    ) -> FleetRunEventBatch:
        return self.read_view().run_events(fleet_run_id, after, limit=limit)
