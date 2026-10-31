from __future__ import annotations

from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, FleetRunAdmissionIntent
from oh_goon.core.models.run_resources import (
    RunResourceEnvelopeEvidence,
    RunResourceEnvelopeObservation,
)
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.core.vocabulary import OperationAction
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.run_admission import repository_admissions_for


def run_resource_envelope(fleet_run_id: str = "resource-run") -> RunResourceEnvelopeEvidence:
    """Structurally valid observed facts for model/Journal tests, not native proof."""
    resources = CicdResourcePolicy(cpu_millis=1000, memory_mib=64, max_processes=16)
    return RunResourceEnvelopeEvidence(
        fleet_run_id=fleet_run_id,
        unit_name="oh-goon-run-" + "a" * 32 + ".scope",
        invocation_id="b" * 32,
        handoff_sha256="sha256:" + "c" * 64,
        process_birth=ProcessBirthIdentity(pid=12345, boot_id="fixture-boot", start_ticks=7),
        resources=resources,
        cgroup_device=1,
        cgroup_inode=2,
        cpu_max="100000 100000",
        memory_max_bytes=resources.memory_bytes,
        memory_swap_max_bytes=0,
        pids_max=16,
        oom_policy="kill",
        memory_oom_group=True,
    )


def run_resource_admission(
    repository: RepositoryIdentity, expected: RunResourceEnvelopeEvidence
) -> FleetRunAdmissionIntent:
    plan = ResolvedFleetPlan.create(
        name="resource-observation",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                step_id="check", argv=("true",), capabilities=PlanStepCapabilities()
            ),
        ),
    )
    target = structural_fleet_plan_target(plan)
    return FleetRunAdmissionIntent(
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        execution=DirectFleetExecutionAdmission(resource_envelope=expected),
        repository_admissions=repository_admissions_for(target, (repository,)),
    )


def run_resource_observation(
    expected: RunResourceEnvelopeEvidence,
) -> RunResourceEnvelopeObservation:
    return RunResourceEnvelopeObservation(
        fleet_run_id=expected.fleet_run_id,
        unit_name=expected.unit_name,
        invocation_id=expected.invocation_id,
        state="INCOMPLETE",
        active_state="inactive",
        result="success",
        cpu_usage_usec=30,
        memory_peak_bytes=2048,
        oom_kill_count=0,
        cleanup="VERIFIED",
        reason_code="NATIVE_METRICS_INCOMPLETE",
    )


def require_native_run_resource_context(resources: CicdResourcePolicy) -> None:
    """Read-only availability probe; never weaken the test caller's native limits."""
    import os

    import pytest

    from oh_goon.core.errors import FleetDomainError
    from oh_goon.integrations.run_resource_envelope import prepare_run_resource_scope

    try:
        prepare_run_resource_scope("native-resource-qualification", resources)
    except (FleetDomainError, OSError) as error:
        reason = f"native whole-Run resource context unavailable: {error}"
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_RUN_RESOURCES") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
