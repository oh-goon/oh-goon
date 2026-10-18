from __future__ import annotations

from types import MappingProxyType

from oh_goon.core.errors import StateConflict
from oh_goon.core.vocabulary import CommandSafety, EffectAttemptStatus, ReconciliationOutcome

LIVE_OWNED_EFFECT_STATUSES = frozenset(
    {
        EffectAttemptStatus.CLAIMED,
        EffectAttemptStatus.EFFECT_STARTED,
    }
)
AUTOMATIC_RECOVERY_EFFECT_STATUSES = LIVE_OWNED_EFFECT_STATUSES | {
    EffectAttemptStatus.OUTCOME_UNKNOWN
}
RECONCILABLE_EFFECT_STATUSES = frozenset(
    {
        EffectAttemptStatus.OUTCOME_UNKNOWN,
        EffectAttemptStatus.PARTIALLY_APPLIED,
        EffectAttemptStatus.INDETERMINATE,
    }
)
OPERATOR_REQUIRED_EFFECT_STATUSES = frozenset(
    {
        EffectAttemptStatus.PARTIALLY_APPLIED,
        EffectAttemptStatus.INDETERMINATE,
    }
)
EXTERNAL_OUTCOME_UNCERTAIN_STATUSES = RECONCILABLE_EFFECT_STATUSES | {
    EffectAttemptStatus.EFFECT_STARTED
}

_ALLOWED_TRANSITIONS = MappingProxyType(
    {
        EffectAttemptStatus.CLAIMED: frozenset({EffectAttemptStatus.EFFECT_STARTED}),
        EffectAttemptStatus.EFFECT_STARTED: frozenset({EffectAttemptStatus.OUTCOME_UNKNOWN}),
        EffectAttemptStatus.OUTCOME_UNKNOWN: frozenset(
            {
                EffectAttemptStatus.PARTIALLY_APPLIED,
                EffectAttemptStatus.INDETERMINATE,
            }
        ),
        EffectAttemptStatus.PARTIALLY_APPLIED: frozenset(
            {
                EffectAttemptStatus.PARTIALLY_APPLIED,
                EffectAttemptStatus.INDETERMINATE,
            }
        ),
        EffectAttemptStatus.INDETERMINATE: frozenset(
            {
                EffectAttemptStatus.PARTIALLY_APPLIED,
                EffectAttemptStatus.INDETERMINATE,
            }
        ),
    }
)


def require_effect_attempt_transition(
    current: EffectAttemptStatus,
    target: EffectAttemptStatus,
    *,
    allow_same: bool = False,
) -> None:
    """Validate unresolved repository-effect coordination transitions."""
    if allow_same and current is target:
        return
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise StateConflict(
            f"Effect attempt cannot transition from {current.value} to {target.value}"
        )


def unresolved_status_for_reconciliation(
    outcome: ReconciliationOutcome,
) -> EffectAttemptStatus | None:
    """Return the remaining coordination state, or None when evidence resolves the attempt."""
    if outcome is ReconciliationOutcome.PARTIALLY_APPLIED:
        return EffectAttemptStatus.PARTIALLY_APPLIED
    if outcome is ReconciliationOutcome.INDETERMINATE:
        return EffectAttemptStatus.INDETERMINATE
    return None


def quarantines_repository(status: EffectAttemptStatus, safety: CommandSafety) -> bool:
    return safety is not CommandSafety.READ_ONLY and status in RECONCILABLE_EFFECT_STATUSES
