"""Read-only controls over frozen Run intent, without native capability discovery."""

from __future__ import annotations

from hashlib import sha256

from oh_goon.core.models.agent_controls import (
    MAX_AGENT_CONTROL_ID_CHARS,
    MAX_AGENT_CONTROL_SELECTIONS,
    AgentControlPlacementAvailability,
    AgentControlSelection,
    AgentRunControl,
    AgentRunControls,
    NativeAgentControlMode,
)
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.fleet_plan import ResolvedAgentStep
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
from oh_goon.core.models.target import FleetPlanTarget


def _mode(selected: ResolvedNativeAgent) -> NativeAgentControlMode:
    if selected.profile.codex_service is not None:
        return "codex_service"
    if selected.profile.codex_login is not None:
        return "codex_login_cli"
    if selected.profile.pi_rpc:
        return "pi_rpc"
    return "interactive_cli" if selected.interactive else "headless_cli"


def _selections(detail: FleetRunDetail) -> tuple[tuple[AgentControlSelection, ...], int]:
    target = detail.run.target
    if not isinstance(target, FleetPlanTarget):
        return (), 0
    placement = (
        detail.execution.placement.isolation_backend
        if isinstance(detail.execution, StrictFleetExecutionAdmission)
        else None
    )
    placement_availability: AgentControlPlacementAvailability = (
        "unavailable"
        if placement is None
        else "omitted_over_limit"
        if len(placement.configuration_id) > 160
        else "recorded"
    )
    selections: list[AgentControlSelection] = []
    omitted = 0
    for step in target.plan.steps:
        if not isinstance(step, ResolvedAgentStep) or step.workload.harness != "native":
            continue
        if len(selections) == MAX_AGENT_CONTROL_SELECTIONS:
            omitted += 1
            continue
        selected = step.workload.native_agent
        selections.append(
            AgentControlSelection(
                step_id=step.step_id,
                agent=selected.agent,
                mode=_mode(selected),
                implementation_identity=f"{selected.agent}:sha256:{selected.executable_sha256}",
                executable_sha256=selected.executable_sha256,
                profile=selected.profile_name,
                selected_backend=selected.profile.backend,
                sandbox_owner=selected.profile.sandbox_owner,
                placement_backend_id=placement.backend_id
                if placement is not None and placement_availability == "recorded"
                else None,
                placement_configuration_id=placement.configuration_id
                if placement is not None and placement_availability == "recorded"
                else None,
                placement_availability=placement_availability,
            )
        )
    return tuple(selections), omitted


def project_agent_controls(detail: FleetRunDetail) -> AgentRunControls:
    """Describe supported product routes; do not launch, re-resolve or qualify them."""
    selections, omitted = _selections(detail)
    native_resume_reason = (
        "The selected Codex service Run owns one fresh thread/turn. Native same-thread resume "
        "RPC evidence does not qualify cross-Run session adoption."
        if any(selection.mode == "codex_service" for selection in selections)
        else "The selected Run exposes no exact native-session resume route. Ephemeral CLI/RPC "
        "execution and protocol inventory cannot qualify cross-Run session adoption."
    )
    cancel_available = detail.run.status.can_request_cancel
    run_id = detail.run.fleet_run_id
    bounded_run_id = len(run_id) <= MAX_AGENT_CONTROL_ID_CHARS
    return AgentRunControls(
        fleet_run_id=run_id if bounded_run_id else None,
        fleet_run_id_sha256=sha256(run_id.encode()).hexdigest() if not bounded_run_id else None,
        run_id_availability="recorded" if bounded_run_id else "omitted_over_limit",
        journal_revision=detail.journal_revision,
        selections=selections,
        omitted_selections=omitted,
        controls=(
            AgentRunControl(
                action="fresh_handoff",
                state="available",
                route="fresh_run_with_handoff",
                requirements=("fresh_resolution", "fresh_admission"),
                reason="Explicit recorded context can seed a fresh Run. It remains untrusted and "
                "carries no native session or execution permission.",
            ),
            AgentRunControl(
                action="exact_native_resume",
                state="unsupported",
                reason=native_resume_reason,
            ),
            AgentRunControl(
                action="cross_harness_fresh_handoff",
                state="available",
                route="fresh_run_with_handoff",
                requirements=("fresh_resolution", "fresh_admission"),
                reason="Select another harness for a separately admitted fresh Run with explicit "
                "untrusted context. Native session migration and installed environment parity "
                "are not established.",
            ),
            AgentRunControl(
                action="steer",
                state="unsupported",
                reason="The Run exposes no live native steering route for the frozen selection.",
            ),
            AgentRunControl(
                action="follow_up",
                state="unsupported",
                reason="Native tool/model follow-up inside an owned turn does not expose a user "
                "follow-up route. Use separate fresh resolution and admission.",
            ),
            AgentRunControl(
                action="abort",
                state="available" if cancel_available else "unsupported",
                route="cancel_run" if cancel_available else None,
                requirements=("original_run_owner",) if cancel_available else (),
                reason="Request cancellation from this original Run owner; terminal result and "
                "process cleanup remain canonical execution evidence."
                if cancel_available
                else "This Run does not accept another cancellation request. "
                "Native abort acknowledgments do not establish process cleanup.",
            ),
            AgentRunControl(
                action="detach",
                state="unsupported",
                reason="The Run exposes no native-session detach/reattach route. Closing a "
                "transport does not transfer Run custody or prove tool quiescence.",
            ),
        ),
        qualification_reason="Frozen harness, mode and backend identities describe selected "
        "intent. This read cut contains no complete installed harness/mode/environment "
        "qualification. Current OS, inventory, RPC presence and historical qualification tables "
        "cannot supply that proof.",
    )
