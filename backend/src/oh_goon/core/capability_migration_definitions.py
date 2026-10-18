from __future__ import annotations

from oh_goon.core.capability_invalidation import ADOPT_UV_INVALIDATES
from oh_goon.core.capability_model import (
    CapabilityDefinition,
    CapabilityRunnerKind,
    CapabilityTargetSelector,
)
from oh_goon.core.models.capability import CapabilityTargetKind
from oh_goon.core.vocabulary import CommandSafety, OperationAction

MIGRATION_CAPABILITIES: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        capability_id="adopt-uv",
        action=OperationAction.ADOPT_UV,
        label="Adopt UV",
        description="Migrate a safely supported Python repository to UV and prepare it for review.",
        runner_kind=CapabilityRunnerKind.MIGRATION,
        target_kind=CapabilityTargetKind.MIGRATION,
        selector=CapabilityTargetSelector.MIGRATION_SAFE,
        parallel_safe=False,
        rerun_supported=False,
        requires_uv_project=False,
        invalidates_capabilities=ADOPT_UV_INVALIDATES,
        safety=CommandSafety.WORKTREE_MUTATION,
    ),
)
