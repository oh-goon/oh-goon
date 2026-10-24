"""Read-only campaign handoff over retained, separately authoritative Fleet Runs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from oh_goon.core.models.agent_repair import AgentRepairResult, AgentRepairStopReason
from oh_goon.core.models.agent_review import AgentAdjudicationRecord, AgentReviewRecord
from oh_goon.core.models.fleet_observation import AgentReviewObservation, NativeAgentObservation
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FailedFleetRun, FleetRunDetail
from oh_goon.core.models.target import FleetPlanTarget


def project_agent_campaign(
    campaign_id: str,
    details: Sequence[FleetRunDetail],
    *,
    journal_revision: int | None = None,
    invocation: AgentRepairResult | None = None,
) -> dict[str, Any]:
    """Expose original facts without reconstructing a controller stop decision.

    The controller's final source recapture happens after its last Run. Its
    invocation receipt is therefore required to report convergence. Retained
    Runs alone cannot establish that recapture or authorize continuation.
    """
    members = []
    for detail in details:
        target = detail.run.target
        if (
            isinstance(target, FleetPlanTarget)
            and (context := target.agent_repair) is not None
            and context.phase is not None
            and context.repair_id == campaign_id
        ):
            members.append((context, detail, target))
    members.sort(key=lambda item: (item[0].attempt, item[1].run.fleet_run_id))
    reasons: list[str] = []
    runs: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    adjudications: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    verification: list[dict[str, Any]] = []
    previous = None
    first = members[0][0] if members else None
    for context, detail, target in members:
        run_id = detail.run.fleet_run_id
        if context.attempt != len(runs) + 1 or context.previous_run_id != previous:
            reasons.append(
                f"Run {run_id} has missing, duplicate or conflicting predecessor linkage"
            )
        if context.attempt == 1 and run_id != campaign_id:
            reasons.append("The initial Run does not match the campaign identity")
        if first is not None and (
            context.original_execution_digest != first.original_execution_digest
            or context.max_attempts != first.max_attempts
            or context.limits != first.limits
            or context.write_roots != first.write_roots
        ):
            reasons.append(f"Run {run_id} changed the original campaign authority")
        if members and detail.run.targets != members[0][1].run.targets:
            reasons.append(f"Run {run_id} changed the original repository scope")
        if not detail.run.status.terminal:
            reasons.append(f"Run {run_id} has not reached a terminal state")
        if detail.outcome.value == "CANCELLED":
            reasons.append(f"Run {run_id} was cancelled; interrupted work is incomplete")
        selected = [step.step_id for step in target.plan.steps]
        executed = []
        repair_steps = []
        for operation in detail.operations:
            evidence = operation.evidence
            if not isinstance(evidence, FleetPlanOperationEvidence) or evidence.plan is None:
                continue
            if evidence.plan.plan_id != target.plan.plan_id:
                reasons.append(f"Run {run_id} has results for a different Plan")
            for step in evidence.plan.steps:
                origin = {"fleet_run_id": run_id, "step_id": step.step_id}
                executed.append(
                    {
                        **origin,
                        "status": step.status,
                        "exit_code": getattr(step, "returncode", None),
                        "native_observations": [
                            {
                                **observation.model_dump(mode="json", exclude={"events"}),
                                "context_observations": [
                                    event.model_dump(mode="json")
                                    for event in observation.events
                                    if event.kind == "context"
                                ],
                            }
                            for observation in step.observations
                            if isinstance(observation, NativeAgentObservation)
                        ],
                        "attempts": [
                            {
                                "attempt": attempt.attempt,
                                "argv": list(attempt.argv),
                                "exit_code": attempt.returncode,
                                "supervisor": attempt.supervisor.model_dump(mode="json")
                                if attempt.supervisor is not None
                                else None,
                            }
                            for attempt in step.attempts
                        ],
                    }
                )
                for observation in step.observations:
                    if not isinstance(observation, AgentReviewObservation):
                        continue
                    record = observation.record
                    if isinstance(record, AgentReviewRecord):
                        reviews.append(
                            {
                                **origin,
                                "review_pass": context.review_pass,
                                **record.model_dump(mode="json", exclude={"findings"}),
                                "finding_ids": [item.finding_id for item in record.findings],
                            }
                        )
                        findings.extend(
                            {
                                **origin,
                                "review_pass": context.review_pass,
                                "reviewer": record.reviewer,
                                "candidate_identity": record.candidate_identity,
                                **finding.model_dump(mode="json"),
                                "adjudication": None,
                                "resolution_status": "unverified",
                            }
                            for finding in record.findings
                        )
                    elif isinstance(record, AgentAdjudicationRecord):
                        adjudications.append(
                            {
                                **origin,
                                "review_pass": context.review_pass,
                                **record.model_dump(mode="json"),
                            }
                        )
                if context.phase == "repair":
                    repair_steps.append(
                        {
                            **origin,
                            "outputs": [output.model_dump(mode="json") for output in step.outputs],
                            "worktree_files_after": list(step.worktree_files_after),
                        }
                    )
        if len(detail.operations) != 1 or [step["step_id"] for step in executed] != selected:
            reasons.append(f"Run {run_id} lacks exact retained Step result membership")
        if context.phase == "repair":
            repairs.append(
                {
                    "fleet_run_id": run_id,
                    "candidate_before": context.candidate_identity,
                    "candidate_after": None,
                    "steps": repair_steps,
                    "change_payload_availability": "metadata_only",
                }
            )
        row = {
            "fleet_run_id": run_id,
            "attempt": context.attempt,
            "previous_run_id": context.previous_run_id,
            "phase": context.phase,
            "review_pass": context.review_pass,
            "candidate_identity": context.candidate_identity,
            "scope_identity": context.scope_identity,
            "execution_digest": target.execution_digest,
            "status": detail.run.status.value,
            "outcome": detail.outcome.value,
            "failure": detail.run.failure.model_dump(mode="json")
            if isinstance(detail.run, FailedFleetRun)
            else None,
            "readback_argv": ["oh-goon", "plan", "handoff", run_id, "--json"],
            "selected_step_ids": selected,
            "selected_commands": [
                {"step_id": step.step_id, "argv": list(getattr(step, "argv", ()))}
                for step in target.plan.steps
            ],
            "recorded_steps": executed,
            "unverified_step_ids": [
                step_id
                for step_id in selected
                if not any(
                    step["step_id"] == step_id
                    and step["status"] == "COMPLETED"
                    and step["exit_code"] == 0
                    for step in executed
                )
            ],
        }
        runs.append(row)
        if context.phase in ("regression", "verification"):
            verification.append(row)
        previous = run_id
    dispositions = {
        (
            record["candidate_identity"],
            record["review_pass"],
            item["reviewer"],
            item["finding_id"],
        ): {"fleet_run_id": record["fleet_run_id"], "step_id": record["step_id"], **item}
        for record in adjudications
        for item in record["dispositions"]
    }
    for finding in findings:
        disposition = dispositions.get(
            (
                finding["candidate_identity"],
                finding["review_pass"],
                finding["reviewer"],
                finding["finding_id"],
            )
        )
        finding["adjudication"] = disposition
        # Admission to repair is not proof that a finding was fixed.
        if disposition is not None and disposition["decision"] == "leave_alone":
            finding["resolution_status"] = "left_alone"
    by_previous = {row["previous_run_id"]: row for row in runs}
    for repair in repairs:
        successor = by_previous.get(repair["fleet_run_id"])
        if successor is not None and successor["phase"] == "regression":
            repair["candidate_after"] = successor["candidate_identity"]
    controller_result: dict[str, Any] = {"availability": "unavailable", "stop_reason": None}
    result = "incomplete"
    if not members:
        reasons.append("No admitted campaign Runs are available")
    if invocation is None:
        reasons.append(
            "The controller stop receipt is not retained; Runs alone do not prove convergence"
        )
    else:
        controller_result = {
            "availability": "invocation_receipt",
            "stop_reason": invocation.stop_reason.value,
            "message": invocation.message,
        }
        if invocation.repair_id != campaign_id:
            reasons.append("The invocation receipt belongs to another campaign")
        if invocation.unresolved_run_ids:
            reasons.append(
                "Admitted Run readback is unresolved: " + ", ".join(invocation.unresolved_run_ids)
            )
        if invocation.admitted_run_ids != tuple(row["fleet_run_id"] for row in runs):
            reasons.append("The projection does not account for every admitted Run")
        if invocation.attempts != tuple(detail for _, detail, _ in members):
            reasons.append("The invocation receipt differs from retained Run evidence")
        if invocation.converged and any(
            row["outcome"] != "SUCCESS" or row["unverified_step_ids"] for row in runs
        ):
            reasons.append("The converged controller receipt conflicts with retained Run results")
        if not reasons and invocation.converged:
            result = "converged"
        elif (
            invocation.stop_reason
            in {
                AgentRepairStopReason.APPLY_FAILED,
                AgentRepairStopReason.PROPOSAL_FAILED,
                AgentRepairStopReason.REGRESSION_FAILED,
                AgentRepairStopReason.VERIFICATION_FAILED,
                AgentRepairStopReason.NO_PROGRESS,
                AgentRepairStopReason.ATTEMPTS_EXHAUSTED,
            }
            and not reasons
        ):
            result = "failed"
        else:
            reasons.append(f"Controller stopped with {invocation.stop_reason.value}")
    return {
        "schema": "oh-goon.agent-campaign-result/v1",
        "campaign_id": campaign_id,
        "journal_revision": journal_revision,
        "result": result,
        "incomplete_reasons": reasons,
        "controller_result": controller_result,
        "original_execution_digest": first.original_execution_digest if first is not None else None,
        "repositories": [item.model_dump(mode="json") for item in members[0][1].run.targets]
        if members
        else [],
        "initial_candidate": first.candidate_identity
        if first is not None and first.attempt == 1
        else None,
        "last_admitted_candidate": members[-1][0].candidate_identity if members else None,
        "final_candidate": members[-1][0].candidate_identity
        if members and result == "converged"
        else None,
        "runs": runs,
        "reviews": reviews,
        "findings": findings,
        "adjudications": adjudications,
        "repair_attempts": repairs,
        "verification": verification,
        "claim_authority": "Review and adjudication records are source-bound Agent claims; Step results retain execution authority",
        "continuation_authority": "Read-only projection; a fresh authorized invocation is required",
    }
