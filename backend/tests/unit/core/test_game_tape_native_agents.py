from __future__ import annotations

import hashlib

import pytest

from oh_goon.cli_plan_summary import resolved_plan_summary
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.game_tape_step_projection import project_steps
from oh_goon.core.models.agent_providers import (
    NativeAgentDefinition,
    NativeAgentName,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_observation import AgentHarnessObservation, NativeAgentObservation
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedLocalSearchAgentStep,
)
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepExecution, PlanOutputEvidence
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.game_tape import GameTapeStepExecution
from oh_goon.core.vocabulary import GameTapeStatus


def _native_step(agent: NativeAgentName) -> ResolvedLocalSearchAgentStep:
    return ResolvedLocalSearchAgentStep(
        step_id="agent",
        argv=("python",),
        timeout_seconds=10,
        delivery_sha256="b" * 64,
        handoff_identity="handoff",
        source_step_id="agent",
        harness="native",
        native_agent=ResolvedNativeAgent(
            agent=agent,
            profile_name="isolated-managed",
            definition=NativeAgentDefinition(
                executable=f"/native/{agent}-custom", arguments=("--configured",)
            ),
            profile=NativeAgentProfile(),
            executable_sha256="1" * 64,
        ),
    )


@pytest.mark.parametrize("agent_name", ("codex", "opencode", "pi"))
def test_durable_native_claims_remain_step_local_and_cannot_overrule_process_failure(
    agent_name: NativeAgentName,
) -> None:
    planned = (
        _native_step(agent_name),
        ResolvedCommandStep(step_id="verification", argv=("pytest",), timeout_seconds=10),
    )
    observation = NativeAgentObservation(
        agent=agent_name,
        implementation_identity=f"{agent_name}:sha256:" + "1" * 64,
        profile="isolated-managed",
        input_identity="sha256:" + "2" * 64,
        occurrence="agent",
        source_identity="sha256:" + "3" * 64,
        role="report",
        events=(AgentHarnessObservation(kind="context", summary="native context event"),),
        native_event_count=6140,
        omitted_observations=6012,
        settled=True,
        failed=False,
    )
    output = PlanOutputEvidence(sha256=hashlib.sha256(b"").hexdigest(), byte_count=0, line_count=0)
    execution = FailedPlanStepExecution(
        step_id="agent",
        kind="agent",
        argv=(
            "/launch/bwrap",
            *("--bind", "/runtime", "/runtime") * 32,
            "/launch/python3.13",
            "-m",
            "relay",
            "--",
            f"/native/{agent_name}-custom",
        ),
        returncode=17,
        stdout_evidence=output,
        stderr_evidence=output,
        repository_state_before="before",
        repository_state_after="after",
        observations=(observation,),
    )
    # Read the same durable evidence representation used by Run detail.
    retained = FailedPlanStepExecution.model_validate_json(execution.model_dump_json())
    agent, verification = project_steps(
        "native-run",
        "repository",
        planned,
        (retained,),
        step_sources={},
        source_label="goon.yml",
        terminal=True,
        operation_reason_code="OPERATION_FAILED",
    )
    assert agent.status is GameTapeStatus.FAILED
    assert agent.exit_code == 17
    assert agent.command == execution.argv
    assert agent.failure is not None and agent.failure.exit_code == 17
    assert agent.native_agent_observations == (observation,)
    assert agent.native_agent_observations[0].authority == "UNTRUSTED_HARNESS_CLAIM"
    assert agent.native_agent_observations[0].native_event_count == 6140
    assert agent.native_agent_observations[0].omitted_observations == 6012
    assert verification.native_agent_observations == ()
    assert verification.status is GameTapeStatus.NOT_RUN
    assert (
        GameTapeStepExecution.model_validate_json(
            agent.model_dump_json(exclude_computed_fields=True)
        )
        == agent
    )


@pytest.mark.parametrize("agent", ("codex", "opencode", "pi"))
def test_native_planned_projection_preserves_frozen_step_and_execution_lock(
    agent: NativeAgentName,
) -> None:
    step = _native_step(agent)
    plan = ResolvedFleetPlan.create(
        name="native",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(step,),
        native_agent=step.native_agent,
    )
    original_json = plan.model_dump_json(exclude_computed_fields=True)
    original_lock = build_execution_lock(plan)
    restored = ResolvedFleetPlan.model_validate_json(original_json)
    projected = project_steps(
        "native-run",
        "repository",
        restored.steps,
        (),
        step_sources={},
        source_label="goon.yml",
        terminal=False,
        operation_reason_code=None,
    )

    assert projected[0].command == (f"/native/{agent}-custom", "--configured")
    assert isinstance(restored.steps[0], ResolvedLocalSearchAgentStep)
    assert restored.steps[0].argv == ("python",)
    assert restored.model_dump_json(exclude_computed_fields=True) == original_json
    assert build_execution_lock(restored) == original_lock
    summary = "\n".join(resolved_plan_summary(restored))
    assert f"required_tool=/native/{agent}-custom" in summary
    assert f"executable=/native/{agent}-custom sha256={'1' * 64} host_support=python" in summary


def test_retained_native_invocation_is_not_reconstructed_from_current_selection() -> None:
    step = _native_step("pi")
    output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    retained = FailedPlanStepExecution.model_validate_json(
        FailedPlanStepExecution(
            step_id="agent",
            kind="agent",
            argv=("python",),
            returncode=17,
            stdout_evidence=output,
            stderr_evidence=output,
            repository_state_before="before",
            repository_state_after="after",
        ).model_dump_json(exclude_computed_fields=True)
    )
    projected = project_steps(
        "historical-run",
        "repository",
        (step,),
        (retained,),
        step_sources={},
        source_label="goon.yml",
        terminal=True,
        operation_reason_code=None,
    )
    assert projected[0].command == ("python",)


@pytest.mark.parametrize("returncode", (126, 127))
@pytest.mark.parametrize(
    "argv",
    (
        ("/launch/python3.13", "-m", "oh_goon.integrations.hashmarks.agent_tools"),
        (
            "/launch/python3.13",
            "-m",
            "oh_goon.integrations.agent_inference_relay",
            "--",
            "/native/pi-custom",
        ),
        ("python",),
        (),
    ),
)
def test_native_executable_failure_reason_uses_retained_invocation(
    argv: tuple[str, ...],
    returncode: int,
) -> None:
    step = _native_step("pi")
    output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    execution = FailedPlanStepExecution.model_validate_json(
        FailedPlanStepExecution(
            step_id="agent",
            kind="agent",
            argv=argv,
            returncode=returncode,
            stdout_evidence=output,
            stderr_evidence=output,
            repository_state_before="before",
            repository_state_after="after",
        ).model_dump_json(exclude_computed_fields=True)
    )
    projected = project_steps(
        "native-run",
        "repository",
        (step,),
        (execution,),
        step_sources={},
        source_label="goon.yml",
        terminal=True,
        operation_reason_code=None,
    )[0]
    assert projected.command == argv
    assert projected.reason is not None
    assert "/native/pi-custom" not in projected.reason
    assert projected.reason.endswith(f": {argv[0]}." if argv else ".")
