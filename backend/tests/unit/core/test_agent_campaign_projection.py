"""Campaign readback preserves linked execution facts and controller uncertainty."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from oh_goon.core.models.agent_repair import (
    AgentRepairBudget,
    AgentRepairContext,
    AgentRepairResult,
    AgentRepairStopReason,
)
from oh_goon.core.models.agent_review import AgentAdjudicationRecord, AgentReviewRecord
from oh_goon.core.models.fleet_observation import (
    AgentHarnessObservation,
    AgentReviewObservation,
    NativeAgentObservation,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanOutputEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    FleetPlanRunLimits,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.projections.agent_campaign import project_agent_campaign
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction, Verdict
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for

_NOW = utc_now()
_LIMITS = FleetPlanRunLimits(deadline_at=_NOW + timedelta(seconds=60), step_timeout_seconds=10)


def _phase(
    ordinal,
    phase,
    *,
    candidate="before",
    record=None,
    review_pass=1,
    exit_code=0,
    repository_path="/fixture/repo",
    native=None,
):
    run_id = "campaign" if ordinal == 1 else f"phase-{ordinal}"
    context = AgentRepairContext(
        repair_id="campaign",
        attempt=ordinal,
        max_attempts=6,
        previous_run_id=None
        if ordinal == 1
        else "campaign"
        if ordinal == 2
        else f"phase-{ordinal - 1}",
        limits=_LIMITS,
        phase=phase,
        review_pass=review_pass,
        original_execution_digest="original",
        candidate_identity=candidate,
        scope_identity="scope",
        write_roots=("code.txt",),
    )
    plan = ResolvedFleetPlan.create(
        name="Campaign projection fixture",
        description="One already admitted phase",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id=phase, argv=("check",), timeout_seconds=10),),
    )
    base = structural_fleet_plan_target(plan)
    target = FleetPlanTarget(**{**base.model_dump(), "agent_repair": context})
    repository = RepositoryIdentity(name="repo", path=Path(repository_path))
    run = CompletedFleetRun(
        fleet_run_id=run_id,
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope({repository.repository_id}),
        targets=(repository,),
        status=FleetRunStatus.COMPLETED,
        accepted_at=_NOW,
        started_at=_NOW,
        finished_at=_NOW,
        progress=FleetRunProgress(
            total=1,
            succeeded=int(exit_code == 0),
            failed=int(exit_code != 0),
            needs_attention=int(exit_code != 0),
        ),
    )
    output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    step_type = CompletedPlanStepExecution if exit_code == 0 else FailedPlanStepExecution
    step = step_type(
        step_id=phase,
        kind="command",
        argv=("check",),
        stdout_evidence=output,
        stderr_evidence=output,
        repository_state_before="before",
        repository_state_after="after",
        observations=(
            *((AgentReviewObservation(record=record),) if record is not None else ()),
            *((native,) if native is not None else ()),
        ),
        returncode=exit_code,
    )
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        target=target,
        verdict=Verdict.READY if exit_code == 0 else Verdict.FAILED,
        returncode=exit_code,
        fleet_run_id=run_id,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN", plan=FleetPlanResult(plan_id=plan.plan_id, steps=(step,))
        ),
    )
    return FleetRunDetail(
        run=run,
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, run.targets),
        operations=(report,),
    )


def _receipt(details, stop=AgentRepairStopReason.CONVERGED):
    return AgentRepairResult(
        repair_id="campaign",
        budget=AgentRepairBudget(max_attempts=2, timeout_seconds=60, step_timeout_seconds=10),
        stop_reason=stop,
        attempts=tuple(details),
        admitted_run_ids=tuple(detail.run.fleet_run_id for detail in details),
    )


def _reviews(candidate, *, clean=False):
    return AgentReviewRecord(
        reviewer="review",
        candidate_identity=candidate,
        scope_identity="scope",
        skill_identity="skill",
        verdict="CLEAN" if clean else "FINDING",
        summary="Reviewed exact fixture source",
        reviewed_paths=("repo/code.txt",),
        findings=()
        if clean
        else (
            {
                "finding_id": "defect",
                "explanation": "The fixture has a defect",
                "evidence": ({"path": "repo/code.txt", "sha256": "a" * 64, "line": 1},),
            },
        ),
    )


def test_campaign_projection_correlates_findings_candidates_and_native_results():
    adjudication = AgentAdjudicationRecord(
        candidate_identity="before",
        scope_identity="scope",
        dispositions=(
            {
                "reviewer": "review",
                "finding_id": "defect",
                "decision": "repair",
                "rationale": "Confirmed",
            },
        ),
        queue=(
            {
                "queue_id": "repair-1",
                "findings": ({"reviewer": "review", "finding_id": "defect"},),
                "rationale": "One repair",
                "regression_expectation": "check passes",
            },
        ),
    )
    adjudication.verify((_reviews("before"),))
    details = (
        _phase(1, "review", record=_reviews("before")),
        _phase(2, "adjudicate", record=adjudication),
        _phase(3, "repair"),
        _phase(4, "regression", candidate="after"),
        _phase(5, "review", candidate="after", review_pass=2, record=_reviews("after", clean=True)),
        _phase(6, "verification", candidate="after", review_pass=2),
    )
    result = project_agent_campaign("campaign", details, invocation=_receipt(details))
    assert result["result"] == "converged" and result["incomplete_reasons"] == []
    assert (result["initial_candidate"], result["final_candidate"]) == ("before", "after")
    finding = result["findings"][0]
    assert finding["fleet_run_id"] == "campaign" and finding["step_id"] == "review"
    assert finding["evidence"] == [{"path": "repo/code.txt", "sha256": "a" * 64, "line": 1}]
    assert finding["adjudication"]["fleet_run_id"] == "phase-2"
    assert finding["resolution_status"] == "unverified"
    assert len(result["repair_attempts"]) == 1
    assert result["repair_attempts"][0]["candidate_after"] == "after"
    assert result["verification"][-1]["selected_step_ids"] == ["verification"]
    assert result["verification"][-1]["recorded_steps"][0]["exit_code"] == 0
    assert result["verification"][-1]["unverified_step_ids"] == []


@pytest.mark.parametrize(
    "stop", [None, AgentRepairStopReason.SOURCE_CHANGED, AgentRepairStopReason.CANCELLED]
)
def test_successful_final_run_does_not_manufacture_controller_convergence(stop):
    details = (
        _phase(1, "review", record=_reviews("before", clean=True)),
        _phase(2, "verification"),
    )
    receipt = None if stop is None else _receipt(details, stop)
    result = project_agent_campaign("campaign", details, journal_revision=42, invocation=receipt)
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert result["incomplete_reasons"]
    assert result["journal_revision"] == 42
    assert result["controller_result"]["availability"] == (
        "unavailable" if stop is None else "invocation_receipt"
    )


def test_missing_linked_run_and_absent_step_results_remain_incomplete():
    details = (_phase(1, "review"), _phase(3, "verification"))
    result = project_agent_campaign("campaign", details, invocation=_receipt(details))
    assert result["result"] == "incomplete"
    assert any("predecessor" in reason for reason in result["incomplete_reasons"])
    selected = _phase(1, "verification")
    missing = selected.model_copy(update={"operations": ()})
    result = project_agent_campaign("campaign", (missing,), invocation=_receipt((missing,)))
    assert result["result"] == "incomplete"
    assert result["verification"][0]["unverified_step_ids"] == ["verification"]


def test_nonterminal_run_is_incomplete_even_with_successful_step_claims():
    from oh_goon.core.models.run import RunningFleetRun

    detail = _phase(1, "review")
    running = RunningFleetRun(
        **{
            **detail.run.model_dump(exclude={"finished_at"}, exclude_computed_fields=True),
            "status": FleetRunStatus.RUNNING,
        }
    )
    detail = FleetRunDetail(**{**detail.model_dump(exclude_computed_fields=True), "run": running})
    result = project_agent_campaign("campaign", (detail,), invocation=_receipt((detail,)))
    assert result["result"] == "incomplete"
    assert any("terminal" in reason for reason in result["incomplete_reasons"])


def test_failed_verification_reports_native_exit_and_unverified_membership():
    details = (_phase(1, "review"), _phase(2, "verification", exit_code=17))
    result = project_agent_campaign(
        "campaign", details, invocation=_receipt(details, AgentRepairStopReason.VERIFICATION_FAILED)
    )
    assert result["result"] == "failed" and result["final_candidate"] is None
    assert result["verification"][0]["recorded_steps"][0]["exit_code"] == 17
    assert result["verification"][0]["unverified_step_ids"] == ["verification"]


def test_linked_run_for_another_repository_cannot_establish_convergence():
    details = (
        _phase(1, "review", record=_reviews("before", clean=True)),
        _phase(2, "verification", repository_path="/fixture/another-repository"),
    )
    result = project_agent_campaign("campaign", details, invocation=_receipt(details))
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert any("repository scope" in reason for reason in result["incomplete_reasons"])


def test_failed_native_preflight_exposes_context_without_raw_conversation():
    context = "Controller failure diagnostic: protected execution requires an empty MCP catalog"
    observation = NativeAgentObservation(
        agent="codex",
        implementation_identity="codex:sha256:" + "a" * 64,
        profile="service",
        input_identity="input",
        occurrence="review",
        source_identity="before",
        role="review",
        native_event_count=0,
        settled=False,
        failed=True,
        events=(
            AgentHarnessObservation(kind="context", summary=context),
            AgentHarnessObservation(kind="tool", summary="Unrequested raw tool output"),
            AgentHarnessObservation(kind="agent", summary="Unrequested model message"),
        ),
    )
    details = (_phase(1, "review", exit_code=126, native=observation),)
    result = project_agent_campaign(
        "campaign", details, invocation=_receipt(details, AgentRepairStopReason.REVIEW_INCOMPLETE)
    )
    assert result["result"] == "incomplete"
    native = result["runs"][0]["recorded_steps"][0]["native_observations"][0]
    assert native["failed"] and not native["settled"]
    assert native["authority"] == "UNTRUSTED_HARNESS_CLAIM"
    assert [event["summary"] for event in native["context_observations"]] == [context]
    assert "events" not in native


@pytest.mark.parametrize(
    "contradiction",
    (
        "failed_result",
        "different_details",
        "duplicate_admitted",
        "missing_attempt",
        "reordered_admission",
    ),
)
def test_controller_receipt_cannot_override_retained_results_or_exact_run_membership(contradiction):
    details = (
        _phase(1, "review", record=_reviews("before", clean=True)),
        _phase(2, "verification"),
    )
    receipt = _receipt(details)
    if contradiction in ("failed_result", "different_details"):
        details = (details[0], _phase(2, "verification", exit_code=17))
        if contradiction == "failed_result":
            receipt = _receipt(details)
    elif contradiction == "duplicate_admitted":
        receipt = replace(receipt, admitted_run_ids=(*receipt.admitted_run_ids, "phase-2"))
    elif contradiction == "missing_attempt":
        receipt = replace(receipt, attempts=details[:1])
    else:
        receipt = replace(receipt, admitted_run_ids=tuple(reversed(receipt.admitted_run_ids)))
    result = project_agent_campaign("campaign", details, invocation=receipt)
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert result["controller_result"]["stop_reason"] == "converged"
    assert result["incomplete_reasons"]
    if contradiction in ("failed_result", "different_details"):
        verification = result["verification"][0]
        assert verification["recorded_steps"][0]["exit_code"] == 17
        assert verification["unverified_step_ids"] == ["verification"]
