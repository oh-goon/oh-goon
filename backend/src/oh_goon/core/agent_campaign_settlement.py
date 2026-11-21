"""Accepted regression recovery and convergence over original campaign facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_repair import (
    AgentCampaignDecision,
    AgentCampaignReadback,
    AgentRegressionFeedbackReference,
    AgentRegressionRecovery,
)
from oh_goon.core.models.agent_review import AgentAdjudicationRecord, AgentReviewRecord
from oh_goon.core.models.fleet_observation import AgentReviewObservation
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedCommandStep
from oh_goon.core.models.fleet_plan_evidence import (
    FailedPlanStepTeardownEvidence,
    PlanStepExecution,
)
from oh_goon.core.models.fleet_plan_step_primitives import PlanStreamOutput
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.target import FleetPlanTarget


def _plan_steps(detail: FleetRunDetail) -> tuple[PlanStepExecution, ...] | None:
    target = detail.run.target
    if (
        not isinstance(target, FleetPlanTarget)
        or detail.run.status.value != "COMPLETED"
        or len(detail.operations) != 1
    ):
        return None
    operation = detail.operations[0]
    evidence = operation.evidence
    if (
        operation.resource_settlement == "UNRESOLVED"
        or not isinstance(evidence, FleetPlanOperationEvidence)
        or evidence.plan is None
        or evidence.plan.plan_id != target.plan.plan_id
        or tuple((step.step_id, step.kind) for step in evidence.plan.steps)
        != tuple((step.step_id, step.kind) for step in target.plan.steps)
    ):
        return None
    return evidence.plan.steps


def _settled_attempt(step: PlanStepExecution) -> bool:
    if (
        step.status not in {"COMPLETED", "FAILED"}
        or step.settlement.cleanup != "VERIFIED"
        or isinstance(step.teardown, FailedPlanStepTeardownEvidence)
        or len(step.attempts) != 1
    ):
        return False
    attempt = step.attempts[0]
    supervisor = attempt.supervisor
    return (
        attempt.attempt == 1
        and supervisor is not None
        and supervisor.final_process_group_quiescent is True
        and supervisor.forced_termination is False
        and supervisor.cleanup_reason == "none"
        and not supervisor.process_group_cleanup_requested
        and (
            attempt.standard_isolation is None
            or attempt.standard_isolation.filesystem_cleanup == "verified"
        )
        and step.settlement.observed_returncode
        == attempt.observed_returncode
        == attempt.returncode
        == getattr(step, "returncode", None)
    )


def regression_recovery(detail: FleetRunDetail) -> AgentRegressionRecovery | None:
    """Classify only explicitly declared, completely settled native code failures.

    A declaration grants no success or replay authority. The retained native
    attempt, cleanup and bounded raw-feedback publication must also agree.
    """
    target = detail.run.target
    if not isinstance(target, FleetPlanTarget) or target.agent_repair is None:
        return None
    context = target.agent_repair
    if (
        context.phase != "regression"
        or context.candidate_identity is None
        or context.scope_identity is None
        or len(detail.run.targets) != 1
        or detail.outcome.value in {"SUCCESS", "CANCELLED"}
    ):
        return None
    steps = _plan_steps(detail)
    if (
        steps is None
        or detail.operations[0].provenance.fleet_run_id != detail.run.fleet_run_id
        or detail.operations[0].target != target
        or detail.operations[0].repository != detail.run.targets[0]
    ):
        return None
    feedback = []
    for selected, step in zip(target.plan.steps, steps, strict=True):
        if (
            not isinstance(selected, ResolvedCommandStep)
            or selected.uv is not None
            or selected.project_source is not None
            or not _settled_attempt(step)
        ):
            return None
        if step.settlement.outcome == "SUCCESS":
            if step.status != "COMPLETED":
                return None
            continue
        policy = selected.regression_failure
        returncode = step.settlement.observed_returncode
        if (
            step.status != "FAILED"
            or step.settlement.outcome != "FAILED"
            or step.settlement.reason != "FAILED_EXIT"
            or policy is None
            or returncode is None
            or returncode <= 0
            or 124 <= returncode <= 127
            or returncode not in policy.exit_codes
        ):
            return None
        declarations = [
            output
            for output in selected.outputs
            if isinstance(output, PlanStreamOutput) and output.name == policy.feedback
        ]
        outputs = [output for output in step.outputs if output.name == policy.feedback]
        if (
            len(declarations) != 1
            or declarations[0].stream != "stdout"
            or not declarations[0].retain
            or declarations[0].max_bytes > 2048
            or len(outputs) != 1
            or outputs[0].retained_stream is None
        ):
            return None
        retained = outputs[0].retained_stream
        encoded = retained.text.encode("utf-8")
        if (
            not retained.text.strip()
            or not 0 < len(encoded) <= 2048
            or len(encoded) != retained.byte_count
            or hashlib.sha256(encoded).hexdigest() != retained.sha256
            or retained.source != step.stdout_evidence
            or retained.source != step.attempts[0].stdout_evidence
            or step.attempts[0].physical_execution_id is None
            or retained.physical_execution_id != step.attempts[0].physical_execution_id
            or not 0 < retained.source.byte_count <= declarations[0].max_bytes
        ):
            return None
        feedback.append(
            AgentRegressionFeedbackReference(
                step_id=step.step_id,
                output_name=policy.feedback,
                physical_execution_id=retained.physical_execution_id,
                sha256=retained.sha256,
                byte_count=retained.byte_count,
            )
        )
    if not feedback:
        return None
    return AgentRegressionRecovery(
        fleet_run_id=detail.run.fleet_run_id,
        operation_id=detail.operations[0].provenance.operation_id,
        candidate_identity=context.candidate_identity,
        scope_identity=context.scope_identity,
        feedback=tuple(feedback),
    )


def regression_feedback_context(
    detail: FleetRunDetail, recovery: AgentRegressionRecovery
) -> dict[str, Any]:
    """Transport retained failed-test bytes, never ambient logs or old approvals."""
    if regression_recovery(detail) != recovery:
        raise FleetPlanInvalid("Regression feedback differs from its exact accepted native result")
    steps = _plan_steps(detail)
    assert steps is not None
    entries = []
    for reference in recovery.feedback:
        step = next(step for step in steps if step.step_id == reference.step_id)
        output = next(output for output in step.outputs if output.name == reference.output_name)
        retained = output.retained_stream
        assert retained is not None
        entries.append(
            {
                **reference.model_dump(mode="json"),
                "text": retained.text,
                "source": retained.source.model_dump(mode="json"),
                "redacted": retained.redacted,
                "observed_returncode": step.settlement.observed_returncode,
            }
        )
    return {
        "fleet_run_id": recovery.fleet_run_id,
        "operation_id": recovery.operation_id,
        "repository_id": detail.run.targets[0].repository_id,
        "candidate_identity": recovery.candidate_identity,
        "scope_identity": recovery.scope_identity,
        "feedback": entries,
    }


def _successful(detail: FleetRunDetail) -> bool:
    steps = _plan_steps(detail)
    return (
        detail.outcome.value == "SUCCESS"
        and steps is not None
        and all(
            step.status == "COMPLETED"
            and step.settlement.outcome == "SUCCESS"
            and step.settlement.cleanup == "VERIFIED"
            and not isinstance(step.teardown, FailedPlanStepTeardownEvidence)
            for step in steps
        )
    )


def regression_phase_context_error(
    campaign: AgentCampaignReadback,
    details: Sequence[FleetRunDetail],
    target: FleetPlanTarget,
) -> str | None:
    """A new Agent phase consumes exactly its committed failed-candidate feedback."""
    context = target.agent_repair
    expected = None
    if context is not None:
        marker = next(
            (
                record.fact.regression_recovery
                for record in reversed(campaign.records)
                if isinstance(record.fact, AgentCampaignDecision)
                and record.fact.regression_recovery is not None
                and record.fact.regression_recovery.candidate_identity == context.candidate_identity
            ),
            None,
        )
        if marker is not None:
            detail = next(
                (detail for detail in details if detail.run.fleet_run_id == marker.fleet_run_id),
                None,
            )
            if detail is None or regression_recovery(detail) != marker:
                return "Campaign phase lacks its exact accepted regression feedback"
            expected = regression_feedback_context(detail, marker)
    for step in target.plan.steps:
        if not isinstance(step, ResolvedAgentStep):
            continue
        try:
            payload = json.loads(step.review_context) if step.review_context is not None else None
        except (ValueError, TypeError):
            payload = None
        actual = payload.get("regression_feedback") if isinstance(payload, dict) else None
        if actual != expected:
            return "Campaign phase changed or omitted its committed regression feedback"
    return None


def _review_records(detail: FleetRunDetail) -> tuple[AgentReviewRecord, ...]:
    steps = _plan_steps(detail)
    return tuple(
        observation.record
        for step in steps or ()
        for observation in step.observations
        if isinstance(observation, AgentReviewObservation)
        and isinstance(observation.record, AgentReviewRecord)
    )


def campaign_convergence_error(
    campaign: AgentCampaignReadback,
    details: Sequence[FleetRunDetail],
    decision: AgentCampaignDecision,
) -> str | None:
    """Validate historical failures without converting them into successful Runs."""
    phases = campaign.phases
    if tuple(phase.fleet_run_id for phase in phases) != tuple(
        detail.run.fleet_run_id for detail in details
    ):
        return "Campaign convergence lacks exact admitted child membership"
    failed = [index for index, detail in enumerate(details) if not _successful(detail)]
    recoveries = [
        record.fact.regression_recovery
        for record in campaign.records
        if isinstance(record.fact, AgentCampaignDecision)
        and record.fact.regression_recovery is not None
    ]
    if len({item.fleet_run_id for item in recoveries}) != len(recoveries) or any(
        not any(
            detail.run.fleet_run_id == recovery.fleet_run_id
            and regression_recovery(detail) == recovery
            for detail in details
        )
        for recovery in recoveries
    ):
        return "Campaign convergence contains spurious regression recovery"
    if not failed:
        return None
    for index in failed:
        recovery = regression_recovery(details[index])
        phase = phases[index]
        position = next(
            (i for i, record in enumerate(campaign.records) if record.fact == phase), -1
        )
        following = (
            campaign.records[position + 1].fact
            if position >= 0 and position + 1 < len(campaign.records)
            else None
        )
        if (
            recovery is None
            or not isinstance(following, AgentCampaignDecision)
            or following.stop_reason is not None
            or following.regression_recovery != recovery
            or following.next_phase != "review"
            or following.candidate_identity != recovery.candidate_identity
            or following.scope_identity != recovery.scope_identity
            or index + 1 >= len(phases)
            or phases[index + 1].context.phase != "review"
            or position + 2 >= len(campaign.records)
            or campaign.records[position + 2].fact != phases[index + 1]
            or following.next_step_ids != phases[index + 1].selected_step_ids
            or tuple(item.fleet_run_id for item in following.consumed_runs)
            != tuple(item.fleet_run_id for item in phases[: index + 1])
        ):
            return "Campaign convergence conflicts with an unsuperseded child failure"
    allowed = {
        "review": {"adjudicate", "verification"},
        "adjudicate": {"repair"},
        "repair": {"regression"},
        "regression": {"review"},
    }
    seen_candidates = {phase.context.candidate_identity for phase in phases[: failed[0] + 1]}
    passing_candidate = None
    for index in range(failed[0], len(phases)):
        context = phases[index].context
        target = details[index].run.target
        if (
            not isinstance(target, FleetPlanTarget)
            or target.agent_repair != context
            or context.candidate_identity is None
            or context.scope_identity is None
            or details[index].operations[0].provenance.fleet_run_id
            != details[index].run.fleet_run_id
            or details[index].operations[0].target != target
        ):
            return "Campaign recovery changed its admitted candidate or scope"
        if (error := regression_phase_context_error(campaign, details, target)) is not None:
            return error
        if context.phase == "regression":
            passing_candidate = context.candidate_identity if _successful(details[index]) else None
        if context.phase == "review":
            records = _review_records(details[index])
            if not records or any(
                record.candidate_identity != context.candidate_identity
                or record.scope_identity != context.scope_identity
                for record in records
            ):
                return "Campaign recovery lacks fresh candidate-bound review evidence"
            clean = all(record.verdict == "CLEAN" for record in records)
            if clean and passing_candidate != context.candidate_identity:
                return "A CLEAN review cannot supersede a known failed regression"
            if not clean and any(record.verdict not in {"CLEAN", "FINDING"} for record in records):
                return "Campaign recovery lacks an accepted fresh review"
        if context.phase == "adjudicate":
            steps = _plan_steps(details[index])
            adjudications = [
                observation.record
                for step in steps or ()
                for observation in step.observations
                if isinstance(observation, AgentReviewObservation)
                and isinstance(observation.record, AgentAdjudicationRecord)
            ]
            if not adjudications or any(
                record.candidate_identity != context.candidate_identity
                or record.scope_identity != context.scope_identity
                or not any(item.decision == "repair" for item in record.dispositions)
                for record in adjudications
            ):
                return "Campaign recovery lacks fresh repair adjudication"
        if index == len(phases) - 1:
            if (
                context.phase != "verification"
                or context.candidate_identity != decision.candidate_identity
                or context.scope_identity != decision.scope_identity
                or passing_candidate != decision.candidate_identity
                or index == 0
                or phases[index - 1].context.phase != "review"
                or not all(
                    record.verdict == "CLEAN" for record in _review_records(details[index - 1])
                )
            ):
                return "Campaign recovery lacks verification of the repaired final candidate"
            continue
        successor = phases[index + 1].context
        if (
            successor.phase not in allowed.get(context.phase or "", set())
            or successor.previous_run_id != phases[index].fleet_run_id
            or (context.phase != "repair" and successor.scope_identity != context.scope_identity)
            or successor.review_pass != (context.review_pass or 0) + (context.phase == "regression")
        ):
            return "Campaign recovery has a conflicting phase or predecessor chain"
        if context.phase == "repair":
            if successor.candidate_identity in seen_candidates:
                return "Campaign recovery repeated an earlier candidate"
            seen_candidates.add(successor.candidate_identity)
        elif successor.candidate_identity != context.candidate_identity:
            return "Campaign recovery changed candidate outside its repair phase"
    return None
