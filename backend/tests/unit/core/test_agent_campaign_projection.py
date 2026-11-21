"""Campaign readback preserves linked execution facts and controller uncertainty."""

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from oh_goon.core.agent_campaign_settlement import (
    regression_feedback_context,
    regression_recovery,
)
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
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport
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


def _target(detail: FleetRunDetail) -> FleetPlanTarget:
    target = detail.run.target
    assert isinstance(target, FleetPlanTarget)
    return target


def _context(detail: FleetRunDetail) -> AgentRepairContext:
    context = _target(detail).agent_repair
    assert context is not None
    return context


def _journal(receipt: AgentRepairResult) -> AgentCampaignReadback:
    campaign = receipt.campaign_journal
    assert campaign is not None
    return campaign


def _evidence(operation: OperationReport) -> FleetPlanOperationEvidence:
    evidence = operation.evidence
    assert isinstance(evidence, FleetPlanOperationEvidence)
    return evidence


def _plan(operation: OperationReport) -> FleetPlanResult:
    plan = _evidence(operation).plan
    assert plan is not None
    return plan


def _phase(
    ordinal: int,
    phase: str,
    *,
    candidate: str = "before",
    record: AgentReviewRecord | AgentAdjudicationRecord | None = None,
    review_pass: int = 1,
    exit_code: int = 0,
    repository_path: str = "/fixture/repo",
    native: NativeAgentObservation | None = None,
    failure_driven: bool = False,
    recoverable: bool = False,
    maximum_runs: int = 6,
    scope: str = "scope",
    write_roots: tuple[str, ...] = ("code.txt",),
    worktree_files_after: tuple[str, ...] = (),
) -> FleetRunDetail:
    run_id = "campaign" if ordinal == 1 else f"phase-{ordinal}"
    context = AgentRepairContext.model_validate(
        {
            "repair_id": "campaign",
            "attempt": ordinal,
            "max_attempts": maximum_runs,
            "previous_run_id": None
            if ordinal == 1
            else "campaign"
            if ordinal == 2
            else f"phase-{ordinal - 1}",
            "limits": _LIMITS,
            "phase": None if failure_driven else phase,
            "review_pass": None if failure_driven else review_pass,
            "original_execution_digest": None if failure_driven else "original",
            "candidate_identity": None if failure_driven else candidate,
            "scope_identity": None if failure_driven else scope,
            "write_roots": () if failure_driven else write_roots,
        }
    )
    plan = ResolvedFleetPlan.create(
        name="Campaign projection fixture",
        description="One already admitted phase",
        execution=MaintenanceExecutionPolicy(),
        steps=(
            ResolvedCommandStep.model_validate(
                {
                    "step_id": phase,
                    "argv": ("check",),
                    "timeout_seconds": 10,
                    **(
                        {
                            "on_failure": "continue",
                            "regression_failure": {
                                "exit_codes": (1,),
                                "feedback": "diagnostic",
                                "reason": "Verifier exit 1 is ordinary code failure",
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
                },
            ),
        )
        + (
            (
                ResolvedAgentStep.model_validate(
                    {
                        "step_id": "agent",
                        "timeout_seconds": 10,
                        "workload": {"harness": "local-model-proposal", "model": "fixture"},
                        "delivery_sha256": "a" * 64,
                        "handoff_identity": "frozen",
                        "source_step_id": "agent",
                    }
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
    repository = RepositoryIdentity.model_validate({"name": "repo", "path": Path(repository_path)})
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
    diagnostic = b"regression failed\n"
    output = (
        PlanOutputEvidence(
            sha256=hashlib.sha256(diagnostic).hexdigest(),
            byte_count=len(diagnostic),
            line_count=1,
        )
        if recoverable
        else PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    )
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
        worktree_files_after=worktree_files_after,
        outputs=(
            PublishedPlanOutputEvidence(
                name="diagnostic",
                sha256=output.sha256,
                byte_count=output.byte_count,
                retained_stream=PlanRetainedStreamOutputEvidence(
                    text=diagnostic.decode().strip(),
                    sha256=hashlib.sha256(diagnostic.strip()).hexdigest(),
                    byte_count=len(diagnostic.strip()),
                    source=output,
                    physical_execution_id="physical-regression",
                ),
            ),
        )
        if recoverable
        else (),
        attempts=(
            PlanStepAttemptEvidence(
                attempt=1,
                returncode=exit_code,
                observed_returncode=exit_code,
                physical_execution_id="physical-regression" if recoverable else None,
                stdout_evidence=output,
                stderr_evidence=output,
                repository_state_before="before",
                repository_state_after="after",
                worktree_files_after=worktree_files_after,
                egress=PlanEgressEvidence(mode="none"),
                supervisor=PlanExecutionSupervisorEvidence(
                    final_process_group_quiescent=True, forced_termination=False
                ),
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


def _receipt(
    details: Sequence[FleetRunDetail],
    stop: AgentRepairStopReason = AgentRepairStopReason.CONVERGED,
) -> AgentRepairResult:
    budget = AgentRepairBudget(
        max_attempts=3 if _context(details[0]).max_attempts > 6 else 2,
        timeout_seconds=60,
        step_timeout_seconds=10,
    )
    records = [
        AgentCampaignRecord(
            session_id="session",
            revision=1,
            fact=AgentCampaignIntent(
                campaign_id="campaign",
                repository_id=details[0].run.targets[0].repository_id,
                original_execution_digest="original",
                budget=budget,
                maximum_runs=_context(details[0]).max_attempts,
                initial_phase=_context(details[0]).phase or "repair",
                initial_step_ids=tuple(step.step_id for step in _target(details[0]).plan.steps),
                deadline_at=_LIMITS.deadline_at,
                candidate_identity=_context(details[0]).candidate_identity,
                scope_identity=_context(details[0]).scope_identity,
                write_roots=_context(details[0]).write_roots,
            ),
        )
    ]
    for index, detail in enumerate(details):
        target = _target(detail)
        assert target.agent_repair is not None
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
        recovery = regression_recovery(detail)
        if recovery is not None and index + 1 < len(details):
            successor = _target(details[index + 1])
            assert successor.agent_repair is not None
            records.append(
                AgentCampaignRecord(
                    session_id="session",
                    revision=len(records) + 1,
                    fact=AgentCampaignDecision(
                        campaign_id="campaign",
                        expected_revision=records[-1].revision,
                        decision_id=f"recover-{index}",
                        phase_ordinal=index + 1,
                        remaining_runs=target.agent_repair.max_attempts - index - 1,
                        consumed_runs=tuple(
                            AgentCampaignRunEvidence(
                                fleet_run_id=item.run.fleet_run_id,
                                terminal_sequence=1,
                                operation_ids=tuple(
                                    op.provenance.operation_id for op in item.operations
                                ),
                            )
                            for item in details[: index + 1]
                        ),
                        candidate_identity=recovery.candidate_identity,
                        scope_identity=recovery.scope_identity,
                        next_phase=successor.agent_repair.phase,
                        next_step_ids=tuple(step.step_id for step in successor.plan.steps),
                        regression_recovery=recovery,
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
                remaining_runs=_context(details[0]).max_attempts - len(details),
                consumed_runs=tuple(
                    AgentCampaignRunEvidence(
                        fleet_run_id=detail.run.fleet_run_id,
                        terminal_sequence=1,
                        operation_ids=tuple(op.provenance.operation_id for op in detail.operations),
                    )
                    for detail in details
                ),
                stop_reason=stop,
                passed_test_step_id=_target(details[-1]).plan.steps[0].step_id
                if stop is AgentRepairStopReason.TEST_PASSED
                else None,
                candidate_identity=_context(details[-1]).candidate_identity,
                scope_identity=_context(details[-1]).scope_identity,
            ),
        )
    )
    return AgentRepairResult(
        repair_id="campaign",
        budget=budget,
        stop_reason=stop,
        attempts=tuple(details),
        admitted_run_ids=tuple(detail.run.fleet_run_id for detail in details),
        committed_decision_id="terminal",
        journal_revision=42,
        campaign_journal=AgentCampaignReadback(
            campaign_id="campaign", journal_revision=42, records=tuple(records)
        ),
    )


def _reviews(
    candidate: str,
    *,
    clean: bool = False,
    scope: str = "scope",
    reviewed_paths: tuple[str, ...] = ("repo/code.txt",),
) -> AgentReviewRecord:
    return AgentReviewRecord.model_validate(
        {
            "reviewer": "review",
            "candidate_identity": candidate,
            "scope_identity": scope,
            "skill_identity": "skill",
            "verdict": "CLEAN" if clean else "FINDING",
            "summary": "Reviewed exact fixture source",
            "reviewed_paths": reviewed_paths,
            "findings": ()
            if clean
            else (
                {
                    "finding_id": "defect",
                    "explanation": "The fixture has a defect",
                    "evidence": ({"path": "repo/code.txt", "sha256": "a" * 64, "line": 1},),
                },
            ),
        }
    )


def test_campaign_projection_correlates_findings_candidates_and_native_results() -> None:
    adjudication = AgentAdjudicationRecord.model_validate(
        {
            "candidate_identity": "before",
            "scope_identity": "scope",
            "dispositions": (
                {
                    "reviewer": "review",
                    "finding_id": "defect",
                    "decision": "repair",
                    "rationale": "Confirmed",
                },
            ),
            "queue": (
                {
                    "queue_id": "repair-1",
                    "findings": ({"reviewer": "review", "finding_id": "defect"},),
                    "rationale": "One repair",
                    "regression_expectation": "check passes",
                },
            ),
        }
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
    result = project_agent_campaign("campaign", details, campaign=_journal(_receipt(details)))
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
def test_successful_final_run_does_not_manufacture_controller_convergence(
    stop: AgentRepairStopReason | None,
) -> None:
    details = (
        _phase(1, "review", record=_reviews("before", clean=True)),
        _phase(2, "verification"),
    )
    receipt = None if stop is None else _receipt(details, stop)
    result = project_agent_campaign(
        "campaign",
        details,
        journal_revision=42,
        campaign=_journal(receipt) if receipt is not None else None,
    )
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert result["incomplete_reasons"]
    assert result["journal_revision"] == 42
    assert result["controller_result"]["availability"] == (
        "unavailable" if stop is None else "journal"
    )


def test_missing_linked_run_and_absent_step_results_remain_incomplete() -> None:
    details = (_phase(1, "review"), _phase(3, "verification"))
    result = project_agent_campaign("campaign", details, campaign=_journal(_receipt(details)))
    assert result["result"] == "incomplete"
    assert any("predecessor" in reason for reason in result["incomplete_reasons"])
    selected = _phase(1, "verification")
    missing = selected.model_copy(update={"operations": ()})
    result = project_agent_campaign("campaign", (missing,), campaign=_journal(_receipt((missing,))))
    assert result["result"] == "incomplete"
    assert result["verification"][0]["unverified_step_ids"] == ["verification"]


def test_nonterminal_run_is_incomplete_even_with_successful_step_claims() -> None:
    from oh_goon.core.models.run import RunningFleetRun

    detail = _phase(1, "review")
    running = RunningFleetRun(
        **{
            **detail.run.model_dump(exclude={"finished_at"}, exclude_computed_fields=True),
            "status": FleetRunStatus.RUNNING,
        }
    )
    detail = FleetRunDetail(**{**detail.model_dump(exclude_computed_fields=True), "run": running})
    result = project_agent_campaign("campaign", (detail,), campaign=_journal(_receipt((detail,))))
    assert result["result"] == "incomplete"
    assert any("terminal" in reason for reason in result["incomplete_reasons"])


def test_failed_verification_reports_native_exit_and_unverified_membership() -> None:
    details = (_phase(1, "review"), _phase(2, "verification", exit_code=17))
    result = project_agent_campaign(
        "campaign",
        details,
        campaign=_journal(_receipt(details, AgentRepairStopReason.VERIFICATION_FAILED)),
    )
    assert result["result"] == "failed" and result["final_candidate"] is None
    assert result["verification"][0]["recorded_steps"][0]["exit_code"] == 17
    assert result["verification"][0]["unverified_step_ids"] == ["verification"]


def _recovered_regression_details(*, added_path: bool = False) -> tuple[FleetRunDetail, ...]:
    roots = ("code.txt", "added.txt") if added_path else ("code.txt",)
    final_scope = "scope-after-created-path" if added_path else "scope"
    adjudication = AgentAdjudicationRecord.model_validate(
        {
            "candidate_identity": "before",
            "scope_identity": "scope",
            "dispositions": (
                {
                    "reviewer": "review",
                    "finding_id": "defect",
                    "decision": "repair",
                    "rationale": "The retained native regression confirms the defect",
                },
            ),
            "queue": (
                {
                    "queue_id": "repair-regression",
                    "findings": ({"reviewer": "review", "finding_id": "defect"},),
                    "rationale": "Repair the failed candidate",
                    "regression_expectation": "The declared verifier passes",
                },
            ),
        }
    )
    return (
        _phase(1, "regression", recoverable=True, exit_code=1, maximum_runs=7, write_roots=roots),
        _phase(
            2, "review", record=_reviews("before"), review_pass=2, maximum_runs=7, write_roots=roots
        ),
        _phase(
            3, "adjudicate", record=adjudication, review_pass=2, maximum_runs=7, write_roots=roots
        ),
        _phase(
            4,
            "repair",
            review_pass=2,
            maximum_runs=7,
            write_roots=roots,
            worktree_files_after=("added.txt",) if added_path else (),
        ),
        _phase(
            5,
            "regression",
            candidate="after",
            review_pass=2,
            maximum_runs=7,
            scope=final_scope,
            write_roots=roots,
        ),
        _phase(
            6,
            "review",
            candidate="after",
            record=_reviews(
                "after",
                clean=True,
                scope=final_scope,
                reviewed_paths=("repo/code.txt", "repo/added.txt")
                if added_path
                else ("repo/code.txt",),
            ),
            review_pass=3,
            maximum_runs=7,
            scope=final_scope,
            write_roots=roots,
        ),
        _phase(
            7,
            "verification",
            candidate="after",
            review_pass=3,
            maximum_runs=7,
            scope=final_scope,
            write_roots=roots,
        ),
    )


@pytest.mark.parametrize("added_path", (False, True))
def test_recovered_regression_converges_without_rewriting_failed_history(added_path: bool) -> None:
    details = _recovered_regression_details(added_path=added_path)
    recovery = regression_recovery(details[0])
    assert recovery is not None
    feedback = regression_feedback_context(details[0], recovery)
    assert feedback["feedback"][0]["text"] == "regression failed"
    assert feedback["feedback"][0]["physical_execution_id"] == "physical-regression"
    receipt = _receipt(details)
    result = project_agent_campaign("campaign", details, campaign=_journal(receipt))
    assert result["result"] == "converged" and result["incomplete_reasons"] == []
    assert result["final_candidate"] == "after"
    assert result["runs"][0]["outcome"] != "SUCCESS"
    assert result["runs"][0]["unverified_step_ids"] == ["regression"]
    assert result["runs"][0]["recorded_steps"][0]["settlement"]["outcome"] == "FAILED"
    assert result["verification"][0]["recorded_steps"][0]["exit_code"] == 1
    if added_path:
        assert result["runs"][3]["scope_identity"] == "scope"
        assert result["runs"][4]["scope_identity"] == "scope-after-created-path"
        assert result["controller_result"]["scope_identity"] == "scope-after-created-path"
        assert "repo/added.txt" in result["reviews"][-1]["reviewed_paths"]


def test_recovery_refuses_scope_change_after_the_successful_regression() -> None:
    details = list(_recovered_regression_details(added_path=True))
    roots = ("code.txt", "added.txt")
    details[5] = _phase(
        6,
        "review",
        candidate="after",
        review_pass=3,
        maximum_runs=7,
        scope="scope-changed-without-repair",
        write_roots=roots,
        record=_reviews(
            "after",
            clean=True,
            scope="scope-changed-without-repair",
            reviewed_paths=("repo/code.txt", "repo/added.txt"),
        ),
    )
    details[6] = _phase(
        7,
        "verification",
        candidate="after",
        review_pass=3,
        maximum_runs=7,
        scope="scope-changed-without-repair",
        write_roots=roots,
    )
    result = project_agent_campaign("campaign", details, campaign=_journal(_receipt(details)))
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert any("phase or predecessor chain" in reason for reason in result["incomplete_reasons"])


@pytest.mark.parametrize("return_to_initial", (False, True))
def test_recovery_cycle_guard_includes_the_initial_pre_failure_candidate(
    return_to_initial: bool,
) -> None:
    prior = _recovered_regression_details()
    observation = _plan(prior[2].operations[0]).steps[0].observations[0]
    assert isinstance(observation, AgentReviewObservation)
    assert isinstance(observation.record, AgentAdjudicationRecord)
    adjudication = observation.record
    final_candidate = "initial" if return_to_initial else "new-repaired-candidate"
    details = (
        _phase(1, "review", candidate="initial", record=_reviews("initial"), maximum_runs=10),
        _phase(
            2,
            "adjudicate",
            candidate="initial",
            maximum_runs=10,
            record=adjudication.model_copy(update={"candidate_identity": "initial"}),
        ),
        _phase(3, "repair", candidate="initial", maximum_runs=10),
        _phase(4, "regression", candidate="before", recoverable=True, exit_code=1, maximum_runs=10),
        _phase(
            5,
            "review",
            candidate="before",
            record=_reviews("before"),
            review_pass=2,
            maximum_runs=10,
        ),
        _phase(
            6, "adjudicate", candidate="before", record=adjudication, review_pass=2, maximum_runs=10
        ),
        _phase(7, "repair", candidate="before", review_pass=2, maximum_runs=10),
        _phase(8, "regression", candidate=final_candidate, review_pass=2, maximum_runs=10),
        _phase(
            9,
            "review",
            candidate=final_candidate,
            review_pass=3,
            maximum_runs=10,
            record=_reviews(final_candidate, clean=True),
        ),
        _phase(10, "verification", candidate=final_candidate, review_pass=3, maximum_runs=10),
    )
    result = project_agent_campaign("campaign", details, campaign=_journal(_receipt(details)))
    if return_to_initial:
        assert result["result"] == "incomplete" and result["final_candidate"] is None
        assert any(
            "repeated an earlier candidate" in reason for reason in result["incomplete_reasons"]
        )
    else:
        assert result["result"] == "converged" and result["incomplete_reasons"] == []
        assert result["final_candidate"] == final_candidate


def test_existing_successful_history_does_not_require_new_recovery_metadata() -> None:
    details = (
        _phase(1, "review", record=_reviews("before", clean=True)),
        _phase(2, "verification"),
    )
    legacy = tuple(
        detail.model_copy(
            update={"operations": (detail.operations[0].model_copy(update={"target": None}),)}
        )
        for detail in details
    )
    result = project_agent_campaign("campaign", legacy, campaign=_journal(_receipt(legacy)))
    assert result["result"] == "converged" and result["incomplete_reasons"] == []


@pytest.mark.parametrize(
    "missing",
    (
        "physical",
        "supervisor",
        "quiescence",
        "observed_exit",
        "feedback",
        "feedback_capacity",
        "category",
        "forced_unknown",
    ),
)
def test_only_complete_declared_native_regression_can_supply_recovery_feedback(
    missing: str,
) -> None:
    detail = _recovered_regression_details()[0]
    assert regression_recovery(detail) is not None
    operation = detail.operations[0]
    step = _plan(operation).steps[0]
    attempt = step.attempts[0]
    assert attempt.supervisor is not None
    if missing == "physical":
        step = step.model_copy(
            update={"attempts": (attempt.model_copy(update={"physical_execution_id": None}),)}
        )
    elif missing == "supervisor":
        step = step.model_copy(
            update={"attempts": (attempt.model_copy(update={"supervisor": None}),)}
        )
    elif missing == "quiescence":
        step = step.model_copy(
            update={
                "attempts": (
                    attempt.model_copy(
                        update={
                            "supervisor": attempt.supervisor.model_copy(
                                update={"final_process_group_quiescent": None}
                            )
                        }
                    ),
                )
            }
        )
    elif missing == "observed_exit":
        step = step.model_copy(
            update={"attempts": (attempt.model_copy(update={"observed_returncode": None}),)}
        )
    elif missing == "forced_unknown":
        step = step.model_copy(
            update={
                "attempts": (
                    attempt.model_copy(
                        update={
                            "supervisor": attempt.supervisor.model_copy(
                                update={"forced_termination": None}
                            )
                        }
                    ),
                )
            }
        )
    elif missing == "feedback":
        output = step.outputs[0]
        assert output.retained_stream is not None
        retained = output.retained_stream.model_copy(
            update={"text": "", "sha256": hashlib.sha256(b"").hexdigest(), "byte_count": 0}
        )
        step = step.model_copy(
            update={"outputs": (output.model_copy(update={"retained_stream": retained}),)}
        )
    else:
        target = _target(detail)
        selected = target.plan.steps[0]
        assert isinstance(selected, ResolvedCommandStep)
        change = (
            {"regression_failure": None}
            if missing == "category"
            else {"outputs": (selected.outputs[0].model_copy(update={"max_bytes": 1}),)}
        )
        target = target.model_copy(
            update={
                "plan": target.plan.model_copy(
                    update={"steps": (selected.model_copy(update=change),)}
                )
            }
        )
        detail = detail.model_copy(update={"run": detail.run.model_copy(update={"target": target})})
        operation = operation.model_copy(update={"target": target})
    detail = detail.model_copy(
        update={
            "operations": (
                operation.model_copy(
                    update={
                        "evidence": _evidence(operation).model_copy(
                            update={"plan": _plan(operation).model_copy(update={"steps": (step,)})}
                        )
                    }
                ),
            )
        }
    )
    assert regression_recovery(detail) is None


@pytest.mark.parametrize("exit_code", (-9, 2, 124, 125, 126, 127))
def test_native_signal_environment_and_reserved_exits_do_not_become_code_recovery(
    exit_code: int,
) -> None:
    detail = _phase(1, "regression", recoverable=True, exit_code=exit_code)
    assert regression_recovery(detail) is None


@pytest.mark.parametrize(
    "contradiction",
    (
        "missing_marker",
        "foreign_operation",
        "foreign_physical",
        "missing_feedback",
        "wrong_raw_source",
        "unsafe_cleanup",
        "forced_termination",
        "wrong_scope",
        "candidate_before_repair",
        "clean_failed_candidate",
        "failed_review",
        "failed_verification",
        "repeated_candidate",
    ),
)
def test_recovery_cannot_hide_unrelated_failure_or_forged_native_evidence(
    contradiction: str,
) -> None:
    details = list(_recovered_regression_details())
    receipt = _receipt(details)
    campaign = _journal(receipt)
    if contradiction in {"missing_marker", "foreign_operation"}:
        marker = campaign.records[2]
        assert isinstance(marker.fact, AgentCampaignDecision)
        assert marker.fact.regression_recovery is not None
        if contradiction == "missing_marker":
            campaign = campaign.model_copy(
                update={"records": (*campaign.records[:2], *campaign.records[3:])}
            )
        else:
            changed = marker.fact.regression_recovery.model_copy(
                update={"operation_id": "foreign-operation"}
            )
            campaign = campaign.model_copy(
                update={
                    "records": (
                        *campaign.records[:2],
                        marker.model_copy(
                            update={
                                "fact": marker.fact.model_copy(
                                    update={"regression_recovery": changed}
                                )
                            }
                        ),
                        *campaign.records[3:],
                    )
                }
            )
    elif contradiction in {
        "foreign_physical",
        "missing_feedback",
        "wrong_raw_source",
        "unsafe_cleanup",
        "forced_termination",
    }:
        original = details[0]
        operation = original.operations[0]
        step = _plan(operation).steps[0]
        if contradiction == "missing_feedback":
            step = step.model_copy(update={"outputs": ()})
        elif contradiction in {"foreign_physical", "wrong_raw_source"}:
            output = step.outputs[0]
            retained = output.retained_stream
            assert retained is not None
            retained = retained.model_copy(
                update={"physical_execution_id": "foreign-physical"}
                if contradiction == "foreign_physical"
                else {"source": retained.source.model_copy(update={"sha256": "f" * 64})}
            )
            step = step.model_copy(
                update={"outputs": (output.model_copy(update={"retained_stream": retained}),)}
            )
        elif contradiction == "unsafe_cleanup":
            step = step.model_copy(
                update={"settlement": step.settlement.model_copy(update={"cleanup": "UNVERIFIED"})}
            )
        else:
            attempt = step.attempts[0]
            assert attempt.supervisor is not None
            step = step.model_copy(
                update={
                    "attempts": (
                        attempt.model_copy(
                            update={
                                "supervisor": attempt.supervisor.model_copy(
                                    update={"forced_termination": True}
                                )
                            }
                        ),
                    )
                }
            )
        details[0] = original.model_copy(
            update={
                "operations": (
                    operation.model_copy(
                        update={
                            "evidence": _evidence(operation).model_copy(
                                update={
                                    "plan": _plan(operation).model_copy(update={"steps": (step,)})
                                }
                            )
                        }
                    ),
                )
            }
        )
    elif contradiction == "wrong_scope":
        detail = details[1]
        target = _target(detail)
        assert target.agent_repair is not None
        details[1] = detail.model_copy(
            update={
                "run": detail.run.model_copy(
                    update={
                        "target": target.model_copy(
                            update={
                                "agent_repair": target.agent_repair.model_copy(
                                    update={"scope_identity": "foreign-scope"}
                                )
                            }
                        )
                    }
                )
            }
        )
    elif contradiction == "candidate_before_repair":
        details[1] = _phase(
            2,
            "review",
            candidate="unadmitted",
            record=_reviews("unadmitted"),
            review_pass=2,
            maximum_runs=7,
        )
    elif contradiction == "clean_failed_candidate":
        details[1] = _phase(
            2, "review", record=_reviews("before", clean=True), review_pass=2, maximum_runs=7
        )
    elif contradiction == "failed_review":
        details[1] = _phase(2, "review", exit_code=1, review_pass=2, maximum_runs=7)
    elif contradiction == "failed_verification":
        details[-1] = _phase(
            7, "verification", candidate="after", exit_code=1, review_pass=3, maximum_runs=7
        )
    else:
        details[4] = _phase(5, "regression", candidate="before", review_pass=2, maximum_runs=7)
    result = project_agent_campaign("campaign", details, campaign=campaign)
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert result["incomplete_reasons"]


def test_linked_run_for_another_repository_cannot_establish_convergence() -> None:
    details = (
        _phase(1, "review", record=_reviews("before", clean=True)),
        _phase(2, "verification", repository_path="/fixture/another-repository"),
    )
    result = project_agent_campaign("campaign", details, campaign=_journal(_receipt(details)))
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert any("repository scope" in reason for reason in result["incomplete_reasons"])


def test_failed_native_preflight_exposes_context_without_raw_conversation() -> None:
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
        campaign=_journal(_receipt(details, AgentRepairStopReason.REVIEW_INCOMPLETE)),
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
def test_controller_receipt_cannot_override_retained_results_or_exact_run_membership(
    contradiction: str,
) -> None:
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
        journal = _journal(receipt)
        duplicate = journal.records[2].model_copy(update={"revision": 40})
        receipt = replace(
            receipt,
            campaign_journal=journal.model_copy(
                update={"records": (*journal.records[:-1], duplicate, journal.records[-1])}
            ),
        )
    elif contradiction == "missing_attempt":
        journal = _journal(receipt)
        terminal = journal.records[-1]
        assert isinstance(terminal.fact, AgentCampaignDecision)
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
        journal = _journal(receipt)
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
        "campaign", details, campaign=_journal(receipt) if receipt is not None else None
    )
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert result["controller_result"]["stop_reason"] == "converged"
    assert result["incomplete_reasons"]
    if contradiction in ("failed_result", "different_details"):
        verification = result["verification"][0]
        assert verification["recorded_steps"][0]["exit_code"] == 17
        assert verification["unverified_step_ids"] == ["verification"]


def test_unresolved_cleanup_keeps_next_effect_blocked_in_the_campaign_report() -> None:
    detail = _phase(1, "verification")
    operation = detail.operations[0]
    step = _plan(operation).steps[0]
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
            "evidence": _evidence(operation).model_copy(
                update={"plan": _plan(operation).model_copy(update={"steps": (incomplete,)})}
            )
        }
    )
    detail = detail.model_copy(update={"operations": (operation,)})
    result = project_agent_campaign("campaign", (detail,), campaign=_journal(_receipt((detail,))))
    assert result["result"] == "incomplete"
    assert result["safe_next_action"] == "inspect_original_runs_and_resolve_ownership"
    assert result["runs"][0]["recorded_steps"][0]["settlement"]["cleanup"] == "UNVERIFIED"


def test_bounded_context_export_accounts_for_excluded_events_and_summary_text() -> None:
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
def test_terminal_candidate_and_scope_must_match_the_original_final_verification(
    field: str,
) -> None:
    details = (_phase(1, "review"), _phase(2, "verification"))
    journal = _journal(_receipt(details))
    assert project_agent_campaign("campaign", details, campaign=journal)["result"] == "converged"
    terminal = journal.records[-1]
    changed = terminal.model_copy(
        update={"fact": terminal.fact.model_copy(update={field: "stale"})}
    )
    journal = journal.model_copy(update={"records": (*journal.records[:-1], changed)})
    result = project_agent_campaign("campaign", details, campaign=journal)
    assert result["result"] == "incomplete" and result["final_candidate"] is None
    assert any("verification candidate/scope" in reason for reason in result["incomplete_reasons"])


def test_failure_driven_policy_preserves_historical_test_failure_in_durable_readback() -> None:
    details = (
        _phase(1, "test", exit_code=17, failure_driven=True),
        _phase(2, "retest", failure_driven=True),
    )
    receipt = _receipt(details, AgentRepairStopReason.TEST_PASSED)
    result = project_agent_campaign("campaign", details, campaign=_journal(receipt))
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
def test_test_passed_decision_cannot_hide_missing_or_failed_native_test(contradiction: str) -> None:
    detail = _phase(
        1, "test", exit_code=17 if contradiction == "failed" else 0, failure_driven=True
    )
    receipt = _receipt((detail,), AgentRepairStopReason.TEST_PASSED)
    journal = _journal(receipt)
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
        plan = _plan(operation)
        operation = operation.model_copy(
            update={
                "evidence": _evidence(operation).model_copy(
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
