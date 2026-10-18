from __future__ import annotations

from pydantic import Field, computed_field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.explanation import AutoFixAction, OperationPhase, ReasonCode
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import RiskLevel


class AttentionRepository(ImmutableModel):
    repository: RepositoryIdentity
    reason_code: ReasonCode
    phase: OperationPhase
    risk: RiskLevel
    can_auto_fix: bool
    auto_fix_action: AutoFixAction | None = None


class AttentionGroup(ImmutableModel):
    reason_code: ReasonCode
    phase: OperationPhase
    risk: RiskLevel
    repositories: tuple[AttentionRepository, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_projection(self) -> AttentionGroup:
        if not self.repositories:
            raise ValueError("Attention groups cannot be empty")
        if any(item.reason_code is not self.reason_code for item in self.repositories):
            raise ValueError("Attention group repositories must share one reason code")
        if any(
            item.phase is not self.phase or item.risk is not self.risk for item in self.repositories
        ):
            raise ValueError("Attention group semantics must match repository items")
        return self

    @computed_field
    @property
    def can_auto_fix(self) -> bool:
        auto_actions = {item.auto_fix_action for item in self.repositories if item.can_auto_fix}
        return len(auto_actions) == 1 and all(item.can_auto_fix for item in self.repositories)

    @computed_field
    @property
    def auto_fix_action(self) -> AutoFixAction | None:
        if not self.can_auto_fix:
            return None
        return next(item.auto_fix_action for item in self.repositories)


class FleetAttention(ImmutableModel):
    healthy: int = Field(default=0, ge=0)
    groups: tuple[AttentionGroup, ...] = Field(default_factory=tuple)

    @computed_field
    @property
    def action_required(self) -> int:
        return sum(len(group.repositories) for group in self.groups)
