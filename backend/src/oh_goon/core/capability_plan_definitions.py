from oh_goon.core.capability_model import (
    CapabilityDefinition,
    CapabilityRunnerKind,
    CapabilityTargetSelector,
)
from oh_goon.core.models.capability import CapabilityTargetKind
from oh_goon.core.vocabulary import CommandSafety, OperationAction

PLAN_CAPABILITIES: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        capability_id="fleet-plan-execute",
        action=OperationAction.EXECUTE_PLAN,
        label="Execute Fleet plan",
        description="Execute one fully resolved Fleet plan across selected repositories.",
        runner_kind=CapabilityRunnerKind.FLEET_PLAN,
        safety=CommandSafety.OPAQUE_MUTATION,
        target_kind=CapabilityTargetKind.FLEET_PLAN,
        selector=CapabilityTargetSelector.LEAGUE_TEAM,
        fleet_supported=True,
        schedule_supported=True,
        parallel_safe=False,
        requires_uv_project=False,
        invalidates_all=True,
    ),
)
