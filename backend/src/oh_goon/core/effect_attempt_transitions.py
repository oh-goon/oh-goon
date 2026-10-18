from __future__ import annotations

from datetime import datetime
from pathlib import Path

from oh_goon.core.effect_attempts import (
    require_effect_attempt_transition,
    unresolved_status_for_reconciliation,
)
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.effect_attempt import (
    ClaimedRepositoryEffectAttempt,
    EffectStartedRepositoryEffectAttempt,
    IndeterminateRepositoryEffectAttempt,
    OutcomeUnknownRepositoryEffectAttempt,
    PartiallyAppliedRepositoryEffectAttempt,
    RepositoryEffectAttempt,
)
from oh_goon.core.models.effect_origin import RepositoryEffectOrigin
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.reconciliation import (
    IndeterminateRepositoryReconciliation,
    PartiallyAppliedRepositoryReconciliation,
    RepositoryReconciliation,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.product_identity import new_operation_id
from oh_goon.core.vocabulary import EffectAttemptStatus, EffectBoundaryKind


def begin_effect_attempt(
    repository: Path,
    capability: CapabilitySnapshot,
    *,
    origin: RepositoryEffectOrigin,
    target: OperationTarget | None,
    baseline: RepositoryStateFingerprint,
    effect_boundary: EffectBoundaryKind,
    started_at: datetime,
    operation_id: str | None = None,
) -> ClaimedRepositoryEffectAttempt:
    resolved = repository.expanduser().resolve()
    return ClaimedRepositoryEffectAttempt(
        status=EffectAttemptStatus.CLAIMED,
        operation_id=operation_id or new_operation_id(),
        repository=RepositoryIdentity(path=resolved),
        capability=capability,
        target=target,
        origin=origin,
        baseline=baseline,
        effect_boundary=effect_boundary,
        started_at=started_at,
        updated_at=started_at,
    )


def bind_effect_execution_input(
    current: RepositoryEffectAttempt,
    *,
    execution_input_id: str,
    transitioned_at: datetime,
) -> ClaimedRepositoryEffectAttempt:
    if not isinstance(current, ClaimedRepositoryEffectAttempt):
        raise ValueError("Fleet execution inputs can only be bound while the effect is CLAIMED")
    if current.execution_input_id is not None:
        if current.execution_input_id == execution_input_id:
            return current
        raise ValueError("Fleet effect attempt execution input identity is already bound")
    fields = _transition_fields(current, transitioned_at=transitioned_at)
    fields["execution_input_id"] = execution_input_id
    return ClaimedRepositoryEffectAttempt(
        status=EffectAttemptStatus.CLAIMED,
        **fields,
    )


def start_effect_attempt(
    current: RepositoryEffectAttempt,
    *,
    transitioned_at: datetime,
) -> EffectStartedRepositoryEffectAttempt:
    if isinstance(current, EffectStartedRepositoryEffectAttempt):
        return current
    require_effect_attempt_transition(current.status, EffectAttemptStatus.EFFECT_STARTED)
    return EffectStartedRepositoryEffectAttempt(
        status=EffectAttemptStatus.EFFECT_STARTED,
        **_transition_fields(current, transitioned_at=transitioned_at),
    )


def mark_effect_unknown(
    current: RepositoryEffectAttempt,
    *,
    note: str,
    transitioned_at: datetime,
) -> OutcomeUnknownRepositoryEffectAttempt:
    if isinstance(current, OutcomeUnknownRepositoryEffectAttempt):
        return current
    require_effect_attempt_transition(current.status, EffectAttemptStatus.OUTCOME_UNKNOWN)
    return OutcomeUnknownRepositoryEffectAttempt(
        status=EffectAttemptStatus.OUTCOME_UNKNOWN,
        **_transition_fields(current, transitioned_at=transitioned_at),
        note=note,
    )


def apply_effect_reconciliation(
    current: RepositoryEffectAttempt,
    reconciliation: RepositoryReconciliation,
    *,
    transitioned_at: datetime,
) -> RepositoryEffectAttempt | None:
    """Return remaining uncertainty; terminal reconciliation is represented by Journal evidence."""
    target = unresolved_status_for_reconciliation(reconciliation.outcome)
    if target is None:
        return None
    require_effect_attempt_transition(current.status, target, allow_same=True)
    transition_fields = _transition_fields(current, transitioned_at=transitioned_at)
    if target is EffectAttemptStatus.PARTIALLY_APPLIED:
        if not isinstance(reconciliation, PartiallyAppliedRepositoryReconciliation):
            raise ValueError("PARTIALLY_APPLIED status requires matching reconciliation evidence")
        return PartiallyAppliedRepositoryEffectAttempt(
            status=EffectAttemptStatus.PARTIALLY_APPLIED,
            **transition_fields,
            reconciliation=reconciliation,
        )
    if not isinstance(reconciliation, IndeterminateRepositoryReconciliation):
        raise ValueError("INDETERMINATE status requires matching reconciliation evidence")
    return IndeterminateRepositoryEffectAttempt(
        status=EffectAttemptStatus.INDETERMINATE,
        **transition_fields,
        reconciliation=reconciliation,
    )


def _transition_fields(
    current: RepositoryEffectAttempt,
    *,
    transitioned_at: datetime,
) -> dict[str, object]:
    return {
        "operation_id": current.operation_id,
        "repository": current.repository,
        "capability": current.capability,
        "target": current.target,
        "origin": current.origin,
        "baseline": current.baseline,
        "effect_boundary": current.effect_boundary,
        "execution_input_id": current.execution_input_id,
        "record_version": current.record_version,
        "started_at": current.started_at,
        "updated_at": transitioned_at,
    }
