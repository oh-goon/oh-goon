"""Native control publication preserves selection and qualification boundaries."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from oh_goon.core.models.agent_controls import AgentRunControls
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    CodexLoginConnection,
    CodexServiceConnection,
    NativeAgentDefinition,
    NativeAgentName,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_executor import FleetExecutorPlacement, PlanExecutorCapacityEvidence
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedNativeAgentWorkload,
)
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunDetail, FleetRunState
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    StrictFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.projections.agent_controls import project_agent_controls
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.run_admission import repository_admissions_for


def _selected(
    mode: str = "headless_cli", *, agent: NativeAgentName = "codex"
) -> ResolvedNativeAgent:
    profile = NativeAgentProfile(backend="bubblewrap", inference_profile="fixture")
    connection: AgentConnection | None = AgentConnection(
        profile="fixture",
        provider_name="fixture",
        provider=AgentProvider(api="openai-responses", base_url="http://127.0.0.1:4567"),
        selection=AgentModel(provider="fixture", model="fixture"),
    )
    if mode == "pi_rpc":
        agent = "pi"
        profile = NativeAgentProfile(
            backend="bubblewrap", allowed_agents=("pi",), inference_profile="fixture", pi_rpc=True
        )
    elif mode == "codex_service":
        connection = None
        profile = NativeAgentProfile(
            codex_service=CodexServiceConnection(
                home="/private/native-state", model="fixture", reasoning_effort="low"
            )
        )
    elif mode == "codex_login_cli":
        connection = None
        profile = NativeAgentProfile(
            codex_login=CodexLoginConnection(
                home="/private/native-state", model="fixture", reasoning_effort="low"
            )
        )
    return ResolvedNativeAgent(
        agent=agent,
        profile_name="selected-profile",
        definition=NativeAgentDefinition(executable="/unavailable/public-native"),
        profile=profile,
        executable_sha256="a" * 64,
        connection=connection,
        interactive=mode == "interactive_cli",
    )


def _detail(
    selections: tuple[ResolvedNativeAgent, ...],
    *,
    status: FleetRunStatus = FleetRunStatus.RUNNING,
    placement_configuration: str | None = None,
) -> FleetRunDetail:
    repository = RepositoryIdentity(path=Path("/controls-fixture/repository"))
    steps = tuple(
        ResolvedAgentStep(
            step_id=f"agent-{index}",
            source_step_id=f"agent-{index}",
            timeout_seconds=30,
            workload=ResolvedNativeAgentWorkload(native_agent=selected),
            delivery_sha256="b" * 64,
            handoff_identity=f"frozen-handoff-{index}",
        )
        for index, selected in enumerate(selections)
    )
    plan = ResolvedFleetPlan.create(
        name="native-control-projection",
        description="",
        execution=CicdExecutionPolicy()
        if placement_configuration
        else MaintenanceExecutionPolicy(),
        steps=steps or (ResolvedCommandStep(step_id="verify", argv=("verify",)),),
        native_agent=selections[0] if selections else None,
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="1" * 64),
        identity=structural_execution_identity(plan),
        agent_deliveries=tuple(
            AgentStepDeliveryReference(
                step_id=step.step_id,
                handoff_identity=step.handoff_identity,
                delivery_sha256=step.delivery_sha256,
                reference_id=f"delivery-{index}",
            )
            for index, step in enumerate(steps)
        ),
    )
    now = utc_now()
    run_fields: dict[str, Any] = {
        "status": status,
        "fleet_run_id": "original-run",
        "target": target,
        "capability": capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        "repository_scope": fleet_repository_scope(None),
        "targets": (repository,),
        "accepted_at": now,
        "progress": {"total": 1, "succeeded": int(status is FleetRunStatus.COMPLETED), "failed": 0},
    }
    if status is not FleetRunStatus.QUEUED:
        run_fields["started_at"] = now
    if status.terminal:
        run_fields["finished_at"] = now
    if status in {FleetRunStatus.CANCELLING, FleetRunStatus.CANCELLED}:
        run_fields["cancel_requested_at"] = now
    if status is FleetRunStatus.FAILED:
        run_fields["failure"] = {
            "code": "FIXTURE_FAILURE",
            "kind": "INTERNAL",
            "stage": "EXECUTION",
            "message": "failed",
        }
    run = TypeAdapter(FleetRunState).validate_python(run_fields)
    execution: DirectFleetExecutionAdmission | StrictFleetExecutionAdmission = (
        DirectFleetExecutionAdmission()
    )
    if placement_configuration is not None:
        execution = StrictFleetExecutionAdmission(
            placement=FleetExecutorPlacement(
                isolation_backend=PlanIsolationBackendEvidence(
                    backend_id="recorded-backend",
                    configuration_id=placement_configuration,
                    kernel_boundary="application_kernel",
                    remote_execution=False,
                    experimental=False,
                ),
                executor_capacity=PlanExecutorCapacityEvidence(
                    fleet_run_id=run.fleet_run_id,
                    pool_id="fixture",
                    executor_id="fixture",
                    executor_generation=1,
                    source="local_verified",
                    backend_configuration_id=placement_configuration,
                    capacity_cpu_millis=2000,
                    capacity_memory_bytes=1024 * 1024 * 1024,
                    requested_parallelism=1,
                    admitted_parallelism=1,
                    placement_policy="priority_rendezvous_fifo_v2",
                    placement_rank=0,
                    executor_priority=0,
                ),
                executor_trust_epoch=0,
            )
        )
    return FleetRunDetail(
        journal_revision=42,
        run=run,
        execution=execution,
        repository_admissions=repository_admissions_for(target, (repository,)),
    )


@pytest.mark.parametrize(
    "mode,agent",
    [
        ("headless_cli", "codex"),
        ("headless_cli", "opencode"),
        ("headless_cli", "pi"),
        ("interactive_cli", "codex"),
        ("codex_login_cli", "codex"),
        ("codex_service", "codex"),
        ("pi_rpc", "pi"),
    ],
)
def test_controls_bind_frozen_harness_mode_and_leave_environment_unqualified(mode, agent):
    detail = _detail((_selected(mode, agent=agent),))
    result = project_agent_controls(detail)
    selection = result.selections[0]
    assert (result.fleet_run_id, result.journal_revision) == ("original-run", 42)
    assert (selection.step_id, selection.agent, selection.mode) == ("agent-0", agent, mode)
    assert selection.implementation_identity == f"{agent}:sha256:{'a' * 64}"
    assert selection.profile == "selected-profile"
    assert selection.selection_availability == "frozen_intent"
    assert selection.environment_qualification == result.environment_qualification == "unqualified"
    assert selection.placement_availability == "unavailable"
    controls = {control.action: control for control in result.controls}
    assert len(controls) == 7
    for action in ("exact_native_resume", "steer", "follow_up", "detach"):
        assert controls[action].state == "unsupported"
        assert controls[action].route is None
    for action in ("fresh_handoff", "cross_harness_fresh_handoff"):
        assert controls[action].state == "available"
        assert controls[action].route == "fresh_run_with_handoff"
        assert controls[action].requirements == ("fresh_resolution", "fresh_admission")


@pytest.mark.parametrize("status", tuple(FleetRunStatus))
def test_abort_is_only_the_original_runs_cancellable_lifecycle_route(status):
    result = project_agent_controls(_detail((_selected(),), status=status))
    abort = next(control for control in result.controls if control.action == "abort")
    assert (abort.state == "available") is status.can_request_cancel
    assert abort.route == ("cancel_run" if status.can_request_cancel else None)
    assert abort.requirements == (("original_run_owner",) if status.can_request_cancel else ())


def test_inventory_names_current_host_and_unavailable_executable_cannot_qualify_controls(
    monkeypatch,
):
    detail = _detail((_selected("codex_service"),))
    baseline = project_agent_controls(detail)
    monkeypatch.setattr(sys, "platform", "another-unqualified-host")
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("No source/native rediscovery"))
    assert project_agent_controls(detail) == baseline
    renamed = replace(_selected("codex_service"), profile_name="resume-steer-detach-qualified")
    renamed_result = project_agent_controls(_detail((renamed,)))
    assert renamed_result.controls == baseline.controls
    assert renamed_result.environment_qualification == "unqualified"


def test_control_selections_preserve_order_duplicates_and_exact_omissions():
    result = project_agent_controls(_detail(tuple(_selected() for _ in range(11))))
    assert tuple(selection.step_id for selection in result.selections) == tuple(
        f"agent-{index}" for index in range(8)
    )
    assert result.omitted_selections == 3
    assert len({selection.implementation_identity for selection in result.selections}) == 1


@pytest.mark.parametrize("configuration", ["recorded-configuration", "x" * 161])
def test_recorded_placement_is_preserved_or_explicitly_omitted_without_qualification(configuration):
    result = project_agent_controls(_detail((_selected(),), placement_configuration=configuration))
    selection = result.selections[0]
    if len(configuration) <= 160:
        assert selection.placement_backend_id == "recorded-backend"
        assert selection.placement_configuration_id == configuration
        assert selection.placement_availability == "recorded"
    else:
        assert selection.placement_backend_id is selection.placement_configuration_id is None
        assert selection.placement_availability == "omitted_over_limit"
    assert result.environment_qualification == "unqualified"


def test_non_native_run_can_supply_untrusted_context_without_inventing_a_native_selection():
    result = project_agent_controls(_detail(()))
    assert result.selections == () and result.omitted_selections == 0
    assert result.controls[0].state == "available"
    assert result.environment_qualification == "unqualified"


def test_overlimit_run_identity_is_omitted_explicitly_without_truncating_its_literal():
    from hashlib import sha256

    original = _detail((_selected(),))
    long_id = "run-" + "x" * 513
    detail = original.model_copy(
        update={
            "run": original.run.model_copy(
                update={
                    "fleet_run_id": long_id,
                }
            )
        }
    )
    result = project_agent_controls(detail)
    assert result.fleet_run_id is None
    assert result.run_id_availability == "omitted_over_limit"
    assert result.fleet_run_id_sha256 == sha256(long_id.encode()).hexdigest()
    assert len(result.model_dump_json()) < 16 * 1024


def test_control_dto_rejects_missing_actions_and_native_resume_route_promotion():
    baseline = project_agent_controls(_detail((_selected("codex_service"),)))
    encoded = baseline.model_dump(mode="json")
    encoded["controls"][1] = encoded["controls"][0]
    with pytest.raises(ValidationError, match="each distinct action"):
        AgentRunControls.model_validate(encoded)
    encoded = baseline.model_dump(mode="json")
    encoded["controls"][1].update(state="available", route="fresh_run_with_handoff")
    with pytest.raises(ValidationError, match="cannot resume or steer"):
        AgentRunControls.model_validate(encoded)
