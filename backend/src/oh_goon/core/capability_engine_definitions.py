from __future__ import annotations

from oh_goon.core.capability_invalidation import (
    INIT_INVALIDATES,
    REVIEW_INVALIDATES,
    UPDATE_INVALIDATES,
)
from oh_goon.core.capability_model import (
    CapabilityDefinition,
    CapabilityRunnerKind,
    CapabilityTargetSelector,
)
from oh_goon.core.models.capability import (
    CapabilityRequirement,
    CapabilityRequirementCondition,
    CapabilityRequirementTarget,
    CapabilityTargetKind,
)
from oh_goon.core.vocabulary import CommandSafety, OperationAction

_READY = CapabilityRequirement(
    capability_id="initialize",
    target_binding=CapabilityRequirementTarget.NONE,
    condition=CapabilityRequirementCondition.READY,
)


def _engine(
    capability_id: str,
    action: OperationAction,
    label: str,
    description: str,
    *,
    target_kind: CapabilityTargetKind = CapabilityTargetKind.NONE,
    fleet_supported: bool = True,
    direct_supported: bool = False,
    rerun_supported: bool = True,
    parallel_safe: bool = False,
    selector: CapabilityTargetSelector = CapabilityTargetSelector.INCLUDED,
    requirements: tuple[CapabilityRequirement, ...] = (),
    invalidates_capabilities: frozenset[str] = frozenset(),
    invalidates_all: bool = False,
    safety: CommandSafety,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id=capability_id,
        action=action,
        label=label,
        description=description,
        runner_kind=CapabilityRunnerKind.ENGINE,
        target_kind=target_kind,
        fleet_supported=fleet_supported,
        schedule_supported=fleet_supported and safety is CommandSafety.READ_ONLY,
        direct_supported=direct_supported,
        rerun_supported=rerun_supported,
        parallel_safe=parallel_safe,
        selector=selector,
        requirements=requirements,
        invalidates_capabilities=invalidates_capabilities,
        invalidates_all=invalidates_all,
        safety=safety,
    )


ENGINE_CAPABILITIES: tuple[CapabilityDefinition, ...] = (
    _engine(
        "status",
        OperationAction.STATUS,
        "Status",
        "Inspect repository state.",
        fleet_supported=False,
        parallel_safe=True,
        safety=CommandSafety.READ_ONLY,
    ),
    _engine(
        "initialize",
        OperationAction.INIT,
        "Initialize",
        "Establish current repository readiness.",
        parallel_safe=True,
        direct_supported=True,
        invalidates_capabilities=INIT_INVALIDATES,
        safety=CommandSafety.READ_ONLY,
    ),
    _engine(
        "plan",
        OperationAction.PLAN,
        "Plan updates",
        "Plan dependency updates without changing the repository.",
        parallel_safe=True,
        direct_supported=True,
        requirements=(_READY,),
        safety=CommandSafety.READ_ONLY,
    ),
    _engine(
        "update",
        OperationAction.UPDATE,
        "Update",
        "Apply dependency updates and validate the result.",
        requirements=(_READY,),
        direct_supported=True,
        invalidates_capabilities=UPDATE_INVALIDATES,
        safety=CommandSafety.WORKTREE_MUTATION,
    ),
    _engine(
        "verify",
        OperationAction.VERIFY,
        "Verify",
        "Verify the current repository state.",
        direct_supported=True,
        safety=CommandSafety.ENVIRONMENT_MUTATION,
    ),
    _engine(
        "prepare-review",
        OperationAction.PREPARE_REVIEW,
        "Prepare review",
        "Create a local review branch and commit for verified dependency changes.",
        selector=CapabilityTargetSelector.REVIEW_READY,
        rerun_supported=False,
        invalidates_capabilities=REVIEW_INVALIDATES,
        safety=CommandSafety.WORKTREE_MUTATION,
    ),
)
