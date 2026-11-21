"""Failure feedback binds accepted facts and leaves incomplete evidence explicit."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from oh_goon.core.models.agent_repair import (
    AgentCampaignDecision,
    AgentCampaignIntent,
    AgentCampaignReadback,
    AgentCampaignRecord,
    AgentRepairBudget,
    AgentRepairContext,
)
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionSupervisorEvidence,
    PlanOutputEvidence,
    PlanRetainedStreamOutputEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
    PublishedPlanOutputEvidence,
    SkippedPlanStepExecution,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    FleetPlanRunLimits,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    CompletedFleetRun,
    FleetRunDetail,
    FleetRunProgress,
    RunningFleetRun,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.run_feedback import MAX_RUN_FEEDBACK_BYTES
from oh_goon.core.models.shard_execution import (
    CanonicalShardExecutionEvidence,
    CanonicalShardResultEvidence,
)
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.projections.run_feedback import project_run_feedback
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction, Verdict
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import (
    structural_execution_identity,
    structural_fleet_plan_target,
)
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for

_NOW = datetime(2026, 11, 21, tzinfo=UTC)
_HASH = "sha256:" + "a" * 64


def _detail(
    *,
    count: int = 1,
    failed_index: int = 0,
    settlement: PlanStepSettlementEvidence | None = None,
    recoverable: bool = False,
    physical_id: str = "original-physical-execution",
    failure_kind: str | None = None,
    agent_metadata: bool = False,
    failed_exit: int = 1,
) -> FleetRunDetail:
    declared = tuple(
        ResolvedAgentStep.model_validate(
            {
                "step_id": f"verify-{index}",
                "source_step_id": f"verify-{index}",
                "timeout_seconds": 10,
                "workload": {"harness": "local-model-proposal", "model": "fixture"},
                "delivery_sha256": "a" * 64,
                "handoff_identity": "original-frozen-handoff",
                **(
                    {
                        "task_summary": "Original bounded goal",
                        "task_sha256": "b" * 64,
                        "task_omitted_characters": 23,
                        "task_redacted": True,
                    }
                    if agent_metadata
                    else {}
                ),
            }
        )
        if failure_kind is not None or agent_metadata
        else ResolvedCommandStep.model_validate(
            {
                "step_id": f"verify-{index}",
                "argv": ("native-verifier",),
                "timeout_seconds": 10,
                **(
                    {
                        "on_failure": "continue",
                        "regression_failure": {
                            "exit_codes": (1,),
                            "feedback": "diagnostic",
                            "reason": "Verifier code 1 denotes code failure",
                        },
                        "outputs": (
                            {
                                "name": "diagnostic",
                                "stream": "stdout",
                                "max_bytes": 2048,
                                "retain": True,
                            },
                        ),
                    }
                    if recoverable
                    else {}
                ),
            }
        )
        for index in range(count)
    )
    plan = ResolvedFleetPlan.create(
        name="Feedback fixture",
        description="An already accepted native result",
        execution=MaintenanceExecutionPolicy(),
        steps=declared,
    )
    target = (
        FleetPlanTarget(
            kind="fleet_plan",
            plan=plan,
            source=PlanFileSource(sha256="0" * 64),
            identity=structural_execution_identity(plan),
            agent_deliveries=tuple(
                AgentStepDeliveryReference(
                    step_id=step.step_id,
                    handoff_identity=step.handoff_identity,
                    delivery_sha256=step.delivery_sha256,
                    reference_id=f"delivery-{step.step_id}",
                )
                for step in declared
                if isinstance(step, ResolvedAgentStep)
            ),
        )
        if failure_kind is not None or agent_metadata
        else structural_fleet_plan_target(plan)
    )
    if recoverable:
        target = target.model_copy(
            update={
                "agent_repair": AgentRepairContext(
                    repair_id="original-run",
                    attempt=1,
                    max_attempts=6,
                    limits=FleetPlanRunLimits(
                        deadline_at=_NOW + timedelta(seconds=60), step_timeout_seconds=10.0
                    ),
                    phase="regression",
                    review_pass=1,
                    original_execution_digest="original-execution",
                    candidate_identity="original-candidate",
                    scope_identity="original-scope",
                    write_roots=("code.py",),
                )
            }
        )
    repository = RepositoryIdentity(name="fixture", path=Path("/fixture/source"))
    output_bytes = b"native failure\n"
    output = PlanOutputEvidence(
        sha256=hashlib.sha256(output_bytes).hexdigest(), byte_count=len(output_bytes), line_count=1
    )
    executions = []
    for index, selected in enumerate(declared):
        failed = index == failed_index
        accepted = (
            settlement
            if failed and settlement is not None
            else PlanStepSettlementEvidence(
                outcome="FAILED" if failed else "SUCCESS",
                cleanup="VERIFIED",
                reason="FAILED_EXIT" if failed else "ACCEPTED_EXIT",
                observed_returncode=failed_exit if failed else 0,
            )
        )
        execution_type = FailedPlanStepExecution if failed else CompletedPlanStepExecution
        executions.append(
            execution_type.model_validate(
                {
                    "step_id": selected.step_id,
                    "kind": selected.kind,
                    "argv": getattr(selected, "argv", ()),
                    "returncode": failed_exit if failed else 0,
                    "stdout_evidence": output,
                    "stderr_evidence": output,
                    "repository_state_before": "candidate",
                    "repository_state_after": "candidate",
                    "settlement": accepted,
                    **(
                        {"failure_kind": failure_kind}
                        if failed and failure_kind is not None
                        else {}
                    ),
                    "attempts": (
                        PlanStepAttemptEvidence(
                            attempt=1,
                            physical_execution_id=physical_id,
                            returncode=failed_exit if failed else 0,
                            observed_returncode=accepted.observed_returncode,
                            stdout_evidence=output,
                            stderr_evidence=output,
                            repository_state_before="candidate",
                            repository_state_after="candidate",
                            egress=PlanEgressEvidence(mode="none"),
                            supervisor=PlanExecutionSupervisorEvidence(
                                final_process_group_quiescent=True, forced_termination=False
                            ),
                        ),
                    ),
                    "outputs": (
                        PublishedPlanOutputEvidence(
                            name="diagnostic",
                            sha256=output.sha256,
                            byte_count=output.byte_count,
                            retained_stream=PlanRetainedStreamOutputEvidence(
                                text="native failure",
                                sha256=hashlib.sha256(b"native failure").hexdigest(),
                                byte_count=len(b"native failure"),
                                source=output,
                                physical_execution_id=physical_id,
                            ),
                        ),
                    )
                    if recoverable
                    else (),
                }
            )
        )
    failed = failed_index >= 0
    run = CompletedFleetRun(
        fleet_run_id="original-run",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope({repository.repository_id}),
        targets=(repository,),
        status=FleetRunStatus.COMPLETED,
        accepted_at=_NOW,
        started_at=_NOW,
        finished_at=_NOW,
        progress=FleetRunProgress(
            total=1, succeeded=int(not failed), failed=int(failed), needs_attention=int(failed)
        ),
    )
    operation = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        target=target,
        verdict=Verdict.FAILED if failed else Verdict.READY,
        returncode=failed_exit if failed else 0,
        fleet_run_id=run.fleet_run_id,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN", plan=FleetPlanResult(plan_id=plan.plan_id, steps=tuple(executions))
        ),
    )
    return FleetRunDetail(
        journal_revision=17,
        run=run,
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, run.targets),
        operations=(operation,),
    )


def _with_shards(detail: FleetRunDetail, members: tuple[str, ...]) -> FleetRunDetail:
    evidence = detail.operations[0].evidence
    assert isinstance(evidence, FleetPlanOperationEvidence) and evidence.plan is not None
    shard = CanonicalShardExecutionEvidence(
        plan_identity=_HASH,
        repository_generation_identity=_HASH,
        execution_context_identity=_HASH,
        execution_policy_identity=_HASH,
        producer_contract_schema="producer-contract",
        producer_version="1.0",
        producer_artifact_identity=_HASH,
        producer_implementation_identity=_HASH,
        producer_contract_identity=_HASH,
        repository_content_identity="captured-content",
        verification_selection_evidence_identity=_HASH,
        selected_membership_identity=_HASH,
        verification_revision_identity=_HASH,
        selected_members=members,
        actual_executed_membership_identity=_HASH,
        actual_executed_members=members[:1],
        membership_complete=False,
        unproven_owner_members=members[:1],
        results=(
            CanonicalShardResultEvidence(
                shard_id="original-shard",
                described_work_identity=_HASH,
                physical_work_identity=_HASH,
                status="ENVIRONMENT_BLOCKED",
                members=members[:1],
            ),
        ),
    )
    operation = detail.operations[0].model_copy(
        update={
            "evidence": evidence.model_copy(
                update={"plan": evidence.plan.model_copy(update={"shard_execution": shard})}
            )
        }
    )
    return detail.model_copy(update={"operations": (operation,)})


def test_known_code_failure_carries_only_exact_accepted_bounded_feedback():
    detail = _detail(recoverable=True)
    feedback = detail.feedback
    row = feedback.steps[0]
    assert feedback.fleet_run_id == "original-run" and feedback.journal_revision == 17
    assert row.diagnostic.classification == "known_code_failure"
    assert row.diagnostic.owner == "agent_campaign_settlement"
    assert row.attempts[0].physical_execution_id == "original-physical-execution"
    assert row.retained_diagnostics[0].text == "native failure"
    assert row.retained_diagnostics[0].authority == "untrusted_recorded_data"
    assert feedback.campaign is not None
    assert feedback.campaign.run_candidate_identity == "original-candidate"
    assert feedback.campaign.current_candidate_identity is None
    assert feedback.budgets is not None and feedback.budgets.remaining_runs is None
    assert feedback.budgets.deadline_at == _NOW + timedelta(seconds=60)
    assert feedback.constraints.write_roots == ("code.py",)
    assert feedback.recovery_action == "none"
    assert project_run_feedback(detail) == feedback


@pytest.mark.parametrize(
    "settlement,classification",
    [
        (
            PlanStepSettlementEvidence(
                outcome="FAILED", cleanup="VERIFIED", reason="FAILED_EXIT", observed_returncode=1
            ),
            "unknown_execution",
        ),
        (
            PlanStepSettlementEvidence(
                outcome="FAILED", cleanup="VERIFIED", reason="FAILED_TO_START"
            ),
            "environment_or_launch_failure",
        ),
        (
            PlanStepSettlementEvidence(
                outcome="TIMED_OUT", cleanup="VERIFIED", reason="DEADLINE_EXPIRED"
            ),
            "timed_out",
        ),
        (
            PlanStepSettlementEvidence(
                outcome="CANCELLED", cleanup="VERIFIED", reason="CALLER_CANCELLED"
            ),
            "cancelled",
        ),
        (
            PlanStepSettlementEvidence(
                outcome="INCOMPLETE", cleanup="UNVERIFIED", reason="CLEANUP_UNVERIFIED"
            ),
            "unknown_execution",
        ),
        (
            PlanStepSettlementEvidence(
                outcome="OUTPUT_LIMITED", cleanup="VERIFIED", reason="OUTPUT_LIMIT_EXCEEDED"
            ),
            "unknown_execution",
        ),
    ],
)
def test_diagnostics_use_settlement_without_guessing_from_exit_or_output(
    settlement, classification
):
    feedback = _detail(settlement=settlement).feedback
    assert feedback.steps[0].diagnostic.classification == classification
    assert feedback.steps[0].retained_diagnostics == ()
    assert feedback.steps[0].diagnostic.reason == settlement.reason


def test_unsettled_or_wrong_run_report_cannot_supply_code_failure_or_recovery():
    detail = _detail(recoverable=True)
    operation = detail.operations[0]
    wrong = operation.model_copy(
        update={
            "provenance": operation.provenance.model_copy(update={"fleet_run_id": "another-run"})
        }
    )
    feedback = detail.model_copy(update={"operations": (wrong,)}).feedback
    assert feedback.steps[0].diagnostic.classification == "unknown_execution"
    assert feedback.steps[0].retained_diagnostics == ()
    assert feedback.recovery_action == "open-recovery"


def test_typed_model_truncation_does_not_reclassify_plain_exit_three():
    # The provider owner adds this typed fact; this projection reads no stderr.
    detail = _detail(failure_kind="model_output_truncated", failed_exit=3)
    assert detail.feedback.steps[0].diagnostic.classification == "model_output_truncated"
    assert _detail(failed_exit=3).feedback.steps[0].diagnostic.classification == "unknown_execution"


def test_long_successful_prefix_cannot_hide_failed_step_and_omissions_are_explicit():
    feedback = _detail(count=20, failed_index=19).feedback
    assert feedback.steps[0].step_id == "verify-19"
    assert len(feedback.steps) == 16 and feedback.omissions.steps == 4
    assert feedback.steps[0].step_execution_id


def test_verification_preserves_exact_membership_actual_results_and_unproven_owners():
    detail = _with_shards(_detail(), ("first", "second"))
    feedback = detail.feedback
    row = feedback.verification[0]
    assert row.evidence == detail.game_tape.repositories[0].verification_execution
    assert row.evidence is not None
    assert row.evidence.selected_members == ("first", "second")
    assert row.evidence.actual_executed_members == ("first",)
    assert not row.evidence.membership_complete
    assert row.evidence.unproven_owner_members == ("first",)
    assert row.results[0].status == "ENVIRONMENT_BLOCKED"
    assert row.qualification == "original_producer_qualifications"
    assert feedback.diagnostic.classification == "verification_unavailable"
    assert feedback.diagnostic.owner == "fleet_shard_execution"


def test_large_flat_selection_is_omitted_whole_and_never_requalified():
    members = tuple(f"member-{index}-" + "x" * 100 for index in range(500))
    feedback = _with_shards(_detail(), members).feedback
    row = feedback.verification[0]
    assert row.evidence is None and row.results == ()
    assert row.selected_count == row.omitted_selected_members == 500
    assert row.actual_count == row.omitted_actual_members == 1
    assert row.qualification == "omitted_not_qualified"
    assert row.selection_evidence_identity == _HASH
    assert len(feedback.model_dump_json(by_alias=True).encode()) <= MAX_RUN_FEEDBACK_BYTES


def test_whole_response_budget_omits_oversize_evidence_without_truncating_identity():
    feedback = _detail(physical_id="physical-" + "x" * MAX_RUN_FEEDBACK_BYTES).feedback
    assert feedback.steps == () and feedback.omissions.steps == 1
    assert feedback.omissions.response_limit_applied
    assert len(feedback.model_dump_json(by_alias=True).encode()) <= MAX_RUN_FEEDBACK_BYTES


def test_active_run_never_turns_retained_report_into_recovery_permission():
    detail = _detail(recoverable=True)
    run = RunningFleetRun.model_validate(
        {
            **detail.run.model_dump(exclude={"finished_at"}, exclude_computed_fields=True),
            "status": FleetRunStatus.RUNNING,
        }
    )
    feedback = detail.model_copy(update={"run": run}).feedback
    assert feedback.diagnostic.classification == "active"
    assert feedback.recovery_action == "none"
    assert feedback.steps[0].diagnostic.classification != "known_code_failure"
    assert feedback.steps[0].retained_diagnostics == ()


def test_original_goal_identity_redaction_and_missing_legacy_metadata_remain_explicit():
    goal = _detail(agent_metadata=True).feedback.goals[0]
    assert goal.summary == "Original bounded goal" and goal.redacted
    assert goal.sha256 == "b" * 64 and goal.omitted_characters == 23
    assert goal.handoff_identity == "original-frozen-handoff"
    assert goal.delivery_sha256 == "a" * 64
    legacy = _detail(failure_kind="model_output_truncated").feedback.goals[0]
    assert legacy.availability == "unavailable" and legacy.summary is None


def test_old_run_candidate_stays_distinct_from_latest_recorded_campaign_candidate_and_budget():
    detail = _detail(recoverable=True)
    intent = AgentCampaignIntent(
        campaign_id="original-run",
        repository_id=detail.run.targets[0].repository_id,
        original_execution_digest="original-execution",
        budget=AgentRepairBudget(max_attempts=6, timeout_seconds=60.0, step_timeout_seconds=10.0),
        maximum_runs=6,
        deadline_at=_NOW + timedelta(seconds=60),
        initial_phase="regression",
        initial_step_ids=("verify-0",),
        candidate_identity="original-candidate",
        scope_identity="original-scope",
        write_roots=("code.py",),
    )
    decision = AgentCampaignDecision(
        campaign_id="original-run",
        expected_revision=1,
        decision_id="recorded-decision",
        phase_ordinal=2,
        remaining_runs=4,
        candidate_identity="later-recorded-candidate",
        scope_identity="later-recorded-scope",
        next_phase="review",
        next_step_ids=("review",),
    )
    campaign = AgentCampaignReadback(
        campaign_id="original-run",
        journal_revision=17,
        records=(
            AgentCampaignRecord(session_id="original-session", revision=1, fact=intent),
            AgentCampaignRecord(session_id="original-session", revision=2, fact=decision),
        ),
    )
    feedback = detail.model_copy(update={"campaign_journal": campaign}).feedback
    assert feedback.campaign is not None and feedback.budgets is not None
    assert feedback.campaign.run_candidate_identity == "original-candidate"
    assert feedback.campaign.current_candidate_identity == "later-recorded-candidate"
    assert feedback.campaign.run_scope_identity == "original-scope"
    assert feedback.campaign.scope_identity == "later-recorded-scope"
    assert feedback.campaign.candidate_freshness == "recorded_not_live"
    assert feedback.budgets.remaining_runs == 4
    assert feedback.budgets.remaining_runs_authority == "committed_decision"
    assert feedback.budgets.decision_id == "recorded-decision"
    stale = detail.model_copy(
        update={"campaign_journal": campaign.model_copy(update={"journal_revision": 16})}
    ).feedback
    assert stale.budgets is not None and stale.budgets.remaining_runs is None


def test_overlong_original_run_identity_is_hashed_and_omitted_everywhere_within_response_bound():
    detail = _detail()
    run_id = "original-" + "x" * MAX_RUN_FEEDBACK_BYTES
    feedback = detail.model_copy(
        update={"run": detail.run.model_copy(update={"fleet_run_id": run_id})}
    ).feedback
    assert feedback.fleet_run_id is None and "fleet_run_id" in feedback.omissions.fields
    assert feedback.fleet_run_id_sha256 == hashlib.sha256(run_id.encode()).hexdigest()
    assert all(reference.fleet_run_id is None for reference in feedback.readback)
    assert len(feedback.model_dump_json(by_alias=True).encode()) <= MAX_RUN_FEEDBACK_BYTES


def test_unsettled_cleanup_precedes_an_ordinary_settled_failure_in_summary():
    detail = _detail(count=2)
    operation = detail.operations[0]
    evidence = operation.evidence
    assert isinstance(evidence, FleetPlanOperationEvidence) and evidence.plan is not None
    incomplete = FailedPlanStepExecution.model_validate(
        {
            **evidence.plan.steps[1].model_dump(exclude_computed_fields=True),
            "status": "FAILED",
            "returncode": 2,
            "settlement": PlanStepSettlementEvidence(
                outcome="INCOMPLETE", cleanup="UNVERIFIED", reason="CLEANUP_UNVERIFIED"
            ),
        }
    )
    operation = operation.model_copy(
        update={
            "evidence": evidence.model_copy(
                update={
                    "plan": evidence.plan.model_copy(
                        update={"steps": (evidence.plan.steps[0], incomplete)}
                    ),
                }
            )
        }
    )
    feedback = detail.model_copy(update={"operations": (operation,)}).feedback
    assert feedback.steps[0].step_id == "verify-1"
    assert feedback.diagnostic.reason == "CLEANUP_UNVERIFIED"
    assert feedback.recovery_action == "open-recovery"


def test_terminal_ordinary_success_keeps_existing_run_history_route():
    detail = _detail(failed_index=-1)
    assert detail.rerun_supported
    assert detail.feedback.recovery_action == "run-same-target"


def test_terminal_unknown_execution_cannot_use_the_ordinary_run_history_route():
    detail = _detail(
        settlement=PlanStepSettlementEvidence(
            outcome="INCOMPLETE",
            cleanup="UNVERIFIED",
            reason="SUPERVISION_UNAVAILABLE",
        )
    )
    assert detail.rerun_supported
    assert detail.feedback.recovery_action == "open-recovery"


def test_typed_not_started_followers_do_not_hide_the_original_provider_failure():
    detail = _detail(count=2, failure_kind="model_output_truncated", failed_exit=3)
    operation = detail.operations[0]
    evidence = operation.evidence
    assert isinstance(evidence, FleetPlanOperationEvidence) and evidence.plan is not None
    skipped = SkippedPlanStepExecution.model_validate(
        {
            **evidence.plan.steps[1].model_dump(
                exclude={"returncode"}, exclude_computed_fields=True
            ),
            "status": "SKIPPED",
            "reason": "Prior Step failed",
            "attempts": (),
            "settlement": PlanStepSettlementEvidence(outcome="SKIPPED", reason="NOT_STARTED"),
        }
    )
    operation = operation.model_copy(
        update={
            "evidence": evidence.model_copy(
                update={
                    "plan": evidence.plan.model_copy(
                        update={"steps": (evidence.plan.steps[0], skipped)}
                    ),
                }
            )
        }
    )
    feedback = detail.model_copy(update={"operations": (operation,)}).feedback
    assert feedback.diagnostic.classification == "model_output_truncated"
    assert feedback.steps[0].step_id == "verify-0"
    assert feedback.steps[1].diagnostic.classification == "skipped"
    assert feedback.steps[1].settlement.cleanup == "UNAVAILABLE"


def _ordinary_status_detail() -> FleetRunDetail:
    base = _detail(failed_index=-1)
    run = CompletedFleetRun.model_validate(
        {
            **base.run.model_dump(exclude_computed_fields=True),
            "target": None,
            "capability": capability_snapshot(OperationAction.STATUS, None),
        }
    )
    return FleetRunDetail(
        journal_revision=17,
        run=run,
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
        operations=(
            make_operation_report(
                run.targets[0],
                OperationAction.STATUS,
                fleet_run_id=run.fleet_run_id,
            ),
        ),
    )


def test_ordinary_status_success_uses_accepted_operation_without_inventing_plan_steps():
    detail = _ordinary_status_detail()
    assert detail.game_tape.repositories[0].steps == ()
    assert detail.feedback.diagnostic.classification == "success"
    assert detail.feedback.diagnostic.reason == "ACCEPTED_OPERATION_RESULTS"
    assert detail.feedback.recovery_action == "run-same-target"


@pytest.mark.parametrize("invalid_result", ["foreign", "missing", "failed"])
def test_missing_or_unaccepted_non_plan_result_never_claims_success_or_a_fresh_run_route(
    invalid_result,
):
    detail = _ordinary_status_detail()
    operation = detail.operations[0]
    if invalid_result == "foreign":
        operation = operation.model_copy(
            update={
                "provenance": operation.provenance.model_copy(
                    update={"fleet_run_id": "another-run"},
                )
            }
        )
    elif invalid_result == "failed":
        operation = make_operation_report(
            detail.run.targets[0],
            OperationAction.STATUS,
            verdict=Verdict.FAILED,
            fleet_run_id=detail.run.fleet_run_id,
            returncode=1,
        )
    feedback = detail.model_copy(
        update={"operations": () if invalid_result == "missing" else (operation,)}
    ).feedback
    assert feedback.diagnostic.classification == "unknown_execution"
    assert feedback.recovery_action == "open-recovery"
