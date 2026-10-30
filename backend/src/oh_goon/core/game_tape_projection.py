from __future__ import annotations

from typing import Literal, cast

from oh_goon.core.execution_ledger import project_execution_ledger
from oh_goon.core.game_tape_identity import repository_execution_id
from oh_goon.core.game_tape_lifecycle import repository_status, run_status
from oh_goon.core.game_tape_run_facts import (
    environment_fingerprint,
    plan_source_label,
    project_issue_focus,
    project_receipt,
    project_repository_failure_focus,
    requirement_facts,
    step_source_map,
)
from oh_goon.core.game_tape_step_projection import project_steps
from oh_goon.core.game_tape_storage import project_storage_maintenance
from oh_goon.core.game_tape_timing import repository_time_bounds
from oh_goon.core.models.fleet_plan_evidence import FleetPlanResult
from oh_goon.core.models.game_tape import (
    GameTape,
    GameTapeProducerImplementationProvenance,
    GameTapeRepositoryExecution,
    GameTapeRuntimeProgress,
    GameTapeShardAttemptLineage,
    GameTapeShardLineage,
    GameTapeVerificationExecutionBoundary,
    GameTapeWhyNotDiagnostic,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    ProcessExitedEvent,
    ProcessStartedEvent,
    ProcessStartingEvent,
    ProcessStateUnknownEvent,
    RepositoryStartedEvent,
    StepAttemptCreatedEvent,
    StepReachedEvent,
    StepStartedEvent,
    TerminationRequestedEvent,
)
from oh_goon.core.models.shard_execution import CanonicalShardResultEvidence
from oh_goon.core.models.target import FleetPlanTarget


def project_game_tape(detail: FleetRunDetail) -> GameTape:
    """Project one immutable Game Tape without introducing execution semantics."""

    target = detail.run.target
    plan = target.plan if isinstance(target, FleetPlanTarget) else None
    reports = {item.repository.repository_id: item for item in detail.operations}
    repositories = tuple(
        _repository_execution(
            detail,
            repository,
            reports.get(repository.repository_id),
            target if isinstance(target, FleetPlanTarget) else None,
            detail.producer_implementation_provenance,
        )
        for repository in detail.run.targets
    )
    repository_failures = project_repository_failure_focus(repositories)
    issue_count, issue_focus = project_issue_focus(
        detail,
        repository_failures,
    )
    return GameTape(
        journal_revision=detail.journal_revision,
        campaign_journal=detail.campaign_journal,
        terminal=detail.run.status.terminal,
        configuration=plan.configuration if plan is not None else None,
        fleet_run_id=detail.run.fleet_run_id,
        run_source=detail.run.source,
        execution=detail.execution,
        resource_observation=detail.resource_observation,
        application_launch=detail.application_launch,
        scheduling=detail.scheduling,
        status=run_status(detail.run),
        execution_reference=(
            target.execution_reference if isinstance(target, FleetPlanTarget) else None
        ),
        requirements=requirement_facts(plan.requirements if plan is not None else ()),
        receipt=project_receipt(detail),
        storage=project_storage_maintenance(detail.storage_maintenance),
        issue_count=issue_count,
        issue_focus=issue_focus,
        enforcer_receipt_available=(
            isinstance(target, FleetPlanTarget)
            and target.plan.execution.purpose == "cicd"
            and isinstance(detail.execution, StrictFleetExecutionAdmission)
        ),
        why_not=_why_not(detail),
        play_by_play=project_execution_ledger(detail),
        repositories=repositories,
    )


