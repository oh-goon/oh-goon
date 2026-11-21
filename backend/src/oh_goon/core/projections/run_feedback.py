"""Read-only, bounded failure feedback from the supplied canonical Run cut."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

from oh_goon.core.agent_campaign_settlement import (
    regression_feedback_context,
    regression_recovery,
)
from oh_goon.core.models.agent_controls import MAX_AGENT_CONTROL_ID_CHARS
from oh_goon.core.models.fleet_plan import ResolvedAgentStep
from oh_goon.core.models.fleet_plan_evidence import (
    FailedPlanStepTeardownEvidence,
    PlanStepExecution,
)
from oh_goon.core.models.game_tape import GameTapeRepositoryExecution, GameTapeStepExecution
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    StrictFleetExecutionAdmission,
)
from oh_goon.core.models.run_feedback import (
    MAX_RUN_FEEDBACK_BYTES,
    MAX_RUN_FEEDBACK_GOALS,
    MAX_RUN_FEEDBACK_SOURCES,
    MAX_RUN_FEEDBACK_STEPS,
    MAX_RUN_FEEDBACK_VERIFICATIONS,
    RunFeedback,
    RunFeedbackAttempt,
    RunFeedbackBudgets,
    RunFeedbackCampaign,
    RunFeedbackConstraints,
    RunFeedbackDiagnostic,
    RunFeedbackGoal,
    RunFeedbackOmissions,
    RunFeedbackOutput,
    RunFeedbackReadbackReference,
    RunFeedbackRecoveryAction,
    RunFeedbackRetainedDiagnostic,
    RunFeedbackSource,
    RunFeedbackStep,
    RunFeedbackVerification,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.projections.agent_controls import project_agent_controls
from oh_goon.core.vocabulary import FleetRunFailureStage, FleetRunStatus

_MAX_WRITE_ROOTS = 16
_MAX_WRITE_ROOT_CHARS = 240
_MAX_VERIFICATION_BYTES = 16 * 1024


def _identity(value: str | None, field: str, omitted: list[str]) -> str | None:
    """An overlong identifier is omitted, never replaced by a different identity."""
    if value is not None and len(value) > MAX_AGENT_CONTROL_ID_CHARS:
        omitted.append(field)
        return None
    return value


def _diagnostic(
    detail: FleetRunDetail,
    step: GameTapeStepExecution,
    *,
    known_code_failure: bool,
    failure_kind: str | None,
    teardown_failed: bool,
    accepted_step: bool,
) -> RunFeedbackDiagnostic:
    settlement = step.settlement
    classification: str
    owner = "fleet_step_settlement"
    if not accepted_step:
        return RunFeedbackDiagnostic(
            classification="active" if not detail.run.status.terminal else "unknown_execution",
            owner="fleet_step_settlement",
            reason="RESULT_BINDING_UNPROVEN",
        )
    if settlement.outcome == "SKIPPED":
        # The acceptor's NOT_STARTED result owns absence of physical work.
        # No process-cleanup receipt is required for an unstarted Step.
        classification = "skipped"
    elif settlement.cleanup != "VERIFIED" or settlement.outcome in {"AUTHORITY_LOST", "INCOMPLETE"}:
        classification = "active" if not detail.run.status.terminal else "unknown_execution"
    elif teardown_failed:
        classification = "unknown_execution"
    elif settlement.outcome == "CANCELLED":
        classification = "cancelled"
    elif settlement.outcome == "TIMED_OUT":
        classification = "timed_out"
    elif settlement.reason == "FAILED_TO_START":
        classification = "environment_or_launch_failure"
    elif failure_kind == "model_output_truncated":
        classification = "model_output_truncated"
    elif failure_kind == "verification_unavailable":
        classification = "verification_unavailable"
    elif known_code_failure:
        classification, owner = "known_code_failure", "agent_campaign_settlement"
    elif settlement.outcome == "SUCCESS":
        classification = "success"
    else:
        classification = "unknown_execution"
    return RunFeedbackDiagnostic.model_validate(
        {"classification": classification, "owner": owner, "reason": settlement.reason}
    )


def _step(
    detail: FleetRunDetail,
    repository: GameTapeRepositoryExecution,
    step: GameTapeStepExecution,
    retained: tuple[RunFeedbackRetainedDiagnostic, ...],
    *,
    failure_kind: str | None,
    teardown_failed: bool,
    accepted_step: bool,
) -> RunFeedbackStep:
    cancellation = step.cancellation
    cancellation_supervisor = cancellation.supervisor if cancellation is not None else None
    return RunFeedbackStep(
        repository_id=repository.repository.repository_id,
        repository_execution_id=repository.repository_execution_id,
        step_id=step.step_id,
        step_execution_id=step.step_execution_id,
        status=step.status.value,
        diagnostic=_diagnostic(
            detail,
            step,
            known_code_failure=bool(retained),
            failure_kind=failure_kind,
            teardown_failed=teardown_failed,
            accepted_step=accepted_step,
        ),
        settlement=step.settlement,
        attempts=tuple(
            RunFeedbackAttempt(
                attempt=attempt.attempt,
                physical_execution_id=attempt.physical_execution_id,
                observed_returncode=attempt.observed_returncode,
                final_process_group_quiescent=attempt.supervisor.final_process_group_quiescent
                if attempt.supervisor is not None
                else None,
                forced_termination=attempt.supervisor.forced_termination
                if attempt.supervisor is not None
                else None,
                cleanup_reason=attempt.supervisor.cleanup_reason
                if attempt.supervisor is not None
                else None,
            )
            for attempt in step.attempts
        ),
        cancellation=RunFeedbackAttempt(
            physical_execution_id=cancellation.physical_execution_id,
            observed_returncode=cancellation.settlement.observed_returncode,
            final_process_group_quiescent=cancellation_supervisor.final_process_group_quiescent
            if cancellation_supervisor is not None
            else None,
            forced_termination=cancellation_supervisor.forced_termination
            if cancellation_supervisor is not None
            else None,
            cleanup_reason=cancellation_supervisor.cleanup_reason
            if cancellation_supervisor is not None
            else None,
        )
        if cancellation is not None
        else None,
        stdout_evidence=step.stdout_evidence,
        stderr_evidence=step.stderr_evidence,
        outputs=tuple(
            RunFeedbackOutput(
                name=output.name,
                sha256=output.sha256,
                byte_count=output.byte_count,
                producer_execution_identity=output.producer_execution_identity,
                retained_sha256=output.retained_stream.sha256
                if output.retained_stream is not None
                else None,
                physical_execution_id=output.retained_stream.physical_execution_id
                if output.retained_stream is not None
                else None,
            )
            for output in step.outputs[:4]
        ),
        omitted_outputs=max(0, len(step.outputs) - 4),
        retained_diagnostics=retained[:4],
        omitted_retained_diagnostics=max(0, len(retained) - 4),
    )


def _verification(
    repository: GameTapeRepositoryExecution, *, accepted: bool
) -> RunFeedbackVerification:
    evidence = repository.verification_execution if accepted else None
    results = repository.shard_lineage if accepted else ()
    row = RunFeedbackVerification(
        repository_id=repository.repository.repository_id,
        availability="recorded" if evidence is not None else "unavailable",
        selected_count=len(evidence.selected_members) if evidence is not None else 0,
        actual_count=len(evidence.actual_executed_members) if evidence is not None else 0,
        result_count=len(results),
        selection_evidence_identity=evidence.selection_evidence_identity
        if evidence is not None
        else None,
        selected_membership_identity=evidence.selected_membership_identity
        if evidence is not None
        else None,
        actual_membership_identity=evidence.actual_executed_membership_identity
        if evidence is not None
        else None,
        evidence=evidence,
        results=results,
        qualification="original_producer_qualifications" if evidence is not None else "unavailable",
    )
    return _omit_verification(row) if _encoded_size(row) > _MAX_VERIFICATION_BYTES else row


def _omit_verification(row: RunFeedbackVerification) -> RunFeedbackVerification:
    """Never return a clipped flat membership as the complete producer selection."""
    return row.model_copy(
        update={
            "availability": "omitted",
            "evidence": None,
            "results": (),
            "qualification": "omitted_not_qualified",
            "omitted_selected_members": row.selected_count,
            "omitted_actual_members": row.actual_count,
            "omitted_results": row.result_count,
        }
    )


def _encoded_size(value: BaseModel) -> int:
    # These models contain no source files or raw logs. JSON is the actual public
    # transport representation, including escaping of retained untrusted text.
    return len(
        json.dumps(
            value.model_dump(mode="json", by_alias=True), ensure_ascii=True, indent=2
        ).encode("utf-8")
    )


def _accepted_reports(detail: FleetRunDetail) -> dict[str, OperationReport]:
    """Bind only original operation results to their parent Run and repository."""
    accepted: dict[str, OperationReport] = {}
    repositories = {repository.repository_id: repository for repository in detail.run.targets}
    reports_per_repository: dict[str, int] = {}
    for operation in detail.operations:
        repository_id = operation.repository.repository_id
        reports_per_repository[repository_id] = reports_per_repository.get(repository_id, 0) + 1
    for operation in detail.operations:
        repository_id = operation.repository.repository_id
        if (
            reports_per_repository[repository_id] != 1
            or operation.provenance.fleet_run_id != detail.run.fleet_run_id
            or operation.target != detail.run.target
            or operation.action != detail.run.action
            or repositories.get(repository_id) != operation.repository
        ):
            continue
        accepted[repository_id] = operation
    return accepted


def _accepted_steps(
    detail: FleetRunDetail, reports: dict[str, OperationReport]
) -> dict[tuple[str, str], PlanStepExecution]:
    """Bind diagnostics to the original accepted operation, source and membership."""
    target = detail.run.target
    if not isinstance(target, FleetPlanTarget):
        return {}
    selected = tuple((step.step_id, step.kind) for step in target.plan.steps)
    accepted: dict[tuple[str, str], PlanStepExecution] = {}
    for repository_id, operation in reports.items():
        evidence = operation.evidence
        if (
            not isinstance(evidence, FleetPlanOperationEvidence)
            or evidence.plan is None
            or evidence.plan.plan_id != target.plan.plan_id
            or tuple((step.step_id, step.kind) for step in evidence.plan.steps) != selected
        ):
            continue
        for step in evidence.plan.steps:
            accepted[(repository_id, step.step_id)] = step
    return accepted


def _run_diagnostic(
    detail: FleetRunDetail,
    steps: list[RunFeedbackStep],
    verification: tuple[RunFeedbackVerification, ...],
    reports: dict[str, OperationReport],
) -> RunFeedbackDiagnostic:
    if not detail.run.status.terminal:
        return RunFeedbackDiagnostic(
            classification="active", owner="fleet_run", reason="RUN_ACTIVE"
        )
    failure = getattr(detail.run, "failure", None)
    if failure is not None:
        return RunFeedbackDiagnostic(
            classification="unknown_execution",
            owner="fleet_run",
            reason=failure.code[:160],
            omitted_reason_characters=max(0, len(failure.code) - 160),
        )
    if not any(
        step.settlement.outcome != "SKIPPED" and step.settlement.cleanup != "VERIFIED"
        for step in steps
    ) and any(
        result.status == "ENVIRONMENT_BLOCKED" for row in verification for result in row.results
    ):
        return RunFeedbackDiagnostic(
            classification="verification_unavailable",
            owner="fleet_shard_execution",
            reason="VERIFICATION_ENVIRONMENT_BLOCKED",
        )
    if steps:
        return steps[0].diagnostic
    if isinstance(detail.run.target, FleetPlanTarget):
        return RunFeedbackDiagnostic(
            classification="verification_unavailable",
            owner="fleet_run",
            reason="NO_ACCEPTED_STEP_RESULT",
        )
    if (
        not reports
        or len(reports) != len(detail.run.targets)
        or len(detail.operations) != len(detail.run.targets)
    ):
        return RunFeedbackDiagnostic(
            classification="unknown_execution", owner="fleet_run", reason="RESULT_BINDING_UNPROVEN"
        )
    if any(not report.outcome.verdict.successful for report in reports.values()):
        return RunFeedbackDiagnostic(
            classification="unknown_execution", owner="fleet_run", reason="OPERATION_NOT_SUCCESSFUL"
        )
    return RunFeedbackDiagnostic(
        classification="cancelled" if detail.run.status is FleetRunStatus.CANCELLED else "success",
        owner="fleet_run",
        reason="RUN_CANCELLED"
        if detail.run.status is FleetRunStatus.CANCELLED
        else "ACCEPTED_OPERATION_RESULTS",
    )


def _recovery_action(
    detail: FleetRunDetail, steps: list[RunFeedbackStep], diagnostic: RunFeedbackDiagnostic
) -> RunFeedbackRecoveryAction:
    """Read-only ingress hint; the original recovery/admission owner still decides.

    The existing Run-history service rejects active, budgeted and authenticated
    Plan lineage. Retained uncertainty never becomes permission to repeat work.
    """
    if not detail.run.status.terminal:
        return "none"
    failure = getattr(detail.run, "failure", None)
    if (
        diagnostic.classification == "unknown_execution"
        or any(
            step.diagnostic.classification == "unknown_execution"
            and step.settlement.outcome != "SKIPPED"
            for step in steps
        )
        or any(operation.resource_settlement == "UNRESOLVED" for operation in detail.operations)
        or (
            failure is not None
            and (
                failure.stage is FleetRunFailureStage.RECOVERY
                or failure.code
                in {
                    "EXTERNAL_EFFECT_OUTCOME_UNKNOWN",
                    "REPOSITORY_EFFECT_OUTCOME_UNKNOWN",
                    "RUN_PARTIALLY_COMMITTED_BY_RESTART",
                    "RUN_INTERRUPTED_BY_RESTART",
                }
            )
        )
    ):
        return "open-recovery"
    if isinstance(detail.run.target, FleetPlanTarget) and (
        detail.run.target.agent_repair is not None
        or isinstance(detail.execution, StrictFleetExecutionAdmission)
    ):
        return "none"
    if isinstance(detail.execution, DirectFleetExecutionAdmission) and (
        detail.execution.resource_envelope is not None
    ):
        return "none"
    return "run-same-target" if detail.rerun_supported else "none"


def _bound(feedback: RunFeedback) -> RunFeedback:
    """Drop whole low-priority projections, preserving explicit omission counts."""
    while _encoded_size(feedback) > MAX_RUN_FEEDBACK_BYTES:
        omissions = feedback.omissions.model_copy(update={"response_limit_applied": True})
        if any(row.evidence is not None or row.results for row in feedback.verification):
            feedback = feedback.model_copy(
                update={
                    "verification": tuple(_omit_verification(row) for row in feedback.verification),
                    "omissions": omissions,
                }
            )
        elif feedback.steps:
            feedback = feedback.model_copy(
                update={
                    "steps": feedback.steps[:-1],
                    "omissions": omissions.model_copy(update={"steps": omissions.steps + 1}),
                }
            )
        elif feedback.goals:
            feedback = feedback.model_copy(
                update={
                    "goals": feedback.goals[:-1],
                    "omissions": omissions.model_copy(update={"goals": omissions.goals + 1}),
                }
            )
        elif feedback.sources:
            feedback = feedback.model_copy(
                update={
                    "sources": feedback.sources[:-1],
                    "omissions": omissions.model_copy(update={"sources": omissions.sources + 1}),
                }
            )
        elif feedback.constraints.write_roots:
            feedback = feedback.model_copy(
                update={
                    "constraints": feedback.constraints.model_copy(
                        update={
                            "write_roots": feedback.constraints.write_roots[:-1],
                            "omitted_write_roots": feedback.constraints.omitted_write_roots + 1,
                        }
                    ),
                    "omissions": omissions,
                }
            )
        elif feedback.campaign is not None:
            feedback = feedback.model_copy(
                update={
                    "campaign": None,
                    "omissions": omissions.model_copy(
                        update={
                            "fields": (*omissions.fields, "campaign"),
                        }
                    ),
                }
            )
        else:
            # All remaining fields have fixed scalar/collection bounds. This
            # branch catches a future DTO extension that breaks this contract.
            raise ValueError("Run feedback fixed metadata exceeds its transport bound")
    return feedback


def project_run_feedback(detail: FleetRunDetail) -> RunFeedback:
    """Observe one immutable supplied cut; perform no I/O, clock read or admission."""
    tape = detail.game_tape
    target = detail.run.target
    plan_target = target if isinstance(target, FleetPlanTarget) else None
    context = plan_target.agent_repair if plan_target is not None else None
    campaign = detail.campaign_journal
    if campaign is not None and (
        context is None
        or campaign.campaign_id != context.repair_id
        or not campaign.complete
        or campaign.journal_revision != detail.journal_revision
    ):
        campaign = None
    intent = campaign.intent if campaign is not None else None
    decision = campaign.terminal_decision if campaign is not None else None
    if campaign is not None and decision is None:
        from oh_goon.core.models.agent_repair import AgentCampaignDecision

        decision = next(
            (
                record.fact
                for record in reversed(campaign.records)
                if isinstance(record.fact, AgentCampaignDecision)
            ),
            None,
        )
    omitted_fields: list[str] = []
    goals = []
    for goal_step in plan_target.plan.steps if plan_target is not None else ():
        if not isinstance(goal_step, ResolvedAgentStep):
            continue
        summary = goal_step.task_summary
        goals.append(
            RunFeedbackGoal(
                step_id=goal_step.step_id,
                availability="recorded" if summary is not None else "unavailable",
                summary=summary[:2048] if summary is not None else None,
                sha256=goal_step.task_sha256,
                redacted=goal_step.task_redacted,
                handoff_identity=goal_step.handoff_identity,
                delivery_sha256=goal_step.delivery_sha256,
                omitted_characters=goal_step.task_omitted_characters + max(0, len(summary) - 2048)
                if summary is not None
                else 0,
            )
        )
    recovery = regression_recovery(detail)
    retained_by_step: dict[str, list[RunFeedbackRetainedDiagnostic]] = {}
    if recovery is not None:
        for entry in regression_feedback_context(detail, recovery)["feedback"]:
            retained_by_step.setdefault(entry["step_id"], []).append(
                RunFeedbackRetainedDiagnostic(
                    output_name=entry["output_name"],
                    physical_execution_id=entry["physical_execution_id"],
                    sha256=entry["sha256"],
                    byte_count=entry["byte_count"],
                    text=entry["text"],
                    redacted=entry["redacted"],
                )
            )
    accepted_reports = _accepted_reports(detail)
    original_steps = _accepted_steps(detail, accepted_reports)
    verification = tuple(
        _verification(
            repository,
            accepted=any(key[0] == repository.repository.repository_id for key in original_steps),
        )
        for repository in tape.repositories[:MAX_RUN_FEEDBACK_VERIFICATIONS]
    )
    steps = []
    for repository in tape.repositories:
        for step in repository.steps:
            original = original_steps.get((repository.repository.repository_id, step.step_id))
            steps.append(
                _step(
                    detail,
                    repository,
                    step,
                    tuple(retained_by_step.get(step.step_id, ())),
                    failure_kind=getattr(original, "failure_kind", None),
                    teardown_failed=original is not None
                    and isinstance(original.teardown, FailedPlanStepTeardownEvidence),
                    accepted_step=original is not None or step.cancellation is not None,
                )
            )
    # Stable failed/uncertain-first projection. Long successful prefixes cannot
    # hide the original failure; this ordering changes no admitted membership.
    priority = {"unknown_execution": 0, "active": 1, "success": 3, "skipped": 4}
    steps.sort(
        key=lambda row: (
            (row.settlement.cleanup == "VERIFIED" or row.settlement.outcome == "SKIPPED")
            and row.settlement.outcome not in {"INCOMPLETE", "AUTHORITY_LOST"},
            priority.get(row.diagnostic.classification, 2),
        )
    )
    diagnostic = _run_diagnostic(detail, steps, verification, accepted_reports)
    write_roots = context.write_roots if context is not None else ()
    selected_roots = tuple(
        root for root in write_roots[:_MAX_WRITE_ROOTS] if len(root) <= _MAX_WRITE_ROOT_CHARS
    )
    run_id = detail.run.fleet_run_id
    run_id_sha256 = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    candidate: str | None = None
    scope: str | None = None
    if campaign is not None and campaign.records:
        latest = campaign.records[-1].fact
        from oh_goon.core.models.agent_repair import AgentCampaignPhase

        if isinstance(latest, AgentCampaignPhase):
            candidate, scope = latest.context.candidate_identity, latest.context.scope_identity
        else:
            candidate, scope = latest.candidate_identity, latest.scope_identity
    feedback = RunFeedback(
        fleet_run_id=_identity(run_id, "fleet_run_id", omitted_fields),
        fleet_run_id_sha256=run_id_sha256,
        journal_revision=detail.journal_revision,
        status=detail.run.status.value,
        outcome=detail.outcome.value,
        terminal=detail.run.status.terminal,
        diagnostic=diagnostic,
        recovery_action=_recovery_action(detail, steps, diagnostic),
        goals=tuple(goals[:MAX_RUN_FEEDBACK_GOALS]),
        constraints=RunFeedbackConstraints(
            execution_policy=plan_target.plan.execution if plan_target is not None else None,
            limits=context.limits if context is not None else None,
            write_roots=selected_roots,
            omitted_write_roots=len(write_roots) - len(selected_roots),
        ),
        campaign=RunFeedbackCampaign(
            campaign_id=_identity(context.repair_id, "campaign.campaign_id", omitted_fields),
            current_candidate_identity=_identity(
                candidate, "campaign.current_candidate_identity", omitted_fields
            ),
            scope_identity=_identity(scope, "campaign.scope_identity", omitted_fields),
            run_candidate_identity=_identity(
                context.candidate_identity, "campaign.run_candidate_identity", omitted_fields
            ),
            run_scope_identity=_identity(
                context.scope_identity, "campaign.run_scope_identity", omitted_fields
            ),
            original_execution_digest=_identity(
                context.original_execution_digest,
                "campaign.original_execution_digest",
                omitted_fields,
            ),
            phase=context.phase,
            attempt=context.attempt,
        )
        if context is not None
        else None,
        budgets=RunFeedbackBudgets(
            original=intent.budget if intent is not None else None,
            maximum_runs=intent.maximum_runs if intent is not None else context.max_attempts,
            remaining_runs=decision.remaining_runs if decision is not None else None,
            remaining_runs_authority="committed_decision"
            if decision is not None
            else "unavailable",
            decision_id=_identity(decision.decision_id, "budgets.decision_id", omitted_fields)
            if decision is not None
            else None,
            decision_phase_ordinal=decision.phase_ordinal if decision is not None else None,
            deadline_at=intent.deadline_at if intent is not None else context.limits.deadline_at,
        )
        if context is not None
        else None,
        sources=tuple(
            RunFeedbackSource(
                repository_id=admission.repository_id,
                admission_id=admission.admission_id,
                source_authority_id=admission.source_authority.authority_id,
                source_subject_instance_id=admission.source_authority.subject_instance_id,
                source_content_sha256=admission.source_authority.source_snapshot.sha256,
            )
            for admission in detail.repository_admissions[:MAX_RUN_FEEDBACK_SOURCES]
        ),
        steps=tuple(steps[:MAX_RUN_FEEDBACK_STEPS]),
        verification=verification,
        controls=project_agent_controls(detail),
        omissions=RunFeedbackOmissions(
            steps=max(0, len(steps) - MAX_RUN_FEEDBACK_STEPS),
            goals=max(0, len(goals) - MAX_RUN_FEEDBACK_GOALS),
            sources=max(0, len(detail.repository_admissions) - MAX_RUN_FEEDBACK_SOURCES),
            verifications=max(0, len(tape.repositories) - MAX_RUN_FEEDBACK_VERIFICATIONS),
            fields=tuple(omitted_fields),
        ),
        readback=(
            RunFeedbackReadbackReference(
                projection="detail",
                fleet_run_id=run_id if len(run_id) <= MAX_AGENT_CONTROL_ID_CHARS else None,
                fleet_run_id_sha256=run_id_sha256,
                evidence="original_run",
            ),
            RunFeedbackReadbackReference(
                projection="game_tape",
                fleet_run_id=run_id if len(run_id) <= MAX_AGENT_CONTROL_ID_CHARS else None,
                fleet_run_id_sha256=run_id_sha256,
                evidence="complete_verification_membership",
            ),
            RunFeedbackReadbackReference(
                projection="outputs",
                fleet_run_id=run_id if len(run_id) <= MAX_AGENT_CONTROL_ID_CHARS else None,
                fleet_run_id_sha256=run_id_sha256,
                evidence="retained_output_references",
            ),
        ),
    )
    return _bound(feedback)
