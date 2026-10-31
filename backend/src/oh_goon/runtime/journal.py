from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from pydantic import Field

from oh_goon.core.diagnostic_redaction import redact_diagnostic_text
from oh_goon.core.errors import InvalidRunTransition, JournalConflict
from oh_goon.core.models.agent_repair import (
    MAX_AGENT_CAMPAIGN_MESSAGE_CHARS,
    TRUNCATED_AGENT_CAMPAIGN_MESSAGE,
    AgentCampaignDecision,
    AgentCampaignIntent,
    AgentCampaignPhase,
    AgentCampaignReadback,
    AgentCampaignRunEvidence,
    AgentRepairResult,
    AgentRepairStopReason,
)
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet import OperationState
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_evidence import PlanStepCancellationEvidence
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport
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
    DirectFleetExecutionAdmission,
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
    RunResourcesObservedEvent,
    RunStartedEvent,
    StorageMaintenanceRecordedEvent,
)
from oh_goon.core.models.run_resources import (
    RunResourceEnvelopeEvidence,
    RunResourceEnvelopeObservation,
)
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceReceipt
from oh_goon.core.models.target import FleetPlanTarget, OperationTarget
from oh_goon.core.run_projection import project_fleet_run_detail
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunOutcome,
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.runtime.database import ExistingDatabaseAppendContext, LocalDatabase
from oh_goon.runtime.journal_admission_validation import (
    _require_valid_readmission,
    _require_valid_rerun,
)
from oh_goon.runtime.journal_execution_lifecycle import (
    LifecycleRootFields,
    ReconciliationRootFields,
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

if TYPE_CHECKING:
    from oh_goon.integrations.fleet_plan_step_execution import StepLifecycleObserver


class RunResourceObservationContext(ImmutableModel):
    """Compact terminal child cut; never a substitute for original Run admission."""

    schema_version: Literal["run-resource-observation/v1"] = "run-resource-observation/v1"
    database: ExistingDatabaseAppendContext
    expected: RunResourceEnvelopeEvidence
    journal_revision: int = Field(ge=1)
    journal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    last_run_sequence: int = Field(ge=2)
    terminal_status: Literal[
        FleetRunStatus.COMPLETED, FleetRunStatus.CANCELLED, FleetRunStatus.FAILED
    ]


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
        targets=tuple(targets),
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

    def begin_agent_campaign(self, intent: AgentCampaignIntent) -> AgentCampaignReadback:
        """Retain original intent; this does not admit any child or resume work."""
        with self._database.transaction(write=True) as connection:
            current = self._facts.read_view(connection).campaign(intent.campaign_id)
            if current.records:
                if current.intent == intent:
                    return current
                raise JournalConflict("Campaign identity is already bound to another intent")
            self._facts.append_campaign(connection, intent)
            return self._facts.read_view(connection).campaign(intent.campaign_id)

    def agent_campaign(self, campaign_id: str) -> AgentCampaignReadback:
        return self.read_view().campaign(campaign_id)

    def agent_campaign_decision_inputs(
        self, campaign_id: str
    ) -> tuple[AgentCampaignReadback, tuple[AgentCampaignRunEvidence, ...], tuple[str, ...]]:
        """Supply bounded references from one verified cut; never run campaign policy."""
        view = self.read_view()
        current = view.campaign(campaign_id)
        settled = []
        unresolved = []
        for phase in current.phases:
            detail = view.required_run_detail(phase.fleet_run_id)
            if not detail.run.status.terminal:
                unresolved.append(phase.fleet_run_id)
                continue
            settled.append(
                AgentCampaignRunEvidence(
                    fleet_run_id=phase.fleet_run_id,
                    terminal_sequence=view.run_events(phase.fleet_run_id).events[-1].sequence,
                    operation_ids=tuple(item.provenance.operation_id for item in detail.operations),
                )
            )
        return current, tuple(settled), tuple(unresolved)

    def agent_campaign_result(self, campaign_id: str) -> AgentRepairResult:
        """Return the committed decision at one verified cut without running policy."""
        view = self.read_view()
        current = view.campaign(campaign_id)
        decision = current.terminal_decision
        intent = current.intent
        if decision is None or intent is None:
            raise JournalConflict("Campaign controller decision is missing or incomplete")
        assert decision.stop_reason is not None
        details = tuple(
            view.required_run_detail(item.fleet_run_id) for item in decision.consumed_runs
        )
        return AgentRepairResult(
            campaign_id,
            intent.budget,
            decision.stop_reason,
            details,
            decision.message,
            tuple(phase.fleet_run_id for phase in current.phases),
            decision.unresolved_run_ids,
            decision.decision_id,
            current.journal_revision,
            current,
        )

    def commit_agent_campaign_decision(
        self, decision: AgentCampaignDecision
    ) -> AgentCampaignReadback:
        with self._database.transaction(write=True) as connection:
            return self.commit_agent_campaign_decision_in(connection, decision)

    def commit_agent_campaign_decision_in(
        self, connection: sqlite3.Connection, decision: AgentCampaignDecision
    ) -> AgentCampaignReadback:
        """CAS one policy output against original settled child evidence."""
        if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
            raise JournalConflict("Campaign publication requires an existing SQLite transaction")
        message = redact_diagnostic_text(decision.message)
        if len(message) > MAX_AGENT_CAMPAIGN_MESSAGE_CHARS:
            message = TRUNCATED_AGENT_CAMPAIGN_MESSAGE
        decision = AgentCampaignDecision.model_validate(
            {**decision.model_dump(), "message": message}
        )
        view = self._facts.read_view(connection)
        current = view.campaign(decision.campaign_id)
        for record in current.records:
            fact = record.fact
            if isinstance(fact, AgentCampaignDecision) and fact.decision_id == decision.decision_id:
                if fact == decision:
                    return current
                raise JournalConflict("Campaign decision identity has conflicting content")
        self._require_campaign_writer(current, decision.expected_revision)
        intent = current.intent
        assert intent is not None
        phases = current.phases
        if decision.phase_ordinal != len(
            phases
        ) or decision.remaining_runs != intent.maximum_runs - len(phases):
            raise JournalConflict("Campaign decision changed exact phase membership or budget")
        expected_evidence = []
        unresolved = []
        stopped = False
        for phase in phases:
            detail = view.required_run_detail(phase.fleet_run_id)
            events = view.run_events(phase.fleet_run_id).events
            stopped = stopped or any(
                event.event_type is FleetRunEventType.RUN_CANCEL_REQUESTED for event in events
            )
            if not detail.run.status.terminal:
                unresolved.append(phase.fleet_run_id)
                continue
            expected_evidence.append(
                (
                    phase.fleet_run_id,
                    events[-1].sequence,
                    tuple(operation.provenance.operation_id for operation in detail.operations),
                )
            )
        if decision.unstarted_selection is not None:
            selected = decision.unstarted_selection
            previous = current.records[-1].fact
            expected_selection = (
                (intent.initial_phase, intent.initial_step_ids)
                if isinstance(previous, AgentCampaignIntent)
                else (previous.next_phase, previous.next_step_ids)
                if isinstance(previous, AgentCampaignDecision) and previous.stop_reason is None
                else None
            )
            if expected_selection != (selected.phase, selected.selected_step_ids):
                raise JournalConflict(
                    "Unstarted selection differs from the original selected phase/Step membership"
                )
            if (
                view.run_detail(selected.selected_run_id) is not None
                or selected.phase_ordinal != len(phases) + 1
                or selected.phase_ordinal > intent.maximum_runs
            ):
                raise JournalConflict(
                    "Unstarted selection conflicts with an admitted identity or phase budget"
                )
        supplied_evidence = [
            (item.fleet_run_id, item.terminal_sequence, item.operation_ids)
            for item in decision.consumed_runs
        ]
        if supplied_evidence != expected_evidence or decision.unresolved_run_ids != tuple(
            unresolved
        ):
            raise JournalConflict(
                "Campaign decision does not reference its exact settled child results"
            )
        success = decision.stop_reason in {
            AgentRepairStopReason.CONVERGED,
            AgentRepairStopReason.TEST_PASSED,
        }
        if success and decision.unstarted_selection is not None:
            raise JournalConflict(
                "Campaign convergence cannot contain selected work that never started"
            )
        if decision.stop_reason is None or success:
            if unresolved or stopped:
                raise JournalConflict(
                    "Campaign advancement/convergence conflicts with accepted stop or unresolved work"
                )
            if not phases:
                raise JournalConflict(
                    "Campaign advancement/convergence requires settled child evidence"
                )
        if success:
            latest = phases[-1].context
            if (
                decision.candidate_identity != latest.candidate_identity
                or decision.scope_identity != latest.scope_identity
            ):
                raise JournalConflict(
                    "Campaign terminal candidate/scope differs from the exact final settled phase"
                )
            if (
                decision.stop_reason is AgentRepairStopReason.CONVERGED
                and latest.phase != "verification"
            ):
                raise JournalConflict(
                    "Campaign convergence requires its final settled verification phase"
                )
        if decision.stop_reason is AgentRepairStopReason.TEST_PASSED:
            detail = view.required_run_detail(phases[-1].fleet_run_id)
            target = detail.run.target
            evidence = detail.operations[0].evidence if len(detail.operations) == 1 else None
            result = evidence.plan if isinstance(evidence, FleetPlanOperationEvidence) else None
            selected_command = (
                next(
                    (
                        step
                        for step in target.plan.steps
                        if step.step_id == decision.passed_test_step_id
                    ),
                    None,
                )
                if isinstance(target, FleetPlanTarget)
                else None
            )
            passed = (
                next(
                    (step for step in result.steps if step.step_id == decision.passed_test_step_id),
                    None,
                )
                if result is not None
                else None
            )
            if (
                not isinstance(selected_command, ResolvedCommandStep)
                or not isinstance(target, FleetPlanTarget)
                or target.agent_repair is None
                or target.agent_repair.phase is not None
                or result is None
                or result.plan_id != target.plan.plan_id
                or tuple((step.step_id, step.kind) for step in result.steps)
                != tuple((step.step_id, step.kind) for step in target.plan.steps)
                or passed is None
                or passed.status != "COMPLETED"
                or passed.settlement.outcome != "SUCCESS"
                or passed.settlement.cleanup != "VERIFIED"
                or passed.settlement.reason != "ACCEPTED_EXIT"
                or not passed.attempts
                or any(
                    step.status != "SKIPPED" and step.settlement.cleanup != "VERIFIED"
                    for step in result.steps
                )
                or any(
                    operation.resource_settlement == "UNRESOLVED"
                    for phase in phases
                    for operation in view.required_run_detail(phase.fleet_run_id).operations
                )
            ):
                raise JournalConflict(
                    "Test-passed decision lacks its exact accepted native test or settled cleanup"
                )
        if decision.stop_reason is None:
            if (
                not isinstance(current.records[-1].fact, AgentCampaignPhase)
                or not decision.remaining_runs
            ):
                raise JournalConflict(
                    "Campaign cannot publish a second successor or exceed its budget"
                )
        if decision.stop_reason is AgentRepairStopReason.CONVERGED and any(
            view.required_run_detail(phase.fleet_run_id).outcome is not FleetRunOutcome.SUCCESS
            for phase in phases
        ):
            raise JournalConflict("Campaign convergence conflicts with unsuccessful child results")
        if decision.stop_reason is AgentRepairStopReason.CONVERGED:
            for phase in phases:
                detail = view.required_run_detail(phase.fleet_run_id)
                target = detail.run.target
                evidence = detail.operations[0].evidence if len(detail.operations) == 1 else None
                result = evidence.plan if isinstance(evidence, FleetPlanOperationEvidence) else None
                if (
                    not isinstance(target, FleetPlanTarget)
                    or result is None
                    or result.plan_id != target.plan.plan_id
                    or tuple((step.step_id, step.kind) for step in result.steps)
                    != tuple((step.step_id, step.kind) for step in target.plan.steps)
                    or any(
                        step.status != "COMPLETED"
                        or step.settlement.outcome != "SUCCESS"
                        or step.settlement.cleanup != "VERIFIED"
                        for step in result.steps
                    )
                    or any(
                        operation.resource_settlement == "UNRESOLVED"
                        for operation in detail.operations
                    )
                ):
                    raise JournalConflict(
                        "Campaign convergence lacks exact accepted settled Step evidence"
                    )
        self._facts.append_campaign(connection, decision)
        return self._facts.read_view(connection).campaign(decision.campaign_id)

    def _require_campaign_writer(
        self, current: AgentCampaignReadback, expected_revision: int
    ) -> None:
        if current.intent is None or current.latest_revision != expected_revision:
            raise JournalConflict("Campaign predecessor revision changed or intent is missing")
        if current.terminal_decision is not None:
            raise JournalConflict("Campaign already has a committed terminal decision")
        if current.records[0].session_id != self._database.session_id:
            raise JournalConflict("Campaign observation cannot resume a prior controller session")

    def _bind_agent_campaign_phase_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        target: FleetPlanTarget,
        targets: list[RepositoryIdentity],
    ) -> None:
        context = target.agent_repair
        assert context is not None
        if context.campaign_revision is None:
            raise JournalConflict(
                "Campaign phase admission requires its committed predecessor revision"
            )
        current = self._facts.read_view(connection).campaign(context.repair_id)
        self._require_campaign_writer(current, context.campaign_revision)
        intent = current.intent
        assert intent is not None
        phases = current.phases
        if (
            context.attempt != len(phases) + 1
            or context.max_attempts != intent.maximum_runs
            or len(targets) != 1
            or targets[0].repository_id != intent.repository_id
            or context.limits.deadline_at != intent.deadline_at
            or context.limits.step_timeout_seconds != intent.budget.step_timeout_seconds
            or context.previous_run_id != (phases[-1].fleet_run_id if phases else None)
            or (not phases and fleet_run_id != context.repair_id)
            or context.write_roots != intent.write_roots
            or (
                context.phase is not None
                and context.original_execution_digest != intent.original_execution_digest
            )
        ):
            raise JournalConflict(
                "Campaign admission changed its original authority, budget or lineage"
            )
        previous = current.records[-1].fact
        if phases:
            if (
                not isinstance(previous, AgentCampaignDecision)
                or previous.stop_reason is not None
                or previous.next_phase != (context.phase or "repair")
                or previous.next_step_ids != tuple(step.step_id for step in target.plan.steps)
                or previous.candidate_identity != context.candidate_identity
                or previous.scope_identity != context.scope_identity
            ):
                raise JournalConflict(
                    "Campaign phase lacks its exact committed advancement decision"
                )
        elif not isinstance(previous, AgentCampaignIntent) or (
            intent.initial_phase != (context.phase or "repair")
            or intent.initial_step_ids != tuple(step.step_id for step in target.plan.steps)
            or context.candidate_identity != intent.candidate_identity
            or context.scope_identity != intent.scope_identity
        ):
            raise JournalConflict(
                "Initial campaign admission differs from original selected candidate/scope"
            )
        self._facts.append_campaign(
            connection,
            AgentCampaignPhase(
                campaign_id=context.repair_id,
                expected_revision=current.latest_revision,
                fleet_run_id=fleet_run_id,
                context=context,
                execution_digest=target.execution_digest,
                selected_step_ids=tuple(step.step_id for step in target.plan.steps),
            ),
        )

    def commit_operation_in(
        self,
        connection: sqlite3.Connection,
        report: OperationReport,
    ) -> None:
        """Commit canonical operation evidence inside a caller-owned write transaction.

        Execution owners use this form so ownership proof and Journal mutation share one
        BEGIN IMMEDIATE boundary.  The Journal still owns validation and fact shape.
        """
        canonical = OperationReport.model_validate(report.model_dump(exclude_computed_fields=True))
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
    ) -> StepLifecycleObserver:
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
            cancellation: PlanStepCancellationEvidence | None = None,
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
                cancellation=cancellation,
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
        cancellation: PlanStepCancellationEvidence | None = None,
    ) -> None:
        """Append one live lifecycle fact inside a caller-owned write transaction."""
        view = self._facts.read_view(connection)
        events = view.run_events(fleet_run_id).events
        common: LifecycleRootFields = dict(
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
            cancellation,
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
        cancellation: PlanStepCancellationEvidence | None = None,
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
                cancellation=cancellation,
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

    def capture_run_resource_observation_context(
        self, expected: RunResourceEnvelopeEvidence
    ) -> RunResourceObservationContext:
        """Capture the final verified child cut after its last runtime write."""
        from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore

        with self._database.transaction() as connection:
            view = self._facts.read_view(connection)
            detail = view.required_run_detail(expected.fleet_run_id)
            if (
                not isinstance(detail.execution, DirectFleetExecutionAdmission)
                or detail.execution.resource_envelope != expected
            ):
                raise InvalidRunTransition(
                    "Native resource observation has no matching original admission"
                )
            if not detail.run.status.terminal:
                raise InvalidRunTransition("Run resource context requires a terminal original Run")
            if detail.resource_observation is not None:
                raise InvalidRunTransition("Run resource observation is already recorded")
            if RepositoryEffectAttemptStore(self._database).by_fleet_run_in(
                connection, expected.fleet_run_id
            ) or any(report.resource_settlement == "UNRESOLVED" for report in detail.operations):
                raise InvalidRunTransition("Run resource context requires settled original effects")
            events = view.run_events(expected.fleet_run_id).events
            revision, fact_hash = self._facts.current_head(connection)
            if revision != view.revision:
                raise JournalConflict("Run resource context differs from its verified Journal cut")
            return RunResourceObservationContext(
                database=self._database.capture_append_context(connection),
                expected=expected,
                journal_revision=revision,
                journal_hash=fact_hash,
                last_run_sequence=events[-1].sequence,
                terminal_status=cast(
                    Literal[
                        FleetRunStatus.COMPLETED, FleetRunStatus.CANCELLED, FleetRunStatus.FAILED
                    ],
                    detail.run.status,
                ),
            )

    @staticmethod
    def record_run_resource_observation_from_context(
        context: RunResourceObservationContext,
        observation: RunResourceEnvelopeObservation,
    ) -> None:
        """Append one final observation without runtime startup or global history reads."""
        expected = context.expected
        if (
            observation.fleet_run_id != expected.fleet_run_id
            or observation.unit_name != expected.unit_name
            or observation.invocation_id != expected.invocation_id
        ):
            raise InvalidRunTransition(
                "Native resource observation differs from original admission"
            )
        candidate = RunResourcesObservedEvent(
            event_type=FleetRunEventType.RUN_RESOURCES_OBSERVED,
            sequence=context.last_run_sequence + 1,
            fleet_run_id=expected.fleet_run_id,
            observation=observation,
        )
        with LocalDatabase.existing_append_transaction(context.database) as connection:
            JournalFactStore.append_run_event_at_head(
                connection,
                candidate,
                session_id=context.database.session_id,
                expected_revision=context.journal_revision,
                expected_hash=context.journal_hash,
            )

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
            assert isinstance(intent.target, FleetPlanTarget)
            ScheduleExecutionStore(self._database).bind_admitted_child_in(
                connection,
                schedule_run_id=intent.schedule_run_id,
                repository_id=repository_scope.repository_ids[0],
                fleet_run_id=fleet_run_id,
                capability=intent.capability,
                target=intent.target,
            )
        if isinstance(intent.target, FleetPlanTarget) and intent.target.agent_repair is not None:
            self._bind_agent_campaign_phase_in(connection, fleet_run_id, intent.target, targets)
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
        common: ReconciliationRootFields = dict(sequence=len(events) + 1, fleet_run_id=fleet_run_id)
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