def _repository_execution(
    detail: FleetRunDetail,
    repository: RepositoryIdentity,
    report: OperationReport | None,
    target: FleetPlanTarget | None,
    producer_provenance: GameTapeProducerImplementationProvenance | None,
) -> GameTapeRepositoryExecution:
    execution_id = repository_execution_id(detail.run.fleet_run_id, repository.repository_id)
    plan = target.plan if target is not None else None
    planned_steps = plan.steps if plan is not None else ()
    if report is None:
        repository_start = next(
            (
                event.generated_at
                for event in detail.lifecycle_events
                if isinstance(event, RepositoryStartedEvent)
                and event.repository_id == repository.repository_id
            ),
            None,
        )
        repository_events = tuple(
            event
            for event in detail.lifecycle_events
            if getattr(event, "repository_id", None) == repository.repository_id
        )
        steps = project_steps(
            detail.run.fleet_run_id,
            repository.repository_id,
            planned_steps,
            (),
            step_sources=step_source_map(plan),
            source_label=plan_source_label(target) if target is not None else "resolved plan",
            terminal=detail.run.status.terminal,
            operation_reason_code=None,
            lifecycle_events=repository_events,
        )
        return GameTapeRepositoryExecution(
            repository_execution_id=execution_id,
            repository=repository,
            status=repository_status(
                None,
                terminal=detail.run.status.terminal,
                started=repository_start is not None,
                lifecycle_events=repository_events,
            ),
            started_at=repository_start,
            fingerprint=environment_fingerprint(target, None, None),
            steps=steps,
            runtime_progress=_runtime_progress(detail, repository.repository_id),
        )

    result = _plan_result(report)
    started, finished = repository_time_bounds(report, result)
    steps = project_steps(
        detail.run.fleet_run_id,
        repository.repository_id,
        planned_steps,
        result.steps if result is not None else (),
        step_sources=step_source_map(plan),
        source_label=plan_source_label(target) if target is not None else "resolved plan",
        terminal=detail.run.status.terminal,
        operation_reason_code=str(report.outcome.reason_code),
    )
    return GameTapeRepositoryExecution(
        repository_execution_id=execution_id,
        repository=repository,
        status=repository_status(report, terminal=detail.run.status.terminal),
        operation_id=report.provenance.operation_id,
        started_at=started,
        finished_at=finished,
        fingerprint=environment_fingerprint(
            target,
            report,
            result.execution_inputs if result is not None else None,
        ),
        steps=steps,
        runtime_progress=_runtime_progress(detail, repository.repository_id),
        shard_lineage=_shard_lineage(result),
        verification_execution=_verification_execution_boundary(result, producer_provenance),
    )


def _runtime_progress(detail: FleetRunDetail, repository_id: str) -> GameTapeRuntimeProgress | None:
    relevant = [
        event
        for event in detail.lifecycle_events
        if getattr(event, "repository_id", None) == repository_id
        and isinstance(
            event,
            (
                StepReachedEvent,
                StepStartedEvent,
                StepAttemptCreatedEvent,
                ProcessStartingEvent,
                ProcessStartedEvent,
                ProcessStateUnknownEvent,
                ProcessExitedEvent,
                DescendantsQuiescentEvent,
                TerminationRequestedEvent,
            ),
        )
    ]
    if not relevant:
        return None
    latest = max(relevant, key=lambda event: event.sequence)
    process_state: Literal[
        "NOT_STARTED", "STARTING", "RUNNING", "UNKNOWN", "EXITED", "QUIESCENT"
    ] = "NOT_STARTED"
    if isinstance(latest, ProcessStartingEvent):
        process_state = "STARTING"
    if isinstance(latest, ProcessStartedEvent):
        process_state = "RUNNING"
    if isinstance(latest, ProcessStateUnknownEvent):
        process_state = "UNKNOWN"
    if isinstance(latest, ProcessExitedEvent):
        process_state = "EXITED"
    if isinstance(latest, DescendantsQuiescentEvent):
        process_state = "QUIESCENT"
    # TerminationRequestedEvent is also ProcessStartedEvent; retain RUNNING until an exit/quiescence fact exists.
    return GameTapeRuntimeProgress(
        journal_sequence=latest.sequence,
        observed_at=latest.generated_at,
        step_id=getattr(latest, "step_id", None),
        step_order=getattr(latest, "step_order", None),
        attempt_number=getattr(latest, "attempt_number", None),
        physical_execution_id=getattr(latest, "physical_execution_id", None),
        process_state=process_state,
        pid=getattr(latest, "pid", None),
        termination_reason=(
            latest.reason if isinstance(latest, TerminationRequestedEvent) else None
        ),
    )


