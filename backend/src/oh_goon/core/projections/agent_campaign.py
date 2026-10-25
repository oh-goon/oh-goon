"""Read-only campaign handoff over retained, separately authoritative Fleet Runs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from oh_goon.core.models.agent_repair import AgentCampaignReadback, AgentRepairStopReason
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
    campaign: AgentCampaignReadback | None = None,
) -> dict[str, Any]:
    """Expose original facts without reconstructing a controller stop decision.

    Campaign decisions and child execution remain separate canonical facts.
    A verified Journal cut is required for convergence; reading never resumes.
    """
    if journal_revision is None and campaign is not None:
        journal_revision = campaign.journal_revision
    members = []
    for detail in details:
        target = detail.run.target
        if (
            isinstance(target, FleetPlanTarget)
            and (context := target.agent_repair) is not None
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
        executed: list[dict[str, Any]] = []
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
                        "settlement": step.settlement.model_dump(mode="json"),
                        "teardown": step.teardown.model_dump(mode="json")
                        if step.teardown is not None
                        else None,
                        "blocking_cause": step.reason if step.status == "SKIPPED" else None,
                        "native_observations": [
                            {
                                **observation.model_dump(mode="json", exclude={"events"}),
                                "context_observations": [
                                    {
                                        **event.model_dump(mode="json"),
                                        "summary": event.summary[:512],
                                        "omitted_summary_characters": max(
                                            0, len(event.summary) - 512
                                        ),
                                    }
                                    for event in tuple(
                                        event
                                        for event in observation.events
                                        if event.kind == "context"
                                    )[-8:]
                                ],
                                "event_projection_scope": "last_eight_controller_context_events",
                                "excluded_event_count": len(observation.events)
                                - min(
                                    8, sum(event.kind == "context" for event in observation.events)
                                ),
                            }
                            for observation in step.observations
                            if isinstance(observation, NativeAgentObservation)
                        ],
                        "attempts": [
                            {
                                "attempt": attempt.attempt,
                                "argv": list(attempt.argv),
                                "exit_code": attempt.returncode,
                                "observed_returncode": attempt.observed_returncode,
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
            "phase": context.phase or "failure_repair",
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
                    and step["settlement"]["outcome"] == "SUCCESS"
                    and step["settlement"]["cleanup"] == "VERIFIED"
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
    decision = campaign.terminal_decision if campaign is not None else None
    cleanup_unresolved = any(
        step["status"] != "SKIPPED"
        and (
            step["settlement"]["cleanup"] != "VERIFIED"
            or (step["teardown"] is not None and step["teardown"]["status"] == "FAILED")
        )
        for row in runs
        for step in row["recorded_steps"]
    )
    if cleanup_unresolved:
        reasons.append("Recorded process/resource cleanup remains unresolved")
    if campaign is None or decision is None:
        reasons.append(
            "The committed campaign decision is unavailable; child success cannot establish convergence"
        )
    else:
        controller_result = {
            "availability": "journal",
            "owner": "campaign_policy",
            "stop_reason": decision.stop_reason.value if decision.stop_reason is not None else None,
            "decision_id": decision.decision_id,
            "message": decision.message,
            "candidate_identity": decision.candidate_identity,
            "scope_identity": decision.scope_identity,
            "remaining_runs": decision.remaining_runs,
            "consumed_runs": [item.model_dump(mode="json") for item in decision.consumed_runs],
            "unresolved_run_ids": list(decision.unresolved_run_ids),
        }
        if campaign.campaign_id != campaign_id:
            reasons.append("The Journal decision belongs to another campaign")
        if not campaign.complete or campaign.journal_revision != journal_revision:
            reasons.append("Campaign evidence is incomplete or belongs to a different Journal cut")
        if decision.unresolved_run_ids:
            reasons.append(
                "Admitted Run readback is unresolved: " + ", ".join(decision.unresolved_run_ids)
            )
        phase_ids = tuple(phase.fleet_run_id for phase in campaign.phases)
        if phase_ids != tuple(row["fleet_run_id"] for row in runs):
            reasons.append("The projection does not account for every admitted Run")
        if tuple(item.fleet_run_id for item in decision.consumed_runs) != phase_ids:
            reasons.append("The terminal decision does not account for every admitted Run result")
        for phase, (_, _, target) in zip(campaign.phases, members, strict=False):
            if (
                phase.selected_step_ids != tuple(step.step_id for step in target.plan.steps)
                or phase.execution_digest != target.execution_digest
            ):
                reasons.append("The retained phase selection differs from admitted execution")
        if decision.stop_reason is AgentRepairStopReason.CONVERGED and any(
            row["outcome"] != "SUCCESS" or row["unverified_step_ids"] for row in runs
        ):
            reasons.append("The converged campaign decision conflicts with retained Run results")
        if decision.stop_reason is AgentRepairStopReason.CONVERGED and (
            not members
            or decision.unstarted_selection is not None
            or members[-1][0].phase != "verification"
            or decision.candidate_identity != members[-1][0].candidate_identity
            or decision.scope_identity != members[-1][0].scope_identity
        ):
            reasons.append(
                "The converged decision is not bound to the final verification candidate/scope"
            )
        if not reasons and decision.stop_reason is AgentRepairStopReason.CONVERGED:
            result = "converged"
        elif not reasons and decision.stop_reason is AgentRepairStopReason.TEST_PASSED:
            result = "test_passed"
        elif (
            decision.stop_reason
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
            reasons.append(
                f"Controller stopped with {decision.stop_reason.value if decision.stop_reason else 'unavailable'}"
            )
    return {
        "schema": "oh-goon.agent-campaign-result/v2",
        "campaign_id": campaign_id,
        "journal_revision": journal_revision,
        "result": result,
        "incomplete_reasons": reasons,
        "controller_result": controller_result,
        "intent": campaign.intent.model_dump(mode="json")
        if campaign is not None and campaign.intent is not None
        else None,
        "complete": campaign.complete if campaign is not None else False,
        "safe_next_action": "fresh_authorized_invocation"
        if decision is not None
        and not decision.unresolved_run_ids
        and not cleanup_unresolved
        and not reasons
        else "inspect_original_runs_and_resolve_ownership",
        "unstarted_selection": decision.unstarted_selection.model_dump(mode="json")
        if decision is not None and decision.unstarted_selection is not None
        else None,
        "decision_history": [record.model_dump(mode="json") for record in campaign.records]
        if campaign is not None
        else [],
        "original_execution_digest": first.original_execution_digest if first is not None else None,
        "repositories": [item.model_dump(mode="json") for item in members[0][1].run.targets]
        if members
        else [],
        "initial_candidate": first.candidate_identity
        if first is not None and first.attempt == 1
        else None,
        "last_admitted_candidate": members[-1][0].candidate_identity if members else None,
        "final_candidate": decision.candidate_identity
        if decision is not None and result == "converged"
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
