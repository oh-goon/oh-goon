from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.effect_identity import RepositoryEffectIdentity
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import ReconciliationOutcome


class _RepositoryReconciliationBase(ImmutableModel):
    """Shared evidence captured by every reconciliation outcome."""

    outcome: ReconciliationOutcome
    reason: str = Field(min_length=1)
    baseline: RepositoryStateFingerprint
    current: RepositoryStateFingerprint
    reconciled_at: AwareDatetime = Field(default_factory=utc_now)

    @property
    def retry_safe(self) -> bool:
        return self.outcome is ReconciliationOutcome.CONFIRMED_NOT_APPLIED

    @property
    def requires_operator(self) -> bool:
        return self.outcome in {
            ReconciliationOutcome.PARTIALLY_APPLIED,
            ReconciliationOutcome.INDETERMINATE,
        }


class ConfirmedAppliedRepositoryReconciliation(_RepositoryReconciliationBase):
    outcome: Literal[ReconciliationOutcome.CONFIRMED_APPLIED]


class ConfirmedNotAppliedRepositoryReconciliation(_RepositoryReconciliationBase):
    outcome: Literal[ReconciliationOutcome.CONFIRMED_NOT_APPLIED]


class PartiallyAppliedRepositoryReconciliation(_RepositoryReconciliationBase):
    outcome: Literal[ReconciliationOutcome.PARTIALLY_APPLIED]


class IndeterminateRepositoryReconciliation(_RepositoryReconciliationBase):
    outcome: Literal[ReconciliationOutcome.INDETERMINATE]


RepositoryReconciliation = Annotated[
    ConfirmedAppliedRepositoryReconciliation
    | ConfirmedNotAppliedRepositoryReconciliation
    | PartiallyAppliedRepositoryReconciliation
    | IndeterminateRepositoryReconciliation,
    Field(discriminator="outcome"),
]

OperatorRequiredRepositoryReconciliation = Annotated[
    PartiallyAppliedRepositoryReconciliation | IndeterminateRepositoryReconciliation,
    Field(discriminator="outcome"),
]


class _RepositoryEffectEvidenceBase(ImmutableModel):
    effect: RepositoryEffectIdentity


class RepositoryEffectReconciliationEvidence(_RepositoryEffectEvidenceBase):
    """Immutable Journal evidence for classification of an uncertain external effect."""

    reconciliation: RepositoryReconciliation

    @property
    def invalidates_prior_results(self) -> bool:
        return not isinstance(
            self.reconciliation,
            ConfirmedNotAppliedRepositoryReconciliation,
        )


class RepositoryEffectAcknowledgementEvidence(_RepositoryEffectEvidenceBase):
    """Immutable operator decision that releases a partial/indeterminate quarantine."""

    reconciliation: OperatorRequiredRepositoryReconciliation
    note: str = Field(min_length=1)
    acknowledged_at: AwareDatetime = Field(default_factory=utc_now)