def _verification_execution_boundary(
    result: FleetPlanResult | None,
    producer_provenance: GameTapeProducerImplementationProvenance | None,
) -> GameTapeVerificationExecutionBoundary | None:
    if result is None or result.shard_execution is None:
        return None
    evidence = result.shard_execution
    if evidence.verification_selection_evidence_identity is None:
        return None
    required = (
        evidence.producer_artifact_identity,
        evidence.producer_implementation_identity,
        evidence.producer_contract_identity,
        evidence.repository_content_identity,
        evidence.verification_revision_identity,
        evidence.selected_membership_identity,
        evidence.actual_executed_membership_identity,
    )
    if any(value is None for value in required):
        return None
    return GameTapeVerificationExecutionBoundary(
        producer_version=evidence.producer_version,
        producer_artifact_identity=cast(str, evidence.producer_artifact_identity),
        producer_implementation_identity=cast(str, evidence.producer_implementation_identity),
        producer_implementation_provenance=(
            producer_provenance
            if producer_provenance is not None
            and producer_provenance.implementation_identity
            == evidence.producer_implementation_identity
            else None
        ),
        producer_contract_identity=cast(str, evidence.producer_contract_identity),
        repository_identity=cast(str, evidence.repository_content_identity),
        revision_identity=cast(str, evidence.verification_revision_identity),
        selection_evidence_identity=evidence.verification_selection_evidence_identity,
        producer_provenance_identity=evidence.verification_selection_evidence_identity,
        producer_repository_generation=evidence.producer_repository_generation,
        producer_identity_generation=evidence.producer_identity_generation,
        producer_evidence_hashes=evidence.producer_evidence_hashes,
        selected_membership_identity=cast(str, evidence.selected_membership_identity),
        selected_members=evidence.selected_members,
        actual_executed_membership_identity=cast(str, evidence.actual_executed_membership_identity),
        actual_executed_members=evidence.actual_executed_members,
        execution_plan_identity=evidence.plan_identity,
        execution_context_identity=evidence.execution_context_identity,
        execution_policy_identity=evidence.execution_policy_identity,
        membership_complete=evidence.membership_complete,
        unproven_owner_members=evidence.unproven_owner_members,
    )


def _shard_lineage(result: FleetPlanResult | None) -> tuple[GameTapeShardLineage, ...]:
    if result is None or result.shard_execution is None:
        return ()

    projected: list[GameTapeShardLineage] = []
    shard_execution = result.shard_execution

    def visit(item: CanonicalShardResultEvidence) -> None:
        projected.append(
            GameTapeShardLineage(
                producer_contract_identity=shard_execution.producer_contract_identity,
                producer_artifact_identity=shard_execution.producer_artifact_identity,
                producer_contract_schema=shard_execution.producer_contract_schema,
                producer_version=shard_execution.producer_version,
                shard_id=item.shard_id,
                described_work_identity=item.described_work_identity,
                physical_work_identity=item.physical_work_identity,
                parent_work_identity=item.parent_work_identity,
                refinement_depth=item.refinement_depth,
                status=item.status,
                resumed=item.resumed,
                members=item.members,
                attempts=tuple(
                    GameTapeShardAttemptLineage(
                        attempt_identity=attempt.attempt_identity,
                        physical_work_identity=attempt.physical_work_identity,
                        attempt=attempt.attempt,
                        status=attempt.status,
                        runtime_authority_receipt_identity=attempt.runtime_authority_receipt_identity,
                        supervisor_receipt_identity=attempt.supervisor_receipt_identity,
                        process_group_quiescent=attempt.process_group_quiescent,
                        forced_termination=attempt.forced_termination,
                    )
                    for attempt in item.attempts
                ),
            )
        )
        for child in item.children:
            visit(child)

    for item in result.shard_execution.results:
        visit(item)
    return tuple(projected)


def _plan_result(report: OperationReport) -> FleetPlanResult | None:
    evidence = report.evidence
    return evidence.plan if isinstance(evidence, FleetPlanOperationEvidence) else None


def _why_not(detail: FleetRunDetail) -> GameTapeWhyNotDiagnostic | None:
    failure = getattr(detail.run, "failure", None)
    if failure is None:
        return None
    authority_related = failure.code in {
        "EXECUTION_AUTHORITY_DIVERGENCE",
        "EXECUTOR_PLACEMENT_LOST",
        "EXECUTOR_READMISSION_NOT_ALLOWED",
        "EXECUTOR_ADMISSION_BLOCKED",
    }
    if failure.code == "EXECUTION_AUTHORITY_DIVERGENCE":
        action = (
            "Inspect the selected step's execution-authority evidence, then rerun from a fresh "
            "admission. Do not reuse the diverged materialization."
        )
    elif failure.code == "EXECUTOR_PLACEMENT_LOST":
        action = (
            "Restore the admitted executor or start a fresh run so placement can be admitted again."
        )
    elif failure.retryable:
        action = "Retry only after the reported blocking condition has changed."
    else:
        action = "Inspect the failure evidence before starting a new run."
    return GameTapeWhyNotDiagnostic(
        code=failure.code,
        stage=str(failure.stage),
        retryable=failure.retryable,
        authority_owner="execution_authority" if authority_related else "fleet_run",
        message=failure.message,
        safe_next_action=action,
    )
