"""Continue stopped campaign work through a separately admitted ordinary Run."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid, RunNotFound, StateConflict
from oh_goon.core.models.agent_continuation import (
    AgentContinuationInspection,
    AgentContinuationReceipt,
    AgentContinuationRequest,
    AgentContinuationResult,
    AgentContinuationReview,
    AgentContinuationSelection,
)
from oh_goon.core.models.agent_providers import AgentProvidersConfig, ResolvedNativeAgent
from oh_goon.core.models.agent_repair import AgentCampaignReadback, AgentRepairStopReason
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepTeardownEvidence
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_subject_instance_id, stable_id
from oh_goon.core.projections.agent_controls import native_agent_control_mode
from oh_goon.integrations.fleet_plan_native_steps import require_native_agent_compatibility
from oh_goon.runtime.agent_providers import (
    agent_providers_path,
    load_agent_providers,
    resolve_native_agent,
)
from oh_goon.services.fleet_plan_continuation_inspection import inspect_continuation_source

if TYPE_CHECKING:
    from oh_goon.services.fleet_plans import FleetPlanService


def _stopped_campaign(
    service: FleetPlanService, selected: str
) -> tuple[AgentCampaignReadback, tuple[FleetRunDetail, ...]]:
    campaign, details = service._runs.agent_campaign_evidence(selected)
    decision = campaign.terminal_decision
    if campaign.intent is None or decision is None or not campaign.complete or not details:
        raise StateConflict("Continuation requires a committed stopped campaign and admitted Run")
    if decision.stop_reason is AgentRepairStopReason.CANCELLED or decision.unresolved_run_ids:
        raise StateConflict("Cancelled or unresolved campaign work cannot authorize continuation")
    for detail in details:
        if not detail.run.status.terminal or detail.outcome.value == "CANCELLED":
            raise StateConflict("Every campaign Run must be terminal and uncancelled")
        target = detail.run.target
        if (
            not isinstance(target, FleetPlanTarget)
            or target.agent_repair is None
            or target.agent_repair.repair_id != campaign.campaign_id
            or len(detail.run.targets) != 1
            or detail.run.targets[0].repository_id != campaign.intent.repository_id
            or len(detail.operations) != 1
        ):
            raise StateConflict(
                "Campaign Run authority or exact repository membership is incomplete"
            )
        operation = detail.operations[0]
        evidence = operation.evidence
        if (
            operation.resource_settlement != "SETTLED"
            or operation.target != target
            or operation.repository != detail.run.targets[0]
            or operation.provenance.fleet_run_id != detail.run.fleet_run_id
            or not isinstance(evidence, FleetPlanOperationEvidence)
            or evidence.plan is None
            or evidence.plan.plan_id != target.plan.plan_id
            or tuple((step.step_id, step.kind) for step in evidence.plan.steps)
            != tuple((step.step_id, step.kind) for step in target.plan.steps)
        ):
            raise StateConflict("Campaign execution or resource settlement is unresolved")
        for step in evidence.plan.steps:
            if step.status == "SKIPPED" and not step.attempts:
                continue
            if (
                step.settlement.cleanup != "VERIFIED"
                or isinstance(step.teardown, FailedPlanStepTeardownEvidence)
                or not step.attempts
                or any(
                    attempt.supervisor is None
                    or attempt.supervisor.final_process_group_quiescent is not True
                    or (
                        attempt.standard_isolation is not None
                        and attempt.standard_isolation.filesystem_cleanup != "verified"
                    )
                    for attempt in step.attempts
                )
            ):
                raise StateConflict("Campaign process cleanup is not verified")
    return campaign, details


def _verification(
    details: tuple[FleetRunDetail, ...],
) -> tuple[tuple[tuple[str, ...], ...], tuple[float | None, ...], str]:
    """Offer only complete direct command intent representable by native --verify."""
    for detail in reversed(details):
        target = detail.run.target
        assert isinstance(target, FleetPlanTarget)
        context = target.agent_repair
        if context is None or context.phase not in {"regression", "verification"}:
            continue
        if target.plan.requirements or not target.plan.steps or len(target.plan.steps) > 16:
            break
        commands = []
        timeouts = []
        for step in target.plan.steps:
            if not isinstance(step, ResolvedCommandStep):
                break
            simple = ResolvedCommandStep(step_id=step.step_id, argv=step.argv)
            # Published diagnostics do not grant effects. All execution semantics,
            # environment, project/uv selection and conditions must be ordinary.
            omitted = {"outputs", "regression_failure"}
            if step.model_dump(exclude=omitted) != simple.model_dump(exclude=omitted):
                break
            commands.append(step.argv)
            timeouts.append(step.timeout_seconds)
        else:
            return (
                tuple(commands),
                tuple(timeouts),
                "Recorded direct verification commands will run afresh.",
            )
        break
    return (
        (),
        (),
        (
            "No complete direct verification selection is available. Native completion alone "
            "does not establish verified success; select verification through a new Plan."
        ),
    )


def _selection_identity(selected: ResolvedNativeAgent) -> str:
    return stable_id(
        "agent-continuation-selection",
        {
            "agent": selected.agent,
            "profile_name": selected.profile_name,
            "definition": selected.definition.model_dump(mode="json"),
            "profile": selected.profile.model_dump(mode="json"),
            "executable_sha256": selected.executable_sha256,
            "connection": selected.connection.model_dump(mode="json")
            if selected.connection is not None
            else None,
            "interactive": selected.interactive,
        },
    )


def _resolve_selection(
    details: tuple[FleetRunDetail, ...],
    workspace: Path,
    *,
    implementation: str | None,
    profile: str | None,
    config_file: str | None,
) -> ResolvedNativeAgent:
    prior = next(
        (
            detail.run.target.plan.native_agent
            for detail in reversed(details)
            if isinstance(detail.run.target, FleetPlanTarget)
            and detail.run.target.plan.native_agent is not None
        ),
        None,
    )
    if implementation is None:
        implementation = prior.agent if prior is not None else None
    if profile is None and prior is not None:
        profile = prior.profile_name
    if implementation is None:
        raise FleetPlanInvalid("Select the native implementation and profile for the fresh Run")
    path = agent_providers_path(os.environ, Path(config_file) if config_file is not None else None)
    config = (
        load_agent_providers(path)
        if config_file is not None or path.exists()
        else AgentProvidersConfig()
    )
    selected = resolve_native_agent(
        config, implementation, profile, workspace=workspace, environment=os.environ
    )
    return selected


def _selection_refusals(
    service: FleetPlanService, selected: ResolvedNativeAgent
) -> tuple[str, ...]:
    """Inspect the existing runtime boundary without launching or selecting another one."""
    if selected.interactive or selected.profile.sandbox_owner != "harness":
        return (
            "Fresh coding continuation requires a noninteractive harness-owned write profile; "
            "protected proposal/apply work requires its original separately admitted campaign.",
        )
    try:
        require_native_agent_compatibility(selected, role="report", terminal_available=False)
    except (FleetDomainError, ValueError):
        return ("The selected native implementation and profile do not support fresh report work.",)
    if selected.profile.backend == "bubblewrap":
        from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation

        if (
            not isinstance(service._standard_process_isolation, BubblewrapProcessIsolation)
            or service._git_service is None
            or service._git_service.command_runner is None
            or service._git_service.observation_runner is None
        ):
            return (
                "The selected Bubblewrap profile requires its already configured isolation "
                "backend and scoped Git execution boundary in this application.",
            )
    return ()


def inspect_agent_continuation(
    service: FleetPlanService,
    campaign_or_run_id: str,
    *,
    implementation: str | None = None,
    profile: str | None = None,
    config_file: str | None = None,
) -> AgentContinuationInspection:
    if service._repositories is None or service._content_store is None:
        raise FleetPlanInvalid("Campaign continuation requires the registered repository owner")
    campaign, details = _stopped_campaign(service, campaign_or_run_id)
    assert campaign.intent is not None and campaign.terminal_decision is not None
    previous = details[-1]
    target = previous.run.target
    assert isinstance(target, FleetPlanTarget)
    repository = service._repositories.require_included(campaign.intent.repository_id)
    selected = _resolve_selection(
        details,
        repository.path,
        implementation=implementation,
        profile=profile,
        config_file=config_file,
    )
    baseline = next(
        (
            detail
            for detail in reversed(details)
            if isinstance(detail.run.target, FleetPlanTarget) and detail.run.target.agent_deliveries
        ),
        None,
    )
    if baseline is None:
        raise FleetPlanInvalid("The original captured campaign candidate is unavailable")
    source = inspect_continuation_source(
        repositories=service._repositories,
        repository_id=repository.repository_id,
        previous=baseline,
        content_store=service._content_store,
        git_service=service._git_service,
    )
    commands, timeouts, reason = _verification(details)
    refusals = _selection_refusals(service, selected)
    identity = _selection_identity(selected)
    review = AgentContinuationReview(
        campaign_id=campaign.campaign_id,
        campaign_revision=campaign.latest_revision,
        decision_id=campaign.terminal_decision.decision_id,
        previous_run_id=previous.run.fleet_run_id,
        previous_execution_digest=target.execution_digest,
        repository_id=repository.repository_id,
        repository_instance_id=source.repository_instance_id,
        source_authority=source.source_authority,
        implementation=selected.agent,
        profile=selected.profile_name,
        config_file=config_file,
        selection_identity=identity,
        verification=commands,
        verification_timeouts=timeouts,
    )
    return AgentContinuationInspection(
        state="verified_success"
        if campaign.terminal_decision.stop_reason is AgentRepairStopReason.CONVERGED
        else "needs_review"
        if campaign.terminal_decision.stop_reason
        in {
            AgentRepairStopReason.REVIEW_INCOMPLETE,
            AgentRepairStopReason.ADJUDICATION_INCOMPLETE,
        }
        else "stopped"
        if refusals
        else "awaiting_input",
        campaign_id=campaign.campaign_id,
        previous_run_id=previous.run.fleet_run_id,
        repository=repository,
        selection=AgentContinuationSelection(
            implementation=selected.agent,
            profile=selected.profile_name,
            mode=native_agent_control_mode(selected),
            selected_backend=selected.profile.backend,
            sandbox_owner=selected.profile.sandbox_owner,
            executable_sha256=selected.executable_sha256,
            selection_identity=identity,
        ),
        feedback=previous.model_copy(update={"campaign_journal": campaign}).feedback,
        source=source,
        review=review,
        verification_status="available" if commands else "unavailable",
        verification_reason=reason,
        can_continue=not refusals,
        refusal_reasons=refusals,
    )


def _receipt(request: AgentContinuationRequest) -> AgentContinuationReceipt:
    return AgentContinuationReceipt(
        request_identity=stable_id("agent-continuation-request", request.model_dump(mode="json")),
        review=request.review,
        feedback_sha256=hashlib.sha256(request.feedback.encode("utf-8")).hexdigest(),
    )


def _existing(
    service: FleetPlanService, request: AgentContinuationRequest, receipt: AgentContinuationReceipt
) -> AgentContinuationResult | None:
    try:
        detail = service._runs.get(request.new_fleet_run_id)
    except RunNotFound:
        return None
    target = detail.run.target
    if (
        detail.run.fleet_run_id != request.new_fleet_run_id
        or not isinstance(target, FleetPlanTarget)
        or target.agent_continuation != receipt
        or tuple(repository.repository_id for repository in detail.run.targets)
        != (request.review.repository_id,)
    ):
        raise StateConflict("The requested Run identity is already bound to different intent")
    return AgentContinuationResult(
        fleet_run_id=detail.run.fleet_run_id, receipt=receipt, acknowledgment="existing"
    )


def continue_agent_campaign(
    service: FleetPlanService, campaign_or_run_id: str, request: AgentContinuationRequest
) -> AgentContinuationResult:
    request = AgentContinuationRequest.model_validate(request.model_dump())
    review = request.review
    if campaign_or_run_id not in {review.campaign_id, review.previous_run_id}:
        raise StateConflict("Continuation request belongs to another campaign")
    if request.new_fleet_run_id in {review.campaign_id, review.previous_run_id}:
        raise StateConflict("A continuation requires a new Run identity")
    receipt = _receipt(request)
    # Lost acknowledgment and exact retries read the original receipt before
    # any catalog resolution, current source walk or native preparation.
    existing = _existing(service, request, receipt)
    if existing is not None:
        return existing
    try:
        if service._repositories is None:
            raise FleetPlanInvalid("Continuation requires the registered repository owner")
        campaign, details = _stopped_campaign(service, campaign_or_run_id)
        decision = campaign.terminal_decision
        assert campaign.intent is not None and decision is not None
        previous = details[-1]
        target = previous.run.target
        assert isinstance(target, FleetPlanTarget)
        verification, timeouts, _ = _verification(details)
        if (
            campaign.campaign_id != review.campaign_id
            or campaign.latest_revision != review.campaign_revision
            or decision.decision_id != review.decision_id
            or previous.run.fleet_run_id != review.previous_run_id
            or target.execution_digest != review.previous_execution_digest
            or campaign.intent.repository_id != review.repository_id
            or verification != review.verification
            or timeouts != review.verification_timeouts
        ):
            raise StateConflict("Campaign authority changed since continuation inspection")
        repository = service._repositories.require_included(review.repository_id)
        if (
            repository_subject_instance_id(repository.path) != review.repository_instance_id
            or review.source_authority.subject_instance_id != review.repository_instance_id
        ):
            raise StateConflict("The registered repository subject changed since inspection")
        selected = _resolve_selection(
            details,
            repository.path,
            implementation=review.implementation,
            profile=review.profile,
            config_file=review.config_file,
        )
        if _selection_identity(selected) != review.selection_identity:
            raise StateConflict("Native selection changed since continuation inspection")
        refusals = _selection_refusals(service, selected)
        if refusals:
            raise FleetPlanInvalid(" ".join(refusals))
        context = json.dumps(
            {
                "feedback": previous.model_copy(
                    update={"campaign_journal": campaign}
                ).feedback.model_dump(mode="json", by_alias=True)
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        started = service.run_native_agent(
            selected=selected,
            workspace=repository.path,
            skill=None,
            task=request.feedback,
            verification=review.verification,
            verification_timeouts=review.verification_timeouts,
            fleet_run_id=request.new_fleet_run_id,
            prior_context=context,
            reviewed_repository_source=review.source_authority,
            expected_repository_instance_ids={review.repository_id: review.repository_instance_id},
            continuation_receipt=receipt,
        )
    except (FleetDomainError, OSError, ValueError):
        # Concurrent identical callers can observe source edits made by the
        # already admitted child. Its exact original receipt wins readback;
        # admission itself remains the one Journal/worker authority.
        existing = _existing(service, request, receipt)
        if existing is not None:
            return existing
        raise
    if started.fleet_run_id != request.new_fleet_run_id:
        raise StateConflict("Continuation admission returned a different Run identity")
    return AgentContinuationResult(
        fleet_run_id=started.fleet_run_id, receipt=receipt, acknowledgment="admitted"
    )
