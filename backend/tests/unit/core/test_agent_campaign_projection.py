"""Campaign readback preserves linked execution facts and controller uncertainty."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from oh_goon.core.models.agent_repair import (
    AgentCampaignDecision,
    AgentCampaignIntent,
    AgentCampaignPhase,
    AgentCampaignReadback,
    AgentCampaignRecord,
    AgentCampaignRunEvidence,
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
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionSupervisorEvidence,
    PlanOutputEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
    SkippedPlanStepExecution,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    FleetPlanRunLimits,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.projections.agent_campaign import project_agent_campaign
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction, Verdict
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import (
    structural_execution_identity,
    structural_fleet_plan_target,
)
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
    failure_driven=False,
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
        phase=None if failure_driven else phase,
        review_pass=None if failure_driven else review_pass,
        original_execution_digest=None if failure_driven else "original",
        candidate_identity=None if failure_driven else candidate,
        scope_identity=None if failure_driven else "scope",
        write_roots=() if failure_driven else ("code.txt",),
    )
    plan = ResolvedFleetPlan.create(
        name="Campaign projection fixture",
        description="One already admitted phase",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id=phase, argv=("check",), timeout_seconds=10),)
        + (
            (
                ResolvedAgentStep(
                    step_id="agent",
                    timeout_seconds=10,
                    workload={"harness": "local-model-proposal", "model": "fixture"},
                    delivery_sha256="a" * 64,
                    handoff_identity="frozen",
                    source_step_id="agent",
                ),
            )
            if failure_driven
            else ()
        ),
    )
    if failure_driven:
        target = FleetPlanTarget(
            kind="fleet_plan",
            plan=plan,
            source=PlanFileSource(sha256="0" * 64),
            identity=structural_execution_identity(plan),
            agent_repair=context,
            agent_deliveries=(
                AgentStepDeliveryReference(
                    step_id="agent",
                    handoff_identity="frozen",
                    delivery_sha256="a" * 64,
                    reference_id="retained-fixture",
                ),
            ),
        )
    else:
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
        attempts=(
            PlanStepAttemptEvidence(
                attempt=1,
                returncode=exit_code,
                stdout_evidence=output,
                stderr_evidence=output,
                repository_state_before="before",
                repository_state_after="after",
                egress=PlanEgressEvidence(mode="none"),
                supervisor=PlanExecutionSupervisorEvidence(final_process_group_quiescent=True),
            ),
        ),
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS" if exit_code == 0 else "FAILED",
            cleanup="VERIFIED",
            reason="ACCEPTED_EXIT" if exit_code == 0 else "FAILED_EXIT",
            observed_returncode=exit_code,
        ),
    )
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        target=target,
        verdict=Verdict.READY if exit_code == 0 else Verdict.FAILED,
        returncode=exit_code,
        fleet_run_id=run_id,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN",
            plan=FleetPlanResult(
                plan_id=plan.plan_id,
                steps=(step,)
                + (
                    (
                        SkippedPlanStepExecution(
                            step_id="agent",
                            kind="agent",
                            reason="Condition not selected",
                            stdout_evidence=output,
                            stderr_evidence=output,
                            repository_state_before="before",
                            repository_state_after="after",
                        ),
                    )
                    if failure_driven
                    else ()
                ),
            ),
        ),
    )
    return FleetRunDetail(
        run=run,
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, run.targets),
        operations=(report,),
    )


def _receipt(details, stop=AgentRepairStopReason.CONVERGED):
    budget = AgentRepairBudget(max_attempts=2, timeout_seconds=60, step_timeout_seconds=10)
    records = [
        AgentCampaignRecord(
            session_id="session",
            revision=1,
            fact=AgentCampaignIntent(
                campaign_id="campaign",
                repository_id=details[0].run.targets[0].repository_id,
                original_execution_digest="original",
                budget=budget,
                maximum_runs=6,
                initial_phase=details[0].run.target.agent_repair.phase or "repair",
                initial_step_ids=tuple(step.step_id for step in details[0].run.target.plan.steps),
                deadline_at=_LIMITS.deadline_at,
            ),
        )
    ]
    for detail in details:
        target = detail.run.target
        records.append(
            AgentCampaignRecord(
                session_id="session",
                revision=len(records) + 1,
                fact=AgentCampaignPhase(
                    campaign_id="campaign",
                    expected_revision=records[-1].revision,
                    fleet_run_id=detail.run.fleet_run_id,
                    context=target.agent_repair,
                    execution_digest=target.execution_digest,
                    selected_step_ids=tuple(step.step_id for step in target.plan.steps),
                ),
            )
        )
    records.append(
        AgentCampaignRecord(
            session_id="session",
            revision=len(records) + 1,
            fact=AgentCampaignDecision(
                campaign_id="campaign",
                expected_revision=records[-1].revision,
                decision_id="terminal",
                phase_ordinal=len(details),
                remaining_runs=6 - len(details),
                consumed_runs=tuple(
                    AgentCampaignRunEvidence(
                        fleet_run_id=detail.run.fleet_run_id,
                        terminal_sequence=1,
                        operation_ids=tuple(op.provenance.operation_id for op in detail.operations),
                    )
                    for detail in details
                ),
                stop_reason=stop,
                passed_test_step_id=details[-1].run.target.plan.steps[0].step_id
                if stop is AgentRepairStopReason.TEST_PASSED
                else None,
                candidate_identity=details[-1].run.target.agent_repair.candidate_identity,
                scope_identity=details[-1].run.target.agent_repair.scope_identity,
            ),
        )
    )
    return AgentRepairResult(
        repair_id="campaign",
        budget=AgentRepairBudget(max_attempts=2, timeout_seconds=60, step_timeout_seconds=10),
        stop_reason=stop,
        attempts=tuple(details),
        admitted_run_ids=tuple(detail.run.fleet_run_id for detail in details),
        committed_decision_id="terminal",
        journal_revision=42,
        campaign_journal=AgentCampaignReadback(
            campaign_id="campaign", journal_revision=42, records=tuple(records)
        ),
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
    result = project_agent_campaign(
        "campaign", details, campaign=_receipt(details).campaign_journal
    )
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
    result = project_agent_campaign(
        "campaign",
        details,
        journal_revision=42,
        campaign=receipt.campaign_journal if receipt is not None else None,
    )
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert result["incomplete_reasons"]
    assert result["journal_revision"] == 42
    assert result["controller_result"]["availability"] == (
        "unavailable" if stop is None else "journal"
    )


def test_missing_linked_run_and_absent_step_results_remain_incomplete():
    details = (_phase(1, "review"), _phase(3, "verification"))
    result = project_agent_campaign(
        "campaign", details, campaign=_receipt(details).campaign_journal
    )
    assert result["result"] == "incomplete"
    assert any("predecessor" in reason for reason in result["incomplete_reasons"])
    selected = _phase(1, "verification")
    missing = selected.model_copy(update={"operations": ()})
    result = project_agent_campaign(
        "campaign", (missing,), campaign=_receipt((missing,)).campaign_journal
    )
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
    result = project_agent_campaign(
        "campaign", (detail,), campaign=_receipt((detail,)).campaign_journal
    )
    assert result["result"] == "incomplete"
    assert any("terminal" in reason for reason in result["incomplete_reasons"])


def test_failed_verification_reports_native_exit_and_unverified_membership():
    details = (_phase(1, "review"), _phase(2, "verification", exit_code=17))
    result = project_agent_campaign(
        "campaign",
        details,
        campaign=_receipt(details, AgentRepairStopReason.VERIFICATION_FAILED).campaign_journal,
    )
    assert result["result"] == "failed" and result["final_candidate"] is None
    assert result["verification"][0]["recorded_steps"][0]["exit_code"] == 17
    assert result["verification"][0]["unverified_step_ids"] == ["verification"]


def test_linked_run_for_another_repository_cannot_establish_convergence():
    details = (
        _phase(1, "review", record=_reviews("before", clean=True)),
        _phase(2, "verification", repository_path="/fixture/another-repository"),
    )
    result = project_agent_campaign(
        "campaign", details, campaign=_receipt(details).campaign_journal
    )
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
        "campaign",
        details,
        campaign=_receipt(details, AgentRepairStopReason.REVIEW_INCOMPLETE).campaign_journal,
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
        journal = receipt.campaign_journal
        duplicate = journal.records[2].model_copy(update={"revision": 40})
        receipt = replace(
            receipt,
            campaign_journal=journal.model_copy(
                update={"records": (*journal.records[:-1], duplicate, journal.records[-1])}
            ),
        )
    elif contradiction == "missing_attempt":
        journal = receipt.campaign_journal
        terminal = journal.records[-1]
        changed = terminal.model_copy(
            update={
                "fact": terminal.fact.model_copy(
                    update={"consumed_runs": terminal.fact.consumed_runs[:1]}
                )
            }
        )
        receipt = replace(
            receipt,
            campaign_journal=journal.model_copy(
                update={"records": (*journal.records[:-1], changed)}
            ),
        )
    else:
        journal = receipt.campaign_journal
        receipt = replace(
            receipt,
            campaign_journal=journal.model_copy(
                update={
                    "records": (
                        journal.records[0],
                        *reversed(journal.records[1:-1]),
                        journal.records[-1],
                    )
                }
            ),
        )
    result = project_agent_campaign(
        "campaign", details, campaign=receipt.campaign_journal if receipt is not None else None
    )
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert result["controller_result"]["stop_reason"] == "converged"
    assert result["incomplete_reasons"]
    if contradiction in ("failed_result", "different_details"):
        verification = result["verification"][0]
        assert verification["recorded_steps"][0]["exit_code"] == 17
        assert verification["unverified_step_ids"] == ["verification"]


def test_unresolved_cleanup_keeps_next_effect_blocked_in_the_campaign_report():
    detail = _phase(1, "verification")
    operation = detail.operations[0]
    step = operation.evidence.plan.steps[0]
    incomplete = step.model_copy(
        update={
            "settlement": PlanStepSettlementEvidence(
                outcome="INCOMPLETE",
                cleanup="UNVERIFIED",
                reason="CLEANUP_UNVERIFIED",
            )
        }
    )
    operation = operation.model_copy(
        update={
            "evidence": operation.evidence.model_copy(
                update={"plan": operation.evidence.plan.model_copy(update={"steps": (incomplete,)})}
            )
        }
    )
    detail = detail.model_copy(update={"operations": (operation,)})
    result = project_agent_campaign(
        "campaign", (detail,), campaign=_receipt((detail,)).campaign_journal
    )
    assert result["result"] == "incomplete"
    assert result["safe_next_action"] == "inspect_original_runs_and_resolve_ownership"
    assert result["runs"][0]["recorded_steps"][0]["settlement"]["cleanup"] == "UNVERIFIED"


def test_bounded_context_export_accounts_for_excluded_events_and_summary_text():
    observation = NativeAgentObservation(
        agent="codex",
        implementation_identity="codex:sha256:" + "a" * 64,
        profile="service",
        input_identity="input",
        occurrence="review",
        source_identity="before",
        role="review",
        native_event_count=14,
        settled=False,
        failed=True,
        events=(
            *(AgentHarnessObservation(kind="context", summary="x" * 3500) for _ in range(12)),
            AgentHarnessObservation(kind="tool", summary="private-tool-canary"),
            AgentHarnessObservation(kind="agent", summary="private-agent-canary"),
        ),
    )
    detail = _phase(1, "review", exit_code=2, native=observation)
    result = project_agent_campaign("campaign", (detail,))
    exported = result["runs"][0]["recorded_steps"][0]["native_observations"][0]
    assert len(exported["context_observations"]) == 8
    assert exported["excluded_event_count"] == 6
    assert all(
        len(event["summary"]) == 512 and event["omitted_summary_characters"] == 2988
        for event in exported["context_observations"]
    )
    assert "private-tool-canary" not in str(result)
    assert "private-agent-canary" not in str(result)


@pytest.mark.parametrize("field", ["candidate_identity", "scope_identity"])
def test_terminal_candidate_and_scope_must_match_the_original_final_verification(field):
    details = (_phase(1, "review"), _phase(2, "verification"))
    journal = _receipt(details).campaign_journal
    assert project_agent_campaign("campaign", details, campaign=journal)["result"] == "converged"
    terminal = journal.records[-1]
    changed = terminal.model_copy(
        update={"fact": terminal.fact.model_copy(update={field: "stale"})}
    )
    journal = journal.model_copy(update={"records": (*journal.records[:-1], changed)})
    result = project_agent_campaign("campaign", details, campaign=journal)
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert any("verification candidate/scope" in reason for reason in result["incomplete_reasons"])


def test_failure_driven_policy_preserves_historical_test_failure_in_durable_readback():
    details = (
        _phase(1, "test", exit_code=17, failure_driven=True),
        _phase(2, "retest", failure_driven=True),
    )
    receipt = _receipt(details, AgentRepairStopReason.TEST_PASSED)
    result = project_agent_campaign("campaign", details, campaign=receipt.campaign_journal)
    assert result["result"] == "test_passed"
    assert result["controller_result"]["stop_reason"] == "test_passed"
    assert result["runs"][0]["outcome"] == "ATTENTION_REQUIRED"
    assert result["runs"][0]["recorded_steps"][0]["status"] == "FAILED"
    assert result["runs"][0]["recorded_steps"][0]["exit_code"] == 17
    assert result["final_candidate"] is None  # No phased-review convergence claim.
    assert result["controller_result"]["passed_test_step_id"] == "retest"


@pytest.mark.parametrize(
    "contradiction", ["failed", "missing_reference", "different_step", "missing_attempt"]
)
def test_test_passed_decision_cannot_hide_missing_or_failed_native_test(contradiction):
    detail = _phase(
        1, "test", exit_code=17 if contradiction == "failed" else 0, failure_driven=True
    )
    receipt = _receipt((detail,), AgentRepairStopReason.TEST_PASSED)
    journal = receipt.campaign_journal
    if contradiction in {"missing_reference", "different_step"}:
        terminal = journal.records[-1]
        terminal = terminal.model_copy(
            update={
                "fact": terminal.fact.model_copy(
                    update={
                        "passed_test_step_id": None
                        if contradiction == "missing_reference"
                        else "agent"
                    }
                )
            }
        )
        journal = journal.model_copy(update={"records": (*journal.records[:-1], terminal)})
    elif contradiction == "missing_attempt":
        operation = detail.operations[0]
        plan = operation.evidence.plan
        operation = operation.model_copy(
            update={
                "evidence": operation.evidence.model_copy(
                    update={
                        "plan": plan.model_copy(
                            update={
                                "steps": (
                                    plan.steps[0].model_copy(update={"attempts": ()}),
                                    *plan.steps[1:],
                                )
                            }
                        )
                    }
                )
            }
        )
        detail = detail.model_copy(update={"operations": (operation,)})
    result = project_agent_campaign("campaign", (detail,), campaign=journal)
    assert result["result"] == "incomplete"
    assert result["controller_result"]["stop_reason"] == "test_passed"
    assert any("accepted native test" in reason for reason in result["incomplete_reasons"])
    assert result["safe_next_action"] == "inspect_original_runs_and_resolve_ownership"
