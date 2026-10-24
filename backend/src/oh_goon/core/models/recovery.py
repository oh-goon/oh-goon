from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, computed_field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.effect_origin import RepositoryEffectOrigin
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.reconciliation import (
    IndeterminateRepositoryReconciliation,
    PartiallyAppliedRepositoryReconciliation,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import (
    CommandSafety,
    EffectAttemptOwner,
    EffectAttemptStatus,
    OperationAction,
)


class InvariantSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class _InvariantFindingBase(ImmutableModel):
    finding_type: str
    code: str = Field(min_length=1)
    severity: InvariantSeverity
    message: str = Field(min_length=1)


class PlainInvariantFinding(_InvariantFindingBase):
    finding_type: Literal["PLAIN"]


class InspectLeaseFinding(_InvariantFindingBase):
    finding_type: Literal["INSPECT_LEASE"]
    target: str = Field(min_length=1)


class RestartRecoveryFinding(_InvariantFindingBase):
    finding_type: Literal["RESTART_RECOVERY"]
    fleet_run_id: str = Field(min_length=1)


class ReconcileEffectAttemptFinding(_InvariantFindingBase):
    finding_type: Literal["RECONCILE_EFFECT_ATTEMPT"]
    operation_id: str = Field(min_length=1)


class InspectRepositoryEffectFinding(_InvariantFindingBase):
    finding_type: Literal["INSPECT_REPOSITORY_EFFECT"]
    operation_id: str = Field(min_length=1)


InvariantFinding = Annotated[
    PlainInvariantFinding
    | InspectLeaseFinding
    | RestartRecoveryFinding
    | ReconcileEffectAttemptFinding
    | InspectRepositoryEffectFinding,
    Field(discriminator="finding_type"),
]


class InvariantAudit(ImmutableModel):
    durable_revision: int = Field(ge=0)
    journal_revision: int | None = Field(default=None, ge=0)
    findings: tuple[InvariantFinding, ...] = Field(default_factory=tuple)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def healthy(self) -> bool:
        return not any(item.severity is InvariantSeverity.ERROR for item in self.findings)


class _RepositoryEffectQuarantineBase(ImmutableModel):
    """Shared immutable identity for repository effects that block further mutation."""

    operation_id: str = Field(min_length=1)
    repository: RepositoryIdentity
    capability: CapabilitySnapshot
    baseline: RepositoryStateFingerprint
    origin: RepositoryEffectOrigin
    status: EffectAttemptStatus
    record_version: int = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def action(self) -> OperationAction:
        return self.capability.action

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safety(self) -> CommandSafety:
        return self.capability.safety

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source(self) -> EffectAttemptOwner:
        return self.origin.kind


class OutcomeUnknownRepositoryEffectQuarantine(_RepositoryEffectQuarantineBase):
    status: Literal[EffectAttemptStatus.OUTCOME_UNKNOWN]
    note: str = Field(min_length=1)


class PartiallyAppliedRepositoryEffectQuarantine(_RepositoryEffectQuarantineBase):
    status: Literal[EffectAttemptStatus.PARTIALLY_APPLIED]
    reconciliation: PartiallyAppliedRepositoryReconciliation


class IndeterminateRepositoryEffectQuarantine(_RepositoryEffectQuarantineBase):
    status: Literal[EffectAttemptStatus.INDETERMINATE]
    reconciliation: IndeterminateRepositoryReconciliation


RepositoryEffectQuarantine = Annotated[
    OutcomeUnknownRepositoryEffectQuarantine
    | PartiallyAppliedRepositoryEffectQuarantine
    | IndeterminateRepositoryEffectQuarantine,
    Field(discriminator="status"),
]


class _RepairActionBase(ImmutableModel):
    action: str
    target: str = Field(min_length=1)
    destructive: bool
    requires_confirmation: bool
    explanation: str = Field(min_length=1)


class ReconcileEffectAttemptAction(_RepairActionBase):
    action: Literal["reconcile-effect-attempt"]
    destructive: Literal[False] = False
    requires_confirmation: Literal[False] = False
    explanation: str = "Re-run evidence classification; do not replay the external operation"


class InspectRepositoryEffectAction(_RepairActionBase):
    action: Literal["inspect-repository-effect"]
    destructive: Literal[False] = False
    requires_confirmation: Literal[True] = True
    explanation: str = "No automatic repair is safe for partial/indeterminate external effects"


class RestartRecoveryAction(_RepairActionBase):
    action: Literal["restart-recovery"]
    destructive: Literal[False] = False
    requires_confirmation: Literal[False] = False
    explanation: str = (
        "Run the canonical startup reconciliation path; do not replay repository effects directly"
    )


class InspectLeaseAction(_RepairActionBase):
    action: Literal["inspect-lease"]
    destructive: Literal[False] = False
    requires_confirmation: Literal[True] = True
    explanation: str = "An apparently live lease is never force-released automatically"


RepairAction = Annotated[
    ReconcileEffectAttemptAction
    | InspectRepositoryEffectAction
    | RestartRecoveryAction
    | InspectLeaseAction,
    Field(discriminator="action"),
]


class RepairPlan(ImmutableModel):
    findings: tuple[InvariantFinding, ...] = Field(default_factory=tuple)
    actions: tuple[RepairAction, ...] = Field(default_factory=tuple)
