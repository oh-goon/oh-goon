from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.effect_attempts import quarantines_repository
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.effect_identity import RepositoryEffectIdentity
from oh_goon.core.models.effect_origin import (
    FleetEffectOrigin,
    RepositoryEffectOrigin,
)
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.reconciliation import (
    IndeterminateRepositoryReconciliation,
    PartiallyAppliedRepositoryReconciliation,
    RepositoryReconciliation,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.vocabulary import (
    CommandSafety,
    EffectAttemptOwner,
    EffectAttemptStatus,
    EffectBoundaryKind,
    OperationAction,
)


class _RepositoryEffectAttemptBase(ImmutableModel):
    """Shared immutable identity/provenance for unresolved external-effect coordination."""

    operation_id: str = Field(min_length=1)
    repository: RepositoryIdentity
    capability: CapabilitySnapshot
    target: OperationTarget | None = None
    origin: RepositoryEffectOrigin
    status: EffectAttemptStatus
    effect_boundary: EffectBoundaryKind
    execution_input_id: str | None = Field(default=None, min_length=1)
    baseline: RepositoryStateFingerprint
    record_version: int = Field(default=0, ge=0)
    started_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_common_lineage(self) -> _RepositoryEffectAttemptBase:
        if not self.capability.matches_intent(self.capability.action, self.target):
            raise ValueError("Effect attempt capability snapshot must match its target kind")
        if self.updated_at < self.started_at:
            raise ValueError("Effect attempt updated_at cannot precede started_at")
        return self

    @property
    def identity(self) -> RepositoryEffectIdentity:
        return RepositoryEffectIdentity(
            operation_id=self.operation_id,
            repository=self.repository,
            capability=self.capability,
            target=self.target,
            origin=self.origin,
        )

    @property
    def action(self) -> OperationAction:
        return self.capability.action

    @property
    def safety(self) -> CommandSafety:
        return self.capability.safety

    @property
    def source(self) -> EffectAttemptOwner:
        return self.origin.kind

    @property
    def fleet_run_id(self) -> str | None:
        if isinstance(self.origin, FleetEffectOrigin):
            return self.origin.fleet_run_id
        return None

    @property
    def quarantines_repository(self) -> bool:
        return self.effect_boundary is EffectBoundaryKind.EXTERNAL and quarantines_repository(
            self.status, self.safety
        )


class ClaimedRepositoryEffectAttempt(_RepositoryEffectAttemptBase):
    status: Literal[EffectAttemptStatus.CLAIMED]


class EffectStartedRepositoryEffectAttempt(_RepositoryEffectAttemptBase):
    status: Literal[EffectAttemptStatus.EFFECT_STARTED]


class OutcomeUnknownRepositoryEffectAttempt(_RepositoryEffectAttemptBase):
    status: Literal[EffectAttemptStatus.OUTCOME_UNKNOWN]
    note: str = Field(min_length=1)


class PartiallyAppliedRepositoryEffectAttempt(_RepositoryEffectAttemptBase):
    status: Literal[EffectAttemptStatus.PARTIALLY_APPLIED]
    reconciliation: PartiallyAppliedRepositoryReconciliation


class IndeterminateRepositoryEffectAttempt(_RepositoryEffectAttemptBase):
    status: Literal[EffectAttemptStatus.INDETERMINATE]
    reconciliation: IndeterminateRepositoryReconciliation


RepositoryEffectAttempt = Annotated[
    ClaimedRepositoryEffectAttempt
    | EffectStartedRepositoryEffectAttempt
    | OutcomeUnknownRepositoryEffectAttempt
    | PartiallyAppliedRepositoryEffectAttempt
    | IndeterminateRepositoryEffectAttempt,
    Field(discriminator="status"),
]


def effect_attempt_reconciliation(
    attempt: RepositoryEffectAttempt,
) -> RepositoryReconciliation | None:
    if isinstance(
        attempt,
        (PartiallyAppliedRepositoryEffectAttempt, IndeterminateRepositoryEffectAttempt),
    ):
        return attempt.reconciliation
    return None
