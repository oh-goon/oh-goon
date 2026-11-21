"""Source-bound recovery reuses the existing review/adjudication/repair graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Never

import pytest

from oh_goon.core.agent_campaign_settlement import regression_recovery
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_repair import AgentRepairContext, AgentRepairStopReason
from oh_goon.core.models.agent_review import (
    AgentAdjudicationRecord,
    AgentReviewRecord,
    FindingDisposition,
    FindingReference,
    RepairQueueEntry,
    ReviewEvidence,
    ReviewFinding,
)
from oh_goon.core.models.fleet_observation import AgentReviewObservation
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedCommandStep
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionSupervisorEvidence,
    PlanOutputEvidence,
    PlanRetainedStreamOutputEvidence,
    PlanStepAttemptEvidence,
    PlanStepExecution,
    PlanStepSettlementEvidence,
    PublishedPlanOutputEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import FleetPlanRunLimits
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.product_identity import repository_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction, Verdict
from oh_goon.services.fleet_plan_repair import AgentReviewRepairCampaign
from oh_goon.services.fleet_plan_resolution_context import AgentCaptureOptions
from tests.factories.agent_review import agent_review_case
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for

type ReviewRecord = AgentReviewRecord | AgentAdjudicationRecord


@dataclass
class _Case:
    campaign: AgentReviewRepairCampaign
    repository: Path
    ordinal: int = 0
    limits: FleetPlanRunLimits = field(
        default_factory=lambda: FleetPlanRunLimits(
            deadline_at=utc_now() + timedelta(seconds=60), step_timeout_seconds=10
        )
    )

    def reviews(self, *, clean: bool = False) -> dict[str, ReviewRecord]:
        records: dict[str, ReviewRecord] = {}
        for selected in self.campaign.plan.steps:
            assert isinstance(selected, ResolvedAgentStep)
            handoff = self.campaign.handoffs[selected.step_id]
            member = next(
                row for row in handoff.source.repository.members if row.path == "code.txt"
            )
            records[selected.step_id] = AgentReviewRecord(
                reviewer=selected.step_id,
                candidate_identity=self.campaign.candidate_identity,
                scope_identity=self.campaign.scope_identity,
                skill_identity=handoff.source.skill.content_identity,
                verdict="CLEAN" if clean else "FINDING",
                summary="Review the current captured code",
                reviewed_paths=tuple(
                    "repo/" + row.path for row in handoff.source.repository.members
                ),
                findings=()
                if clean
                else (
                    ReviewFinding(
                        finding_id="broken-code",
                        explanation="The captured code still needs correction",
                        evidence=(
                            ReviewEvidence(path="repo/code.txt", sha256=member.sha256, line=1),
                        ),
                    ),
                ),
            )
        return records

    def adjudication(self) -> dict[str, ReviewRecord]:
        references = tuple(
            FindingReference(reviewer=review.reviewer, finding_id=finding.finding_id)
            for review in self.campaign.reviews
            for finding in review.findings
        )
        return {
            self.campaign.plan.steps[0].step_id: AgentAdjudicationRecord(
                candidate_identity=self.campaign.candidate_identity,
                scope_identity=self.campaign.scope_identity,
                dispositions=tuple(
                    FindingDisposition(
                        **reference.model_dump(), decision="repair", rationale="Fix code"
                    )
                    for reference in references
                ),
                queue=(
                    RepairQueueEntry(
                        queue_id="q1",
                        findings=references,
                        rationale="One source defect",
                        regression_expectation="The original native regression must pass",
                    ),
                ),
            )
        }

    def detail(
        self,
        records: dict[str, ReviewRecord] | None = None,
        *,
        failed: bool = False,
        retain: bool = True,
    ) -> FleetRunDetail:
        self.ordinal += 1
        campaign, plan = self.campaign, self.campaign.plan
        now = utc_now()
        run_id = "campaign" if self.ordinal == 1 else f"phase-{self.ordinal}"
        context = AgentRepairContext.model_validate(
            dict(
                repair_id="campaign",
                attempt=self.ordinal,
                previous_run_id=None
                if self.ordinal == 1
                else "campaign"
                if self.ordinal == 2
                else f"phase-{self.ordinal - 1}",
                max_attempts=4 * (campaign.passes - 1) + 2,
                phase=campaign.phase,
                review_pass=campaign.review_pass,
                original_execution_digest=campaign.original_digest,
                candidate_identity=campaign.candidate_identity,
                scope_identity=campaign.scope_identity,
                write_roots=campaign.write_roots,
                limits=self.limits,
            )
        )
        target = FleetPlanTarget(
            kind="fleet_plan",
            plan=plan,
            source=PlanFileSource(sha256=plan.configuration.root_source_sha256)
            if plan.configuration is not None and plan.configuration.root_source_sha256 is not None
            else PlanFileSource(sha256="0" * 64),
            identity=structural_execution_identity(plan),
            agent_repair=context,
            agent_deliveries=tuple(
                AgentStepDeliveryReference(
                    step_id=step.step_id,
                    handoff_identity=step.handoff_identity,
                    delivery_sha256=step.delivery_sha256,
                    reference_id=f"fixture-delivery-{index}",
                )
                for index, step in enumerate(plan.steps)
                if isinstance(step, ResolvedAgentStep)
            ),
        )
        repository = RepositoryIdentity(
            repository_id=repository_id(self.repository), name="repo", path=self.repository
        )
        run = CompletedFleetRun(
            fleet_run_id=run_id,
            target=target,
            capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
            repository_scope=fleet_repository_scope({repository.repository_id}),
            targets=(repository,),
            status=FleetRunStatus.COMPLETED,
            accepted_at=now,
            started_at=now,
            finished_at=now,
            progress=FleetRunProgress(
                total=1, succeeded=int(not failed), failed=int(failed), needs_attention=int(failed)
            ),
        )
        steps: list[PlanStepExecution] = []
        empty = PlanOutputEvidence(
            sha256=hashlib.sha256(b"").hexdigest(), byte_count=0, line_count=0
        )
        for selected in plan.steps:
            argv = (
                selected.argv
                if isinstance(selected, ResolvedCommandStep)
                else (selected.kind, selected.step_id)
            )
            raw = b"The current candidate still fails its native test\n" if failed else b""
            stdout = PlanOutputEvidence(
                sha256=hashlib.sha256(raw).hexdigest(),
                byte_count=len(raw),
                line_count=int(bool(raw)),
            )
            outputs: tuple[PublishedPlanOutputEvidence, ...] = ()
            if failed and retain:
                assert (
                    isinstance(selected, ResolvedCommandStep)
                    and selected.regression_failure is not None
                )
                text = raw.decode().strip()
                digest = hashlib.sha256(text.encode()).hexdigest()
                outputs = (
                    PublishedPlanOutputEvidence(
                        name=selected.regression_failure.feedback,
                        sha256=digest,
                        byte_count=len(text.encode()),
                        retained_stream=PlanRetainedStreamOutputEvidence(
                            text=text,
                            sha256=digest,
                            byte_count=len(text.encode()),
                            source=stdout,
                            physical_execution_id="physical-regression",
                        ),
                    ),
                )
            code = int(failed)
            step_type = FailedPlanStepExecution if failed else CompletedPlanStepExecution
            record = None if records is None else records.get(selected.step_id)
            steps.append(
                step_type(
                    step_id=selected.step_id,
                    kind=selected.kind,
                    argv=argv,
                    stdout_evidence=stdout,
                    stderr_evidence=empty,
                    repository_state_before="candidate-state",
                    repository_state_after="candidate-state",
                    returncode=code,
                    outputs=outputs,
                    observations=() if record is None else (AgentReviewObservation(record=record),),
                    attempts=(
                        PlanStepAttemptEvidence(
                            attempt=1,
                            argv=argv,
                            physical_execution_id="physical-regression"
                            if failed
                            else f"physical-{run_id}-{selected.step_id}",
                            returncode=code,
                            observed_returncode=code,
                            stdout_evidence=stdout,
                            stderr_evidence=empty,
                            repository_state_before="candidate-state",
                            repository_state_after="candidate-state",
                            egress=PlanEgressEvidence(mode="none"),
                            supervisor=PlanExecutionSupervisorEvidence(
                                final_process_group_quiescent=True,
                                forced_termination=False,
                                process_group_cleanup_requested=False,
                                cleanup_reason="none",
                                leader_exited_before_group_quiescence=False,
                            ),
                        ),
                    ),
                    settlement=PlanStepSettlementEvidence(
                        outcome="FAILED" if failed else "SUCCESS",
                        cleanup="VERIFIED",
                        reason="FAILED_EXIT" if failed else "ACCEPTED_EXIT",
                        observed_returncode=code,
                    ),
                )
            )
        report = make_operation_report(
            repository,
            OperationAction.EXECUTE_PLAN,
            target=target,
            verdict=Verdict.FAILED if failed else Verdict.READY,
            returncode=int(failed),
            fleet_run_id=run_id,
            operation_id=f"operation-{self.ordinal}",
            evidence=FleetPlanOperationEvidence(
                kind="FLEET_PLAN", plan=FleetPlanResult(plan_id=plan.plan_id, steps=tuple(steps))
            ),
        )
        return FleetRunDetail(
            run=run,
            execution=DirectFleetExecutionAdmission(),
            repository_admissions=repository_admissions_for(target, run.targets),
            operations=(report,),
        )

    def enter_regression(self) -> None:
        assert self.campaign.advance(self.detail(self.reviews())) is None
        assert self.campaign.advance(self.detail(self.adjudication())) is None
        (self.repository / "code.txt").write_text("intermediate repair\n")
        assert self.campaign.advance(self.detail()) is None
        assert self.campaign.phase == "regression"


def _case(tmp_path: Path, *, passes: int = 3) -> _Case:
    # The established fixture owns synthetic Skills/provider configuration; its
    # resolver still constructs and validates the real complete campaign.
    resolver, path, repository, values = agent_review_case(tmp_path)
    options: AgentCaptureOptions = {
        "inputs": values["inputs"],
        "repository_root": values["repository_root"],
        "skill_root": values["skill_root"],
        "allowed_acceptance": values["allowed_acceptance"],
        "connection": values["connection"],
        "extensions_path": None,
        "cancel_requested": None,
        "locked_skills": (),
    }
    plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
    return _Case(
        AgentReviewRepairCampaign(
            plan,
            handoffs,
            resolver=resolver,
            plan_path=path,
            capture_options=options,
            passes=passes,
            write_roots=("code.txt", "regression.txt"),
        ),
        repository,
    )


def _contexts(case: _Case) -> list[dict[str, Any]]:
    return [
        json.loads(step.review_context or "{}")
        for step in case.campaign.plan.steps
        if isinstance(step, ResolvedAgentStep)
    ]


def test_failed_regression_feedback_reaches_fresh_cycle_and_clears_after_changed_repair(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    case.enter_regression()
    failed = case.detail(failed=True)
    expected = regression_recovery(failed)
    assert expected is not None
    assert case.campaign.advance(failed) is None
    assert case.campaign.phase == "review" and case.campaign.review_pass == 2
    assert case.campaign.regression_recovery == expected
    feedback = _contexts(case)[0]["regression_feedback"]
    assert feedback["fleet_run_id"] == failed.run.fleet_run_id
    assert feedback["candidate_identity"] == case.campaign.candidate_identity
    assert feedback["feedback"][0]["text"] == "The current candidate still fails its native test"
    assert case.campaign.advance(case.detail(case.reviews())) is None
    assert case.campaign.regression_recovery is None
    assert all(context["regression_feedback"] == feedback for context in _contexts(case))
    assert case.campaign.advance(case.detail(case.adjudication())) is None
    assert all(context["regression_feedback"] == feedback for context in _contexts(case))
    (case.repository / "code.txt").write_text("final repair\n")
    assert case.campaign.advance(case.detail()) is None
    assert case.campaign.regression_feedback is None
    assert case.campaign.advance(case.detail()) is None
    assert case.campaign.review_pass == 3
    assert all("regression_feedback" not in context for context in _contexts(case))
    assert case.campaign.advance(case.detail(case.reviews(clean=True))) is None
    assert case.campaign.phase == "verification"
    assert case.campaign.advance(case.detail()) is AgentRepairStopReason.CONVERGED
    assert failed.outcome.value == "ATTENTION_REQUIRED"


def test_clean_review_cannot_override_known_failed_regression_on_same_candidate(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    case.enter_regression()
    assert case.campaign.advance(case.detail(failed=True)) is None
    assert (
        case.campaign.advance(case.detail(case.reviews(clean=True)))
        is AgentRepairStopReason.REGRESSION_FAILED
    )
    assert case.campaign.phase == "review"
    assert case.campaign.regression_recovery is None


def test_recovery_keeps_original_pass_budget(tmp_path: Path) -> None:
    case = _case(tmp_path, passes=2)
    case.enter_regression()
    assert case.campaign.advance(case.detail(failed=True)) is None
    assert (
        case.campaign.advance(case.detail(case.reviews()))
        is AgentRepairStopReason.ATTEMPTS_EXHAUSTED
    )
    assert case.campaign.review_pass == 2 and case.campaign.regression_recovery is None


def test_recovery_does_not_repeat_an_unchanged_repair_candidate(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.enter_regression()
    assert case.campaign.advance(case.detail(failed=True)) is None
    assert case.campaign.advance(case.detail(case.reviews())) is None
    assert case.campaign.advance(case.detail(case.adjudication())) is None
    assert case.campaign.advance(case.detail()) is AgentRepairStopReason.NO_PROGRESS
    assert case.campaign.regression_recovery is None


def test_failed_regression_without_retained_feedback_stops_without_recapture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    case.enter_regression()

    def fail_capture(*_args: Any, **_kwargs: Any) -> Never:
        pytest.fail("An ineligible failed regression must stop before another source capture")

    monkeypatch.setattr(case.campaign.resolver, "resolve_frozen_agents", fail_capture)
    assert (
        case.campaign.advance(case.detail(failed=True, retain=False))
        is AgentRepairStopReason.REGRESSION_FAILED
    )
    assert case.campaign.phase == "regression" and case.campaign.review_pass == 1
    assert case.campaign.regression_recovery is None


def test_source_changed_after_failed_regression_cannot_be_readmitted(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.enter_regression()
    detail = case.detail(failed=True)
    (case.repository / "code.txt").write_text("unrelated external edit\n")
    with pytest.raises(FleetPlanInvalid, match="candidate changed"):
        case.campaign.advance(detail)
    assert case.campaign.regression_recovery is None
