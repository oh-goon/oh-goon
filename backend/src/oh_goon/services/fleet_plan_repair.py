"""Repair workload admission and stop decisions from original Fleet evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_output_refs import step_output_reference
from oh_goon.core.models.agent_repair import AgentRepairStopReason as Stop
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedLocalSearchAgentStep,
    StepFailurePolicy,
    StepStatusCondition,
)
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepTeardownEvidence
from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
from oh_goon.core.models.fleet_plan_step_contract import (
    EmptyPlanCacheSource,
    RepositoryWorkspaceSource,
)
from oh_goon.core.models.fleet_plan_step_primitives import PlanStreamOutput, StepLifecycle
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.path_authority import repository_relative_parts
from oh_goon.integrations.agent_patch_apply import parse_apply_arguments


@dataclass(frozen=True, slots=True)
class AgentRepairSteps:
    test: ResolvedCommandStep
    agent: ResolvedLocalSearchAgentStep
    apply: ResolvedCommandStep
    retest: ResolvedCommandStep
    feedback_name: str
    allowed_path: str


def _requires_status(step, source: str, status: str) -> bool:
    return (
        isinstance(step.when, StepStatusCondition)
        and step.when.step_id == source
        and step.when.statuses == (status,)
    )


def _apply_selection(argv: tuple[str, ...]):
    """Admit the direct CLI or explicit locked uv project invocation used by source installs.

    This is a workload allowlist. Native uv still owns project/environment resolution.
    Directory-changing and arbitrary wrapper commands are not repair apply authority.
    """
    if Path(argv[0]).name == "oh-goon":
        application = argv[1:]
    elif Path(argv[0]).name == "uv" and argv[1:2] == ("run",):
        index = 2
        project = False
        switches = set()
        while index < len(argv):
            token = argv[index]
            if token == "--project" and not project and index + 1 < len(argv):
                project = True
                index += 2
            elif token in {"--offline", "--frozen", "--no-sync"} and token not in switches:
                switches.add(token)
                index += 1
            else:
                break
        if not project or switches != {"--offline", "--frozen", "--no-sync"}:
            raise FleetPlanInvalid(
                "repair uv apply requires an explicit offline, frozen, no-sync project"
            )
        if index >= len(argv) or argv[index] != "oh-goon":
            raise FleetPlanInvalid("repair apply must select the original oh-goon CLI")
        application = argv[index + 1 :]
    else:
        raise FleetPlanInvalid("repair apply must select the original oh-goon CLI")
    if application[:2] != ("agent", "apply"):
        raise FleetPlanInvalid("repair apply must select the original agent apply command")
    return parse_apply_arguments(application[2:])


def validate_agent_repair_plan(plan: ResolvedFleetPlan, handoff) -> AgentRepairSteps:
    if plan.execution.purpose is ExecutionPurpose.CICD:
        raise FleetPlanInvalid(
            "automatic repair uses the selected standard-local execution profile"
        )
    if len(plan.steps) != 4:
        raise FleetPlanInvalid("repair requires exactly test, proposal, apply and retest Steps")
    test, agent, apply, retest = plan.steps
    if (
        type(test) is not ResolvedCommandStep
        or type(apply) is not ResolvedCommandStep
        or type(retest) is not ResolvedCommandStep
        or not isinstance(agent, ResolvedLocalSearchAgentStep)
        or agent.harness != "local-model-proposal"
    ):
        raise FleetPlanInvalid(
            "repair requires native command, proposal Agent, apply and native retest"
        )
    if any(
        step.retry.max_attempts != 1
        or step.lifecycle is not StepLifecycle.NORMAL
        or step.working_directory != "."
        or step.uv is not None
        or not isinstance(step.workspace.source, RepositoryWorkspaceSource)
        or not isinstance(step.cache.source, EmptyPlanCacheSource)
        or step.workspace.publish
        or step.cache.publish
        or step.artifacts
        for step in plan.steps
    ):
        raise FleetPlanInvalid(
            "repair Steps require single attempts in the selected repository workspace"
        )
    if (
        test.when is not None
        or test.on_failure is not StepFailurePolicy.CONTINUE
        or retest.on_failure is not StepFailurePolicy.CONTINUE
        or test.success_codes != (0,)
        or retest.success_codes != (0,)
        or not _requires_status(agent, test.step_id, "FAILED")
        or not _requires_status(apply, agent.step_id, "COMPLETED")
        or not _requires_status(retest, apply.step_id, "COMPLETED")
        or apply.on_failure is not StepFailurePolicy.STOP
        or apply.success_codes != (0,)
    ):
        raise FleetPlanInvalid(
            "repair requires explicit failure-only proposal and successful apply/retest dependencies"
        )
    reference = step_output_reference(agent.feedback_reference or "")
    if reference is None or reference[0] != test.step_id:
        raise FleetPlanInvalid("repair Agent must select its exact failed native test diagnostic")
    for producer in (test, retest):
        if (
            len(producer.outputs) != 1
            or not isinstance(producer.outputs[0], PlanStreamOutput)
            or producer.outputs[0].name != reference[1]
            or producer.outputs[0].max_bytes > 2048
        ):
            raise FleetPlanInvalid(
                "repair tests require one bounded native stream diagnostic of at most 2048 bytes"
            )
    selected = _apply_selection(apply.argv)
    if (
        step_output_reference(selected.proposal) != (agent.step_id, "proposal")
        or selected.expected_handoff_identity is not None
    ):
        raise FleetPlanInvalid("repair apply must consume this attempt's original proposal output")
    path = selected.expected_path
    repository_relative_parts(path, label="repair allowed write path")
    if path not in {member.path for member in handoff.source.repository.members}:
        raise FleetPlanInvalid("repair apply path is not an original captured repository member")
    return AgentRepairSteps(test, agent, apply, retest, reference[1], path)


def repair_attempt_decision(detail: FleetRunDetail, steps: AgentRepairSteps) -> Stop | None:
    """None means the exact failed retest permits another caller-budgeted attempt."""
    if getattr(getattr(detail.run, "failure", None), "code", None) == "OPERATION_DEADLINE_EXPIRED":
        return Stop.TIMED_OUT
    if detail.run.status.value in {"CANCELLED", "CANCELLING"}:
        return Stop.CANCELLED
    if detail.run.status.value != "COMPLETED" or len(detail.operations) != 1:
        return Stop.EXECUTION_UNRESOLVED
    evidence = detail.operations[0].evidence
    result = getattr(evidence, "plan", None)
    if result is None or tuple(item.step_id for item in result.steps) != (
        steps.test.step_id,
        steps.agent.step_id,
        steps.apply.step_id,
        steps.retest.step_id,
    ):
        return Stop.EXECUTION_UNRESOLVED
    target = detail.run.target
    if target is None or result.plan_id != target.plan.plan_id:
        return Stop.EXECUTION_UNRESOLVED
    for step in result.steps:
        if isinstance(step.teardown, FailedPlanStepTeardownEvidence):
            return Stop.EXECUTION_UNRESOLVED
        needs_native_proof = step.status == "COMPLETED" or (
            step.status == "FAILED" and step.step_id in {steps.test.step_id, steps.retest.step_id}
        )
        if needs_native_proof and len(step.attempts) != 1:
            return Stop.EXECUTION_UNRESOLVED
        for attempt in step.attempts:
            if attempt.supervisor is None:
                if needs_native_proof:
                    return Stop.EXECUTION_UNRESOLVED
                continue
            if attempt.supervisor.cleanup_reason == "timeout":
                return Stop.TIMED_OUT
            if attempt.supervisor.cleanup_reason == "cancel":
                return Stop.CANCELLED
            if attempt.supervisor.cleanup_reason == "output_limit":
                return Stop.FEEDBACK_UNAVAILABLE
    test, agent, apply, retest = result.steps
    if test.status == "COMPLETED" and test.returncode == 0:
        return (
            Stop.TEST_PASSED
            if all(step.status == "SKIPPED" for step in (agent, apply, retest))
            else Stop.EXECUTION_UNRESOLVED
        )
    if not _has_feedback(test, steps.feedback_name):
        return Stop.FEEDBACK_UNAVAILABLE
    if agent.status != "COMPLETED" or agent.returncode != 0:
        return Stop.PROPOSAL_FAILED
    if apply.status != "COMPLETED" or apply.returncode != 0:
        return Stop.APPLY_FAILED
    if retest.status == "COMPLETED" and retest.returncode == 0:
        return Stop.TEST_PASSED
    if retest.status != "FAILED" or not retest.attempts:
        return Stop.EXECUTION_UNRESOLVED
    if not _has_feedback(retest, steps.feedback_name):
        return Stop.FEEDBACK_UNAVAILABLE
    return None


def _has_feedback(step, name: str) -> bool:
    return any(output.name == name and 0 < output.byte_count <= 2048 for output in step.outputs)
