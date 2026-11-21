"""Fresh continuation admission fences and original-Run receipt replay."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid, RunNotFound, StateConflict
from oh_goon.core.models.agent_continuation import AgentContinuationRequest, AgentContinuationReview
from oh_goon.core.models.agent_providers import (
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.agent_repair import (
    AgentCampaignDecision,
    AgentCampaignIntent,
    AgentCampaignPhase,
    AgentCampaignReadback,
    AgentCampaignRecord,
    AgentRepairBudget,
    AgentRepairContext,
    AgentRepairStopReason,
)
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionSupervisorEvidence,
    PlanOutputEvidence,
    PlanSnapshotEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    FleetPlanRunLimits,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.projections.run_feedback import _continuation_state
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from oh_goon.services import fleet_plan_continuation as continuation
from tests.factories.fleet_plan_targets import (
    structural_execution_identity,
    structural_fleet_plan_target,
)
from tests.factories.operation_reports import make_operation_report


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    repository = RepositoryIdentity(path=root)
    deadline = utc_now() + timedelta(minutes=1)
    budget = AgentRepairBudget(max_attempts=1, timeout_seconds=60, step_timeout_seconds=10)
    context = AgentRepairContext(
        repair_id="campaign",
        attempt=1,
        max_attempts=1,
        limits=FleetPlanRunLimits(deadline_at=deadline, step_timeout_seconds=10),
        phase="regression",
        review_pass=1,
        original_execution_digest="original",
        candidate_identity="candidate",
        scope_identity="scope",
        write_roots=("source.py",),
    )
    plan = ResolvedFleetPlan.create(
        name="regression",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="check", argv=("check",)),),
    )
    target = structural_fleet_plan_target(plan).model_copy(update={"agent_repair": context})
    output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    step = CompletedPlanStepExecution(
        step_id="check",
        kind="command",
        argv=("check",),
        returncode=0,
        stdout_evidence=output,
        stderr_evidence=output,
        repository_state_before="before",
        repository_state_after="after",
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS",
            cleanup="VERIFIED",
            reason="ACCEPTED_EXIT",
            observed_returncode=0,
        ),
        attempts=(
            PlanStepAttemptEvidence(
                attempt=1,
                returncode=0,
                observed_returncode=0,
                stdout_evidence=output,
                stderr_evidence=output,
                repository_state_before="before",
                repository_state_after="after",
                egress=PlanEgressEvidence(mode="none"),
                supervisor=PlanExecutionSupervisorEvidence(
                    final_process_group_quiescent=True,
                    forced_termination=False,
                ),
            ),
        ),
    )
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        target=target,
        fleet_run_id="campaign",
        operation_id="operation",
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN",
            plan=FleetPlanResult(plan_id=plan.plan_id, steps=(step,)),
        ),
    )
    intent = AgentCampaignIntent(
        campaign_id="campaign",
        repository_id=repository.repository_id,
        original_execution_digest="original",
        budget=budget,
        maximum_runs=1,
        deadline_at=deadline,
        initial_phase="regression",
        initial_step_ids=("check",),
    )
    campaign = AgentCampaignReadback(
        campaign_id="campaign",
        journal_revision=10,
        records=(
            AgentCampaignRecord(session_id="session", revision=1, fact=intent),
            AgentCampaignRecord(
                session_id="session",
                revision=2,
                fact=AgentCampaignPhase(
                    campaign_id="campaign",
                    expected_revision=1,
                    fleet_run_id="campaign",
                    context=context,
                    execution_digest=target.execution_digest,
                    selected_step_ids=("check",),
                ),
            ),
            AgentCampaignRecord(
                session_id="session",
                revision=3,
                fact=AgentCampaignDecision(
                    campaign_id="campaign",
                    expected_revision=2,
                    decision_id="stopped",
                    phase_ordinal=1,
                    remaining_runs=0,
                    stop_reason=AgentRepairStopReason.ATTEMPTS_EXHAUSTED,
                ),
            ),
        ),
    )
    detail = SimpleNamespace(
        run=SimpleNamespace(
            fleet_run_id="campaign",
            target=target,
            targets=(repository,),
            status=FleetRunStatus.COMPLETED,
        ),
        operations=(report,),
        outcome=SimpleNamespace(value="ATTENTION_REQUIRED"),
        feedback=SimpleNamespace(
            model_dump=Mock(
                return_value={
                    "authority": "observation_only",
                    "diagnostic": "original failed verification",
                }
            )
        ),
    )
    detail.model_copy = lambda **_: detail
    subject = repository_subject_instance_id(root)
    source = RepositorySourceAuthority.create(
        subject_instance_id=subject,
        source_snapshot=PlanSnapshotEvidence(sha256="a" * 64, byte_count=1, entry_count=1),
    )
    review = AgentContinuationReview(
        campaign_id="campaign",
        campaign_revision=3,
        decision_id="stopped",
        previous_run_id="campaign",
        previous_execution_digest=target.execution_digest,
        repository_id=repository.repository_id,
        repository_instance_id=subject,
        source_authority=source,
        implementation="codex",
        profile="selected",
        selection_identity="agent-continuation-selection-" + "b" * 64,
        verification=(("check",),),
        verification_timeouts=(3600,),
    )
    request = AgentContinuationRequest(
        new_fleet_run_id="fresh-child",
        review=review,
        feedback="Repair the observed problem",
        authorize=True,
    )
    service = SimpleNamespace(
        _runs=SimpleNamespace(
            get=Mock(side_effect=RunNotFound("not yet admitted")),
            agent_campaign_evidence=Mock(return_value=(campaign, (detail,))),
        ),
        _repositories=SimpleNamespace(require_included=Mock(return_value=repository)),
        run_native_agent=Mock(return_value=SimpleNamespace(fleet_run_id="fresh-child")),
    )
    selected = object()
    resolve = Mock(return_value=selected)
    monkeypatch.setattr(continuation, "_resolve_selection", resolve)
    monkeypatch.setattr(continuation, "_selection_identity", lambda _: review.selection_identity)
    monkeypatch.setattr(continuation, "_selection_refusals", lambda *_: ())
    return SimpleNamespace(
        service=service,
        request=request,
        detail=detail,
        campaign=campaign,
        report=report,
        step=step,
        selected=selected,
        resolve=resolve,
        repository=repository,
    )


def test_fresh_submission_transports_reviewed_authority_and_server_feedback(case):
    result = continuation.continue_agent_campaign(case.service, "campaign", case.request)
    assert result.fleet_run_id == "fresh-child" and result.acknowledgment == "admitted"
    submitted = case.service.run_native_agent.call_args.kwargs
    assert submitted["selected"] is case.selected
    assert submitted["task"] == case.request.feedback
    assert submitted["verification"] == (("check",),)
    assert submitted["verification_timeouts"] == (3600,)
    assert submitted["reviewed_repository_source"] == case.request.review.source_authority
    assert submitted["expected_repository_instance_ids"] == {
        case.repository.repository_id: case.request.review.repository_instance_id,
    }
    assert json.loads(submitted["prior_context"]) == {
        "feedback": {"authority": "observation_only", "diagnostic": "original failed verification"},
    }
    assert submitted["continuation_receipt"] == result.receipt
    assert case.detail.run.target.agent_continuation is None


def test_exact_retry_reads_original_receipt_before_source_catalog_or_parent(case):
    result = continuation.continue_agent_campaign(case.service, "campaign", case.request)
    target = case.detail.run.target.model_copy(update={"agent_continuation": result.receipt})
    case.service._runs.get = Mock(
        return_value=SimpleNamespace(
            run=SimpleNamespace(
                fleet_run_id="fresh-child",
                target=target,
                targets=(case.repository,),
            )
        )
    )
    case.service._runs.agent_campaign_evidence.reset_mock()
    case.service._repositories.require_included.reset_mock()
    case.resolve.reset_mock()
    case.repository.path.rmdir()
    replay = continuation.continue_agent_campaign(case.service, "campaign", case.request)
    assert replay.acknowledgment == "existing" and replay.receipt == result.receipt
    case.service._runs.agent_campaign_evidence.assert_not_called()
    case.service._repositories.require_included.assert_not_called()
    case.resolve.assert_not_called()
    case.service.run_native_agent.assert_called_once()
    changed = case.request.model_copy(update={"feedback": "Different instruction"})
    with pytest.raises(StateConflict, match="different intent"):
        continuation.continue_agent_campaign(case.service, "campaign", changed)
    case.service.run_native_agent.assert_called_once()


def test_exact_receipt_cannot_authorize_a_lookup_returning_another_run_identity(case):
    receipt = continuation._receipt(case.request)
    target = case.detail.run.target.model_copy(update={"agent_continuation": receipt})
    case.service._runs.get = Mock(
        return_value=SimpleNamespace(
            run=SimpleNamespace(
                fleet_run_id="foreign-run",
                target=target,
                targets=(case.repository,),
            )
        )
    )
    with pytest.raises(StateConflict, match="different intent"):
        continuation.continue_agent_campaign(case.service, "campaign", case.request)
    case.service._runs.agent_campaign_evidence.assert_not_called()
    case.resolve.assert_not_called()
    case.service.run_native_agent.assert_not_called()


@pytest.mark.parametrize("boundary", ["active", "cancelled", "cleanup", "quiescence", "resources"])
def test_unsettled_campaign_refuses_before_catalog_or_native_admission(case, boundary):
    if boundary == "active":
        case.detail.run.status = FleetRunStatus.RUNNING
    elif boundary == "cancelled":
        case.detail.outcome.value = "CANCELLED"
    elif boundary in {"cleanup", "quiescence"}:
        step = case.step
        if boundary == "cleanup":
            step = step.model_copy(
                update={
                    "settlement": step.settlement.model_copy(
                        update={"cleanup": "UNAVAILABLE"},
                    )
                }
            )
        else:
            attempt = step.attempts[0]
            step = step.model_copy(
                update={
                    "attempts": (
                        attempt.model_copy(
                            update={
                                "supervisor": attempt.supervisor.model_copy(
                                    update={"final_process_group_quiescent": False}
                                ),
                            }
                        ),
                    )
                }
            )
        evidence = case.report.evidence.model_copy(
            update={
                "plan": case.report.evidence.plan.model_copy(
                    update={"steps": (step,)},
                )
            }
        )
        case.detail.operations = (case.report.model_copy(update={"evidence": evidence}),)
    else:
        case.detail.operations = (
            SimpleNamespace(
                resource_settlement="UNRESOLVED",
                target=case.detail.run.target,
                repository=case.repository,
                provenance=case.report.provenance,
                evidence=case.report.evidence,
            ),
        )
    with pytest.raises(StateConflict):
        continuation.continue_agent_campaign(case.service, "campaign", case.request)
    case.resolve.assert_not_called()
    case.service.run_native_agent.assert_not_called()


@pytest.mark.parametrize("boundary", ["revision", "subject", "selection", "verification"])
def test_stale_review_refuses_before_admission(case, monkeypatch, boundary):
    review = case.request.review
    if boundary == "revision":
        review = review.model_copy(update={"campaign_revision": 2})
    elif boundary == "subject":
        case.repository.path.rmdir()
        case.repository.path.mkdir()
    elif boundary == "selection":
        monkeypatch.setattr(continuation, "_selection_identity", lambda _: "different-selection")
    else:
        review = review.model_copy(update={"verification": (("unreviewed-command",),)})
    request = case.request.model_copy(update={"review": review})
    with pytest.raises(StateConflict):
        continuation.continue_agent_campaign(case.service, "campaign", request)
    case.service.run_native_agent.assert_not_called()


def test_concurrent_admission_readback_does_not_submit_another_worker(case):
    def admit_then_lose_ack(**submission):
        child = SimpleNamespace(
            run=SimpleNamespace(
                fleet_run_id="fresh-child",
                targets=(case.repository,),
                target=case.detail.run.target.model_copy(
                    update={
                        "agent_continuation": submission["continuation_receipt"],
                    }
                ),
            )
        )
        case.service._runs.get = Mock(return_value=child)
        raise StateConflict("Source changed while the identical child began work")

    case.service.run_native_agent.side_effect = admit_then_lose_ack
    result = continuation.continue_agent_campaign(case.service, "campaign", case.request)
    assert result.acknowledgment == "existing"
    case.service.run_native_agent.assert_called_once()


@pytest.mark.parametrize("feedback", [" ", "\x00bad", "é" * 5000, "\ud800"])
def test_feedback_is_validated_before_any_operation(feedback, case):
    with pytest.raises(ValueError):
        AgentContinuationRequest(
            new_fleet_run_id="child",
            review=case.request.review,
            feedback=feedback,
            authorize=True,
        )


@pytest.mark.parametrize(
    "declaration",
    [
        {"uv": {"lock_mode": "frozen"}, "argv": ("uv", "run", "--frozen", "pytest")},
        {"environment": ({"name": "CHANGED", "parts": ({"kind": "literal", "value": "value"},)},)},
        {"working_directory": "subdirectory"},
        {"success_codes": (0, 1)},
    ],
)
def test_verification_does_not_copy_untransported_execution_semantics(case, declaration):
    selected = ResolvedCommandStep.model_validate(
        {"step_id": "check", "argv": ("check",), **declaration}
    )
    plan = case.detail.run.target.plan.model_copy(update={"steps": (selected,)})
    case.detail.run.target = case.detail.run.target.model_copy(update={"plan": plan})
    commands, timeouts, qualification = continuation._verification((case.detail,))
    assert commands == timeouts == () and "does not establish verified success" in qualification


def test_verification_cannot_reauthor_a_custom_command_timeout(case):
    selected = ResolvedCommandStep(step_id="check", argv=("check",), timeout_seconds=17)
    plan = case.detail.run.target.plan.model_copy(update={"steps": (selected,)})
    case.detail.run.target = case.detail.run.target.model_copy(update={"plan": plan})
    commands, timeouts, _ = continuation._verification((case.detail,))
    assert commands == timeouts == ()


def test_protected_selected_profile_remains_inspectable_but_has_no_fresh_report_route():
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="protected",
        definition=NativeAgentDefinition(),
        profile=NativeAgentProfile(backend="bubblewrap", sandbox_owner="execution_backend"),
        executable_sha256="a" * 64,
    )
    refusal = continuation._selection_refusals(SimpleNamespace(), selected)
    assert refusal and "protected proposal/apply" in refusal[0]
    assert (
        selected.profile_name == "protected"
        and selected.profile.sandbox_owner == "execution_backend"
    )


def test_unavailable_selected_runtime_cannot_be_submitted_by_bypassing_inspection(
    case, monkeypatch
):
    monkeypatch.setattr(
        continuation, "_selection_refusals", lambda *_: ("Selected profile unavailable",)
    )
    with pytest.raises(FleetPlanInvalid, match="Selected profile unavailable"):
        continuation.continue_agent_campaign(case.service, "campaign", case.request)
    case.resolve.assert_called_once()
    case.service.run_native_agent.assert_not_called()


@pytest.mark.parametrize(
    "boundary,expected",
    [
        ("no-verification", "native_finished"),
        ("verified", "verified_success"),
        ("failed", "needs_review"),
        ("unknown-cleanup", "stopped"),
        ("cancelled", "stopped"),
        ("active", "awaiting_input"),
        ("missing-result", "stopped"),
        ("changed-membership", "stopped"),
    ],
)
def test_continuation_state_requires_original_native_and_independent_result_facts(
    case, boundary, expected
):
    native = ResolvedNativeAgent(
        agent="codex",
        profile_name="selected",
        definition=NativeAgentDefinition(),
        profile=NativeAgentProfile(),
        executable_sha256="c" * 64,
    )
    agent = ResolvedAgentStep(
        step_id="native-agent",
        source_step_id="native-agent",
        timeout_seconds=30,
        workload={"harness": "native", "native_agent": native},
        delivery_sha256="a" * 64,
        handoff_identity="frozen",
    )
    verification = () if boundary == "no-verification" else (case.detail.run.target.plan.steps[0],)
    review = case.request.review.model_copy(
        update={
            "verification": tuple(step.argv for step in verification),
            "verification_timeouts": tuple(step.timeout_seconds for step in verification),
        }
    )
    receipt = continuation._receipt(case.request.model_copy(update={"review": review}))
    plan = case.detail.run.target.plan.model_copy(update={"steps": (agent, *verification)})
    case.detail.run.target = case.detail.run.target.model_copy(
        update={
            "plan": plan,
            "agent_repair": None,
            "agent_continuation": receipt,
        }
    )
    case.detail.outcome.value = "SUCCESS"
    native_result = case.step.model_copy(update={"step_id": "native-agent", "kind": "agent"})
    results = (native_result, *((case.step,) if verification else ()))
    if boundary == "failed":
        case.detail.outcome.value = "ATTENTION_REQUIRED"
        failed = case.step.model_copy(
            update={
                "settlement": case.step.settlement.model_copy(
                    update={"outcome": "FAILED"},
                )
            }
        )
        results = (native_result, failed)
    elif boundary == "unknown-cleanup":
        results = (
            native_result,
            case.step.model_copy(
                update={
                    "settlement": case.step.settlement.model_copy(
                        update={"cleanup": "UNAVAILABLE"}
                    ),
                }
            ),
        )
    elif boundary == "cancelled":
        case.detail.outcome.value = "CANCELLED"
    elif boundary == "active":
        case.detail.run.status = FleetRunStatus.RUNNING
    elif boundary == "missing-result":
        results = (native_result,)
    elif boundary == "changed-membership":
        review = review.model_copy(update={"verification": (("different-command",),)})
        receipt = receipt.model_copy(update={"review": review})
        case.detail.run.target = case.detail.run.target.model_copy(
            update={"agent_continuation": receipt}
        )
    steps = {(case.repository.repository_id, step.step_id): step for step in results}
    assert (
        _continuation_state(
            case.detail,
            {case.repository.repository_id: case.report},
            steps,
        )
        == expected
    )


@pytest.mark.parametrize("tamper", ["feedback", "source", "verification", "campaign"])
def test_durable_target_rejects_continuation_receipt_with_different_authority(case, tamper):
    receipt = continuation._receipt(case.request)
    native = ResolvedNativeAgent(
        agent="codex",
        profile_name="selected",
        definition=NativeAgentDefinition(),
        profile=NativeAgentProfile(),
        executable_sha256="c" * 64,
    )
    agent = ResolvedAgentStep(
        step_id="native-agent",
        source_step_id="native-agent",
        timeout_seconds=30,
        workload={"harness": "native", "native_agent": native},
        delivery_sha256="a" * 64,
        handoff_identity="frozen",
        task_summary=case.request.feedback,
        task_sha256=receipt.feedback_sha256,
    )
    plan = ResolvedFleetPlan.create(
        name="fresh",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(agent, case.detail.run.target.plan.steps[0]),
        native_agent=native,
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
        agent_deliveries=(
            AgentStepDeliveryReference(
                step_id="native-agent",
                handoff_identity="frozen",
                delivery_sha256="a" * 64,
                reference_id="retained-fixture",
            ),
        ),
        reviewed_repository_source=receipt.review.source_authority,
        agent_continuation=receipt,
    )
    assert FleetPlanTarget.model_validate(target.model_dump()) == target
    payload = target.model_dump()
    if tamper == "feedback":
        payload["agent_continuation"]["feedback_sha256"] = "d" * 64
    elif tamper == "source":
        payload["reviewed_repository_source"] = None
    elif tamper == "verification":
        payload["agent_continuation"]["review"]["verification"] = (("different-command",),)
    else:
        payload["agent_repair"] = case.detail.run.target.agent_repair
    with pytest.raises(ValueError, match="Continuation"):
        FleetPlanTarget.model_validate(payload)
