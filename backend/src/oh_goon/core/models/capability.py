from __future__ import annotations

from enum import StrEnum
from typing import cast

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary import CommandSafety, OperationAction, UvRecipeId


class CapabilityTargetKind(StrEnum):
    NONE = "NONE"
    RECIPE = "RECIPE"
    MIGRATION = "MIGRATION"
    FLEET_PLAN = "FLEET_PLAN"


class CapabilityRequirementTarget(StrEnum):
    NONE = "NONE"
    SAME = "SAME"


class CapabilityRequirementCondition(StrEnum):
    READY = "READY"
    SUCCESSFUL = "SUCCESSFUL"


class CapabilityRequirement(ImmutableModel):
    """Explicit prerequisite capability binding for new-work admission."""

    capability_id: str = Field(min_length=1)
    target_binding: CapabilityRequirementTarget
    condition: CapabilityRequirementCondition


class CapabilitySnapshot(ImmutableModel):
    """Durable capability policy bound to an admitted/executed operation.

    Historical Journal/Schedule replay must use this snapshot rather than re-resolving
    mutable current registry policy.
    """

    capability_id: str = Field(min_length=1)
    policy_fingerprint: str = Field(min_length=1)
    action: OperationAction
    target_kind: CapabilityTargetKind
    safety: CommandSafety
    invalidates_capabilities: tuple[str, ...] = ()
    invalidates_all: bool = False

    def matches_intent(self, action: OperationAction, target: object | None) -> bool:
        return self.action is action and self.target_kind is capability_target_kind(target)


def capability_target_kind(target: object | None) -> CapabilityTargetKind:
    if target is None:
        return CapabilityTargetKind.NONE
    kind = getattr(target, "kind", None)
    mapping = {
        "recipe": CapabilityTargetKind.RECIPE,
        "migration": CapabilityTargetKind.MIGRATION,
        "fleet_plan": CapabilityTargetKind.FLEET_PLAN,
    }
    try:
        return mapping[cast(str, kind)]
    except KeyError as exc:
        raise TypeError(f"Unknown operation target kind: {kind!r}") from exc


class CapabilityInfo(ImmutableModel):
    """Public projection of the canonical capability definition."""

    capability_id: str
    action: OperationAction
    label: str
    description: str
    target_kind: CapabilityTargetKind
    fleet_supported: bool
    schedule_supported: bool
    direct_supported: bool
    rerun_supported: bool
    parallel_safe: bool
    requires_uv_project: bool
    requirements: tuple[CapabilityRequirement, ...] = Field(default_factory=tuple)
    safety: CommandSafety
    recipe: UvRecipeId | None = None
    user_configurable: bool = False
    produces_security_findings: bool = False
    produces_sbom: bool = False
